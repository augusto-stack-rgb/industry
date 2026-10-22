"""Marcas e patentes depositadas no INPI, pelos dados abertos (atualizados toda
semana em dadosabertos.inpi.gov.br). Marca ou patente nova costuma ser linha de
produto nova: mais catalogo e mais documentacao tecnica, o que puxa o RAG.

Os arquivos sao grandes (os de marcas passam de 1 GB), entao sao lidos em
streaming e so as linhas das empresas do universo ficam, sem gravar em disco. O
arquivo de classes (7,6 GB) fica de fora: o nome da marca ja diz a linha.
Os depositantes trazem o CNPJ, entao o casamento e exato.

  marca_depositada    marca depositada nos ultimos 12 meses (um gancho por marca
                      e dia, juntando os pedidos de cada classe)
  patente_depositada  pedido de patente ou modelo de utilidade depositado nos
                      ultimos 24 meses (a publicacao sai ~18 meses depois do deposito)
"""
import csv
from datetime import date, timedelta

import httpx

from . import comum

FONTE = 'inpi'
BASE = 'https://dadosabertos.inpi.gov.br/download/'
BUSCA_MARCA = 'https://busca.inpi.gov.br/pePI/servlet/MarcasServletController?Action=searchMarca&tipoPesquisa=BY_NUM_PROC&NumPedido={}'
BUSCA_PATENTE = 'https://busca.inpi.gov.br/pePI/servlet/PatenteServletController?Action=searchPatente&tipoPesquisa=BY_NUM_PROC&NumPedido={}'


def _linhas(cli, arquivo, filtro=None):
    """Linhas do CSV como dicionarios. `filtro(linha_crua)` descarta antes do
    parse, o que deixa os arquivos de varios GB rapidos de atravessar."""
    with cli.stream('GET', BASE + arquivo) as r:
        r.raise_for_status()
        it = r.iter_lines()
        cab = next(csv.reader([next(it)]))
        for linha in it:
            if filtro and not filtro(linha):
                continue
            try:
                valores = next(csv.reader([linha]))
            except (csv.Error, StopIteration):
                continue
            if len(valores) == len(cab):
                yield dict(zip(cab, valores))


def _num(linha):
    p = linha.split(',', 2)
    return p[1] if len(p) > 1 else ''


def coletar(supabase, log) -> dict:
    emps = {e['cnpj']: e for e in comum.empresas(supabase)}
    hoje = date.today()
    ganchos = []
    with httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as cli:
        # marcas: depositantes -> processos das empresas -> datas
        tem_cnpj = lambda l: any(c in l for c in emps)   # noqa: E731
        marcas = {d['numero_inpi']: d['cnpj_cpf_titular']
                  for d in _linhas(cli, 'marcas/MARCAS_DEPOSITANTES.csv', tem_cnpj)
                  if d['cnpj_cpf_titular'] in emps}
        log.info(f'{len(marcas)} processos de marca das empresas')
        recentes = {}
        for d in _linhas(cli, 'marcas/MARCAS_DADOS_BIBLIOGRAFICOS.csv', lambda l: _num(l) in marcas):
            dep = d['data_deposito']
            if dep and date.fromisoformat(dep) >= hoje - timedelta(days=365):
                recentes[d['numero_inpi']] = d
        # cada classe e um pedido separado: agrupa a mesma marca no mesmo dia
        grupos = {}
        for n, d in recentes.items():
            nome = d['elemento_nominativo'] or 'figurativa'
            grupos.setdefault((marcas[n], nome.upper(), d['data_deposito']), []).append((n, d))
        for (c, _, dep), itens in grupos.items():
            n, d = itens[0]
            nome = d['elemento_nominativo'] or 'figurativa'
            ganchos.append(comum.gancho(
                c, FONTE, 'marca_depositada', 'rag', dep,
                f'Depositou a marca {nome} em {date.fromisoformat(dep).strftime("%d/%m/%Y")}'
                + (f', em {len(itens)} classes de produto ou serviço' if len(itens) > 1 else ''),
                BUSCA_MARCA.format(n), f'{c}|{nome.upper()}|{dep}', processos=[x for x, _ in itens],
                natureza=d['descricao_natureza']))

        # patentes: depositantes -> processos -> datas -> titulos
        patentes = {d['numero_inpi']: d['cgccpfdepositante']
                    for d in _linhas(cli, 'patentes/PATENTES_DEPOSITANTES.csv', tem_cnpj)
                    if d['cgccpfdepositante'] in emps}
        log.info(f'{len(patentes)} processos de patente das empresas')
        pat_recentes = {}
        for d in _linhas(cli, 'patentes/PATENTES_DADOS_BIBLIOGRAFICOS.csv', lambda l: _num(l) in patentes):
            dep = d['data_deposito']
            if dep and date.fromisoformat(dep) >= hoje - timedelta(days=730):
                pat_recentes[d['numero_inpi']] = dep
        titulos = {}
        if pat_recentes:
            for d in _linhas(cli, 'patentes/PATENTES_CONTEUDO.csv', lambda l: _num(l) in pat_recentes):
                titulos[d['numero_inpi']] = d['titulo']
        for n, dep in pat_recentes.items():
            titulo = (titulos.get(n) or '').strip().capitalize()
            ganchos.append(comum.gancho(
                patentes[n], FONTE, 'patente_depositada', 'rag', dep,
                f'Depositou pedido de patente em {date.fromisoformat(dep).strftime("%d/%m/%Y")}'
                + (f': {titulo}' if titulo and titulo.upper() != 'SEM TITULO' else ''),
                BUSCA_PATENTE.format(n), n))
    novos = comum.gravar(supabase, ganchos)
    log.info(f'{len(recentes)} marcas e {len(pat_recentes)} patentes recentes, {novos} ganchos novos')
    return {'marcas_das_empresas': len(marcas), 'marcas_recentes': len(recentes),
            'patentes_das_empresas': len(patentes), 'patentes_recentes': len(pat_recentes),
            'ganchos': len(ganchos), 'novos': novos}
