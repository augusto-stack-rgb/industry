"""Financiamentos do BNDES, pelos dados abertos de operacoes de financiamento.

Dois arquivos, baixados para o HD so quando o BNDES publica versao nova:
  nao automaticas   ~20 MB, CNPJ completo e descricao do projeto
  automaticas       ~1,2 GB, feitas por bancos repassadores (BRDE, Badesc,
                    Sicredi...); o CNPJ vem mascarado (**.*16.560/0001-**),
                    entao a empresa e reconhecida pelos digitos visiveis, pelo
                    municipio e pelo nome do cliente
Vira gancho a operacao contratada nos ultimos 24 meses (financiamento_bndes).
Finep, BRDE, Fapesc e Fapergs nao publicam operacoes por CNPJ em dados abertos.
"""
import os
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import duckdb
import httpx

from . import comum

FONTE = 'bndes'
PASTA = Path(os.getenv('GANCHOS_DIR', 'D:/receita_federal/ganchos')) / 'bndes'
BASE = 'https://dadosabertos.bndes.gov.br/dataset/10e21ad1-568e-45e5-a8af-43f2c05ef1a2/resource/'
ARQUIVOS = {
    'nao_automaticas': BASE + '6f56b78c-510f-44b6-8274-78a5b7e931f4/download/operacoes-financiamento-operacoes-nao-automaticas.csv',
    'indiretas_automaticas': BASE + '612faa0b-b6be-4b2c-9317-da5dc2c0b901/download/operacoes-financiamento-operacoes-indiretas-automaticas.csv',
}
JANELA_DIAS = 730


def atualizar(log) -> dict[str, Path]:
    """Baixa o que o BNDES atualizou e converte de latin-1 para UTF-8."""
    PASTA.mkdir(parents=True, exist_ok=True)
    saida = {}
    with httpx.Client(timeout=600, follow_redirects=True) as cli:
        for nome, url in ARQUIVOS.items():
            utf8 = PASTA / f'{nome}.utf8.csv'
            remoto = parsedate_to_datetime(cli.head(url).headers['last-modified']).timestamp()
            if not utf8.exists() or utf8.stat().st_mtime < remoto:
                bruto = PASTA / f'{nome}.csv'
                if not bruto.exists() or bruto.stat().st_mtime < remoto:
                    log.info(f'baixando {nome}')
                    with cli.stream('GET', url) as r, open(bruto, 'wb') as f:
                        for bloco in r.iter_bytes(1 << 22):
                            f.write(bloco)
                with open(bruto, 'rb') as src, open(utf8, 'wb') as dst:
                    while bloco := src.read(1 << 22):
                        dst.write(bloco.decode('latin-1').encode('utf-8'))
                bruto.unlink()
            saida[nome] = utf8
    return saida


def _ler(con, arq, colunas, desde, ufs):
    return con.execute(f"""select {colunas} from read_csv('{arq.as_posix()}', delim=';', header=true,
                                   all_varchar=true, ignore_errors=true)
                            where data_da_contratacao >= ? and trim(uf) in ({', '.join('?' * len(ufs))})""",
                       [desde, *ufs]).fetchall()


def _casa_mascara(mascarado: str, cnpj: str) -> bool:
    fmt = f'{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}'
    return len(mascarado) == len(fmt) and all(m in '*' or m == f for m, f in zip(mascarado, fmt))


def _brl(v: float) -> str:
    return f'R$ {v:,.0f}'.replace(',', '.')


def _dia(d: str) -> str:
    return f'{d[8:10]}/{d[5:7]}/{d[:4]}' if d and len(d) >= 10 else d or ''


def _valor(v):
    try:
        return float((v or '0').replace('.', '').replace(',', '.'))
    except ValueError:
        return 0.0


def coletar(supabase, log) -> dict:
    arqs = atualizar(log)
    emps = comum.empresas(supabase)
    por_cnpj = {e['cnpj']: e for e in emps}
    ufs = sorted({e['uf'] for e in emps if e['uf']})
    desde = (date.today() - timedelta(days=JANELA_DIAS)).isoformat()
    con = duckdb.connect()
    ganchos = []

    for cliente, cnpj, desc, mun, data, valor, produto, num in _ler(
            con, arqs['nao_automaticas'],
            'cliente, cnpj, descricao_do_projeto, municipio, data_da_contratacao, valor_contratado_reais, produto, '
            'numero_do_contrato', desde, ufs):
        c = ''.join(ch for ch in cnpj or '' if ch.isdigit())
        if c in por_cnpj:
            ganchos.append(comum.gancho(
                c, FONTE, 'financiamento_bndes', 'ambos', data,
                f'Financiamento BNDES de {_brl(_valor(valor))} contratado em {_dia(data)} para '
                f'{(desc or produto or "").strip().lower()[:160]}', ARQUIVOS['nao_automaticas'], f'{c}|{num}|{data}',
                produto=produto))

    por_mun = {}
    for e in emps:
        por_mun.setdefault(comum.norm(e['municipio']), []).append(e)
    for cliente, mascarado, mun, data, valor, produto, inst in _ler(
            con, arqs['indiretas_automaticas'],
            'cliente, cpf_cnpj, municipio, data_da_contratacao, valor_da_operacao_em_reais, produto, '
            'instituicao_financeira_credenciada', desde, ufs):
        for e in por_mun.get(comum.norm(mun), []):
            if _casa_mascara((mascarado or '').strip(), e['cnpj']) and \
                    comum.mesma_empresa(cliente, e['razao_social'], e['nome_fantasia']):
                ganchos.append(comum.gancho(
                    e['cnpj'], FONTE, 'financiamento_bndes', 'ambos', data,
                    f'Financiamento BNDES de {_brl(_valor(valor))} pelo {(inst or "agente").strip()} em {_dia(data)}, '
                    f'linha {(produto or "").strip()}',
                    ARQUIVOS['indiretas_automaticas'], f'{e["cnpj"]}|{data}|{valor}|{produto}', produto=produto))
    novos = comum.gravar(supabase, ganchos)
    log.info(f'{len(ganchos)} operacoes casadas com {len({g["cnpj"] for g in ganchos})} empresas, {novos} novas')
    return {'operacoes': len(ganchos), 'empresas': len({g['cnpj'] for g in ganchos}), 'novos': novos}
