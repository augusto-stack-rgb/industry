"""Vagas abertas que revelam uma dor ligada a um dos pacotes.

Duas origens por empresa:
  1. busca publica de vagas do LinkedIn (sem login) pelo nome da empresa; so
     conta a vaga cujo empregador e a propria empresa (`comum.mesma_empresa`);
  2. painel de carreira na Gupy, quando o site da empresa aponta para um
     <sub>.gupy.io (a Gupy lista todas as vagas abertas do painel).

So vira gancho a vaga cujo titulo casa com DASHBOARD (dados, BI, PCP,
controladoria...) ou RAG (qualidade, documentacao, assistencia tecnica, SAC
tecnico...). As demais contam no estado (quantas vagas abertas), sem gancho.
"""
import html
import json
import re
import time
import urllib.parse
from datetime import date

from . import comum

FONTE = 'vagas'
LINKEDIN = 'https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search'
BRASIL = '106057199'   # geoId do Brasil no LinkedIn

DASHBOARD = re.compile(
    r'\b(bi|business intelligence|dados|data|analytics|indicadores|pcp|planejamento e controle|'
    r'planejamento d[ae] (produc|demanda|vendas)|controladoria|controller|custos|fp ?& ?a|s ?& ?op|'
    r'demanda|supply chain|intelig[eê]ncia (comercial|de mercado)|power bi|planejamento financeiro)', re.I)
RAG = re.compile(
    r'(qualidade|sgq|iso ?9001|documenta[cç][aã]o|assist[eê]ncia t[eé]cnica|suporte t[eé]cnico|\bsac\b|'
    r'p[oó]s.?vendas?|engenharia de aplica|aplica[cç][aã]o t[eé]cnica|t[eé]cnico de campo|treinamento t[eé]cnico|'
    r'regulat[oó]ri|assuntos regulat|normas t[eé]cnicas|atendimento t[eé]cnico)', re.I)
# cargos que revelam a dor em cada setor, alem dos gerais acima
# (espelha `vagas` de scripts/receita_federal/perfis_setor.py)
POR_SETOR = {
    'advocacia': {'rag': r'paralegal|controladoria jur[ií]dica|assistente jur[ií]dico|analista jur[ií]dico|estagi[aá]rio de direito',
                  'dashboard': r'controladoria jur[ií]dica|gest[aã]o de (processos|contencioso)|legal ops'},
    'contabilidade': {'rag': r'analista (fiscal|cont[aá]bil|tribut[aá]rio)|departamento pessoal|assistente fiscal',
                      'dashboard': r'bpo|controller|analista financeiro'},
    'engenharia': {'rag': r'projetista|engenheiro (de projetos|civil|eletricista|mec[aâ]nico)|inspetor|t[eé]cnico em edifica',
                   'dashboard': r'planejamento|pmo|controle de projetos'},
    'laboratorios': {'rag': r'biom[eé]dic|t[eé]cnico de laborat|analista de laborat|garantia da qualidade',
                     'dashboard': r'faturamento|conv[eê]nio|glosa'},
    'clinicas': {'rag': r'recepcionista|atendimento ao paciente|enfermeir',
                 'dashboard': r'faturamento|conv[eê]nio|glosa'},
}
ATS_GUPY = re.compile(r'https?://([a-z0-9-]+)\.gupy\.io', re.I)


def classificar(titulo: str, segmento: str | None = None) -> str | None:
    extra = POR_SETOR.get(segmento or '', {})
    d = bool(DASHBOARD.search(titulo)) or bool(extra.get('dashboard') and re.search(extra['dashboard'], titulo, re.I))
    r = bool(RAG.search(titulo)) or bool(extra.get('rag') and re.search(extra['rag'], titulo, re.I))
    return 'ambos' if d and r else 'dashboard' if d else 'rag' if r else None


def linkedin(cli, e) -> list[dict]:
    nome = e['nome_fantasia'] or e['razao_social']
    q = ' '.join(sorted(comum.nucleo(nome), key=len, reverse=True)[:3]) or comum.norm(nome)
    url = f'{LINKEDIN}?keywords={urllib.parse.quote(q)}&location=Brazil&geoId={BRASIL}&start=0'
    r = cli.get(url)
    if r.status_code == 429:
        time.sleep(60)
        r = cli.get(url)
    if r.status_code != 200:
        return []
    vagas = []
    for card in re.findall(r'<li>(.*?)</li>', r.text, re.S):
        g = lambda p: (m := re.search(p, card, re.S)) and html.unescape(m.group(1)).strip()   # noqa: E731
        empresa = g(r'base-search-card__subtitle">.*?>\s*(.*?)\s*<')
        if not empresa or not comum.mesma_empresa(empresa, e['nome_fantasia'], e['razao_social']):
            continue
        link = g(r'href="(https://[^"]+/jobs/view/[^"?]+)')
        vagas.append({'titulo': g(r'base-search-card__title">\s*(.*?)\s*<') or '', 'empresa': empresa,
                      'local': g(r'job-search-card__location">\s*(.*?)\s*<'),
                      'data': g(r'datetime="([^"]+)"'), 'url': link, 'origem': 'linkedin',
                      'id': (re.search(r'-(\d+)$', link or '') or [None, link])[1]})
    return vagas


def gupy(cli, e) -> list[dict]:
    try:
        h = cli.get(e['site_url']).text
    except Exception:
        return []
    subs = {s.lower() for s in ATS_GUPY.findall(h)} - {'www', 'portal', 'login'}
    vagas = []
    for sub in subs:
        try:
            p = cli.get(f'https://{sub}.gupy.io/').text
            m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', p, re.S)
            jobs = json.loads(m.group(1))['props']['pageProps'].get('jobs') or [] if m else []
        except Exception:
            continue
        for j in jobs:
            end = (j.get('workplace') or {}).get('address') or {}
            vagas.append({'titulo': (j.get('title') or '').strip(), 'empresa': sub,
                          'local': ' '.join(x for x in (end.get('city'), end.get('stateShortName')) if x),
                          'data': (j.get('publishedDate') or '')[:10] or None,
                          'url': f'https://{sub}.gupy.io/jobs/{j.get("id")}', 'origem': 'gupy',
                          'id': f'gupy{sub}{j.get("id")}'})
    return vagas


def coletar(supabase, log, pausa: float = 4.0) -> dict:
    emps = comum.empresas(supabase)
    ganchos, estado, sem_resposta, novos = [], {}, 0, 0
    pendentes, estado_pendente = [], {}   # gravados a cada 25 empresas
    with comum.cliente_http() as cli:
        for i, e in enumerate(emps):
            try:
                vagas = linkedin(cli, e) + gupy(cli, e)
            except Exception as ex:   # uma empresa com erro nao derruba as outras
                sem_resposta += 1
                log.warning(f'{e["cnpj"]}: {type(ex).__name__}: {ex}')
                continue
            vistas = {v['id'] for v in vagas}
            estado[e['cnpj']] = estado_pendente[e['cnpj']] = {'abertas': len(vistas), 'em': date.today().isoformat()}
            antes = len(ganchos)
            for v in {v['id']: v for v in vagas}.values():
                pac = classificar(v['titulo'], e.get('segmento'))
                if pac:
                    ganchos.append(comum.gancho(
                        e['cnpj'], FONTE, 'vaga_dados' if pac == 'dashboard' else 'vaga_documentos' if pac == 'rag'
                        else 'vaga_dados_documentos', pac, v['data'] or date.today(),
                        f'Vaga aberta de {v["titulo"]}' + (f' em {v["local"]}' if v['local'] else ''),
                        v['url'], v['id'], origem=v['origem'], empregador=v['empresa']))
            if vagas:
                log.info(f'{(e["nome_fantasia"] or e["razao_social"])[:40]}: {len(vistas)} vagas, '
                         f'{sum(1 for g in ganchos if g["cnpj"] == e["cnpj"])} ganchos')
            pendentes += ganchos[antes:]
            if i % 25 == 0:
                log.info(f'{i}/{len(emps)} empresas lidas')
                comum.salvar_estados(supabase, FONTE, estado_pendente)
                novos += comum.gravar(supabase, pendentes)
                pendentes, estado_pendente = [], {}
            time.sleep(pausa)
    comum.salvar_estados(supabase, FONTE, estado_pendente)
    novos += comum.gravar(supabase, pendentes)
    return {'empresas': len(emps), 'com_vagas': sum(1 for x in estado.values() if x['abertas']),
            'ganchos': len(ganchos), 'novos': novos, 'erros': sem_resposta}
