"""Filiais das empresas, lidas dos zips mensais de Estabelecimentos da Receita.

A base local (scripts/receita_federal) so guarda matrizes, e nenhuma API publica
lista as filiais de um CNPJ. Entao, uma vez por snapshot mensal da RF, os 10
zips de Estabelecimentos passam um a um pelo HD (baixa, filtra pelos CNPJs
basicos do universo, apaga) e sobra so um parquet pequeno com as filiais.

  filial_nova   filial com inicio de atividade nos ultimos 12 meses, ou que nao
                existia no snapshot anterior (dashboard: operacao em mais de um lugar)
"""
import os
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

import duckdb

from . import comum

FONTE = 'filiais'
RF = Path(__file__).resolve().parents[3] / 'scripts' / 'receita_federal'
PASTA = Path(os.getenv('GANCHOS_DIR', 'D:/receita_federal/ganchos')) / 'filiais'


def _rf():
    if str(RF) not in sys.path:
        sys.path.insert(0, str(RF))
    import baixar   # noqa: E402
    import carregar   # noqa: E402
    from coletar_sul import ler_csv, para_utf8   # noqa: E402
    return baixar, carregar, ler_csv, para_utf8


def extrair(basicos: set[str], log) -> tuple[str, Path]:
    """Parquet com as filiais do snapshot mais recente (retomavel por zip)."""
    baixar, _, ler_csv, para_utf8 = _rf()
    mes = baixar.snapshots()[-1]
    destino = PASTA / mes
    final = destino / 'filiais.parquet'
    if final.exists():
        return mes, final
    destino.mkdir(parents=True, exist_ok=True)
    arquivos = dict(n for n in baixar.listar(baixar.RAIZ + mes + '/') if n[0].startswith('Estabelecimentos'))
    con = duckdb.connect()
    con.execute('create temp table alvo (cnpj_basico varchar)')
    con.executemany('insert into alvo values (?)', [[b] for b in sorted(basicos)])
    for nome in sorted(arquivos):
        parte = destino / f'{nome[:-4]}.parquet'
        if parte.exists():
            continue
        zipado, csv = destino / nome, destino / f'{nome[:-4]}.utf8.csv'
        log.info(f'{mes} {nome}: {arquivos[nome] / 1e6:.0f} MB')
        baixar.baixar(baixar.RAIZ + mes + '/' + nome, zipado, arquivos[nome])
        with zipfile.ZipFile(zipado) as z, z.open(z.infolist()[0]) as src:
            para_utf8(src, csv)
        zipado.unlink()
        con.execute(f"copy (select * from {ler_csv(csv, 'estabelecimentos')} "
                    f"where matriz_filial = '2' and cnpj_basico in (select cnpj_basico from alvo)) "
                    f"to '{parte.as_posix()}' (format parquet)")
        csv.unlink()
    con.execute(f"copy (select * from read_parquet('{destino.as_posix()}/Estabelecimentos*.parquet')) "
                f"to '{final.as_posix()}' (format parquet)")
    for p in destino.glob('Estabelecimentos*.parquet'):
        p.unlink()
    return mes, final


def coletar(supabase, log) -> dict:
    emps = comum.empresas(supabase)
    por_basico = {e['cnpj'][:8]: e for e in emps}
    mes, arq = extrair(set(por_basico), log)
    antes = comum.estados(supabase, FONTE)
    hoje = date.today()
    linhas = duckdb.sql(f"""select cnpj_basico || cnpj_ordem || cnpj_dv, cnpj_basico, nome_fantasia,
                                   situacao_cadastral, data_inicio_atividade, municipio, uf
                              from read_parquet('{arq.as_posix()}')""").fetchall()
    muns = dict(duckdb.connect('D:/receita_federal/rf_sul.duckdb', read_only=True)
                .execute('select codigo, descricao from municipios').fetchall())
    ganchos, estado = [], {}
    for cnpj_f, basico, fantasia, sit, inicio, mun, uf in linhas:
        e = por_basico[basico]
        ativa = sit == '02'
        estado.setdefault(e['cnpj'], {'filiais': [], 'ativas': 0, 'mes': mes})
        estado[e['cnpj']]['filiais'].append(cnpj_f)
        estado[e['cnpj']]['ativas'] += ativa
        ini = date(int(inicio[:4]), int(inicio[4:6]), int(inicio[6:8])) if inicio and len(inicio) == 8 else None
        ja_existia = cnpj_f in set((antes.get(e['cnpj']) or {}).get('filiais') or [])
        recente = ini and ini >= hoje - timedelta(days=365)
        nova = antes.get(e['cnpj']) is not None and not ja_existia
        if ativa and (recente or nova):
            local = f'{muns.get(mun, mun)} {uf}'.strip()
            ganchos.append(comum.gancho(
                e['cnpj'], FONTE, 'filial_nova', 'dashboard', ini or hoje,
                f'Abriu filial em {local}' + (f' em {ini.strftime("%d/%m/%Y")}' if ini else ''),
                f'https://minhareceita.org/{cnpj_f}', cnpj_f, fantasia=fantasia))
    for c in {e['cnpj'] for e in emps} - set(estado):
        estado[c] = {'filiais': [], 'ativas': 0, 'mes': mes}
    comum.salvar_estados(supabase, FONTE, estado)
    novos = comum.gravar(supabase, ganchos)
    com = sum(1 for x in estado.values() if x['ativas'])
    log.info(f'snapshot {mes}: {len(linhas)} filiais de {com} empresas, {len(ganchos)} ganchos, {novos} novos')
    return {'snapshot': mes, 'filiais': len(linhas), 'empresas_com_filial': com, 'ganchos': len(ganchos), 'novos': novos}
