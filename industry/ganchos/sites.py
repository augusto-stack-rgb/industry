"""Mudancas no site da empresa.

Le o sitemap (do robots.txt ou nos caminhos de costume, seguindo indices) e os
links para PDF da home e do sitemap. A data vem do `lastmod` do sitemap e do
`Last-Modified` de cada PDF, entao ha gancho ja na primeira rodada; nas
seguintes, URL ou PDF que nao existia na leitura anterior tambem vira gancho.

  pdf_novo            PDF publicado ou atualizado nos ultimos 180 dias (RAG)
  paginas_atualizadas paginas de um tipo (produtos, downloads, unidades...)
                      com lastmod nos ultimos 90 dias, agrupadas por tipo e mes
  paginas_novas       URLs do sitemap que nao existiam na leitura anterior
"""
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

from . import comum

FONTE = 'site'
MAX_SITEMAPS = 12
MAX_PDFS = 40
TIPOS = {   # tipo de pagina -> (regex na URL, pacote, nome no texto)
    'produtos': (r'produto|product|catalog|cat[aá]logo|linha|/loja/|/shop/', 'ambos', 'produtos e catálogo'),
    'documentos': (r'download|manual|ficha|document|norma|certifica|boletim|fispq|datasheet|publica[cç]|artigo|informativ|'
                   r'jurisprud|legisla|exame|preparo|especialidade|procedimento|laudo|ensaio', 'rag', 'documentos, publicações e serviços'),
    'unidades': (r'unidade|filia|onde.?estamos|representante|distribuidor|revenda|lojas', 'dashboard', 'unidades e representantes'),
    'noticias': (r'noticia|news|blog|imprensa|novidade|lancamento|lan[cç]amento', 'ambos', 'notícias e lançamentos'),
}
IGNORAR = re.compile(r'/(tag|category|categoria|author|autor|page|feed|wp-json|attachment)/|\?', re.I)


def _data(s):
    try:
        return datetime.fromisoformat(s.strip().replace('Z', '+00:00')[:25]).date()
    except Exception:
        try:
            return date.fromisoformat(s.strip()[:10])
        except Exception:
            return None


def sitemap(cli, raiz) -> dict[str, date | None]:
    fila = []
    try:
        rb = cli.get(urllib.parse.urljoin(raiz, '/robots.txt'))
        fila = re.findall(r'(?im)^\s*sitemap:\s*(\S+)', rb.text) if rb.status_code == 200 else []
    except Exception:
        pass
    fila = fila or [urllib.parse.urljoin(raiz, p) for p in ('/sitemap.xml', '/sitemap_index.xml', '/wp-sitemap.xml')]
    urls, vistos = {}, set()
    while fila and len(vistos) < MAX_SITEMAPS:
        sm = fila.pop(0)
        if sm in vistos:
            continue
        vistos.add(sm)
        try:
            r = cli.get(sm)
        except Exception:
            continue
        if r.status_code != 200 or '<loc>' not in r.text:
            continue
        for bloco in re.findall(r'<(?:url|sitemap)>(.*?)</(?:url|sitemap)>', r.text, re.S):
            loc = re.search(r'<loc>\s*(.*?)\s*</loc>', bloco, re.S)
            if not loc:
                continue
            u = loc.group(1).replace('&amp;', '&')
            lm = re.search(r'<lastmod>\s*(.*?)\s*</lastmod>', bloco, re.S)
            if '<sitemap>' in r.text and re.search(r'sitemap[^/]*\.xml', u, re.I):
                fila.append(u)
            else:
                urls[u] = _data(lm.group(1)) if lm else None
    # sitemap gerado na hora poe a mesma data em tudo: ai a data nao diz nada
    datas = [d for d in urls.values() if d]
    if len(datas) >= 10 and max(datas.count(x) for x in set(datas)) / len(datas) > 0.8:
        urls = dict.fromkeys(urls)
    return urls


def pdfs(cli, raiz, urls) -> dict[str, date | None]:
    achados = {u for u in urls if u.lower().split('?')[0].endswith('.pdf')}
    try:
        h = cli.get(raiz).text
        for href in re.findall(r'href=["\']([^"\']+\.pdf)["\']', h, re.I):
            achados.add(urllib.parse.urljoin(raiz, href))
    except Exception:
        pass
    datas = {}
    for u in sorted(achados)[:MAX_PDFS]:
        try:
            r = cli.head(u)
            lm = r.headers.get('last-modified')
            datas[u] = parsedate_to_datetime(lm).date() if r.status_code == 200 and lm else None
        except Exception:
            datas[u] = None
    return datas


def tipo_de(url):
    for t, (pad, _, _) in TIPOS.items():
        if re.search(pad, url, re.I):
            return t
    return None


def coletar(supabase, log, pausa: float = 1.0) -> dict:
    emps = comum.empresas(supabase)
    antes = comum.estados(supabase, FONTE)
    hoje = date.today()
    ganchos, estado, sem_sitemap = [], {}, 0
    with comum.cliente_http(timeout=25) as cli:
        for e in emps:
            c = e['cnpj']
            raiz = '{0.scheme}://{0.netloc}/'.format(urllib.parse.urlparse(e['site_url']))
            urls = {u: d for u, d in sitemap(cli, raiz).items() if not IGNORAR.search(u)}
            sem_sitemap += not urls
            docs = pdfs(cli, raiz, urls)
            ant = antes.get(c) or {}
            # PDFs recentes, ou que nao existiam na leitura anterior
            for u, d in sorted(docs.items(), key=lambda x: x[1] or date.min, reverse=True):
                novo = ant and u not in set(ant.get('pdfs') or [])
                if (d and d >= hoje - timedelta(days=180)) or novo:
                    nome = urllib.parse.unquote(u.rsplit('/', 1)[-1])
                    ganchos.append(comum.gancho(c, FONTE, 'pdf_novo', 'rag', d or hoje,
                                                f'PDF publicado ou atualizado no site: {nome}', u, u))
            # paginas atualizadas nos ultimos 90 dias, agrupadas por tipo e mes
            grupos = {}
            for u, d in urls.items():
                t = tipo_de(u)
                if t and d and d >= hoje - timedelta(days=90):
                    grupos.setdefault((t, d.strftime('%Y-%m')), []).append((d, u))
            for (t, mes), itens in grupos.items():
                itens.sort(reverse=True)
                _, pac, nome = TIPOS[t]
                ganchos.append(comum.gancho(
                    c, FONTE, 'paginas_atualizadas', pac, itens[0][0],
                    f'{len(itens)} página(s) de {nome} atualizada(s) em {mes[5:]}/{mes[:4]}',
                    itens[0][1], f'{c}|{t}|{mes}', exemplos=[u for _, u in itens[:5]]))
            # URLs novas desde a leitura anterior
            if ant:
                novas = [u for u in urls if u not in set(ant.get('urls') or []) and tipo_de(u)]
                por_tipo = {}
                for u in novas:
                    por_tipo.setdefault(tipo_de(u), []).append(u)
                for t, us in por_tipo.items():
                    _, pac, nome = TIPOS[t]
                    ganchos.append(comum.gancho(
                        c, FONTE, 'paginas_novas', pac, hoje, f'{len(us)} página(s) nova(s) de {nome} no site',
                        us[0], f'{c}|{t}|{hoje.isoformat()}', exemplos=us[:5]))
            estado[c] = {'urls': sorted(urls)[:3000], 'pdfs': sorted(docs), 'em': hoje.isoformat()}
            time.sleep(pausa)
    comum.salvar_estados(supabase, FONTE, estado)
    novos = comum.gravar(supabase, ganchos)
    log.info(f'{len(emps)} sites, {sem_sitemap} sem sitemap, {len(ganchos)} ganchos, {novos} novos')
    return {'empresas': len(emps), 'sem_sitemap': sem_sitemap, 'ganchos': len(ganchos), 'novos': novos}
