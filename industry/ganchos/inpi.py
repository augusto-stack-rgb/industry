"""Marcas e patentes depositadas no INPI, pelos dados abertos (atualizados toda
semana em dadosabertos.inpi.gov.br). Marca ou patente nova costuma ser linha de
produto nova: mais catalogo e mais documentacao tecnica, o que puxa o RAG.

Os arquivos sao grandes (o de classes das marcas passa de 7 GB), entao sao lidos
em streaming e so as linhas das empresas do universo ficam, sem gravar em disco.
Os depositantes trazem o CNPJ, entao o casamento e exato.

  marca_depositada    pedido de marca depositado nos ultimos 12 meses
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
        # marcas: depositantes -> processos das empresas -> datas -> classes
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
        classes = {}
        if recentes:
            for d in _linhas(cli, 'marcas/MARCAS_CLASSIFICACOES_NICE.csv', lambda l: _num(l) in recentes):
                classes.setdefault(d['numero_inpi'], set()).add(
                    (d['classe_nice'].strip(), (d['especificacao'] or '').strip()[:140]))
        for n, d in recentes.items():
            c = marcas[n]
            espec = '; '.join(f'classe {cl}' + (f', {es.lower()}' if es else '') for cl, es in sorted(classes.get(n, ())))
            ganchos.append(comum.gancho(
                c, FONTE, 'marca_depositada', 'rag', d['data_deposito'],
                f'Depositou a marca {d["elemento_nominativo"] or "figurativa"} em '
                f'{date.fromisoformat(d["data_deposito"]).strftime("%d/%m/%Y")}' + (f' ({espec})' if espec else ''),
                BUSCA_MARCA.format(n), n, natureza=d['descricao_natureza'], situacao=d['descricao_situacao']))

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
