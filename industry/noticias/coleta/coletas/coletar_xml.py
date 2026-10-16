import html
import requests
import pandas as pd

from datetime import datetime, date, timedelta, timezone
from bs4 import BeautifulSoup
from lxml import etree as ET
from sqlalchemy import text

from .configuracoes import JornalXML
from . import saude
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine


def _links_existentes(engine, fonte: str) -> set:
    with engine.connect() as conn:
        res = conn.execute(
            text("SELECT DISTINCT link FROM noticias.noticias_raw WHERE fonte = :f"),
            {"f": fonte},
        )
        return {r[0] for r in res}


def _extrair_texto(soup: BeautifulSoup, config: JornalXML):
    if config.content_selector:
        corpo = soup.select_one(config.content_selector)
        if not corpo:
            return None
        for tag in corpo(["script", "style"]):
            tag.decompose()
        paragrafos = [p.get_text(" ", strip=True) for p in corpo.find_all("p")]
        # "Publicidade" é o rótulo dos blocos de anúncio no meio do texto.
        texto = " ".join(p for p in paragrafos if p and p != "Publicidade")
        return texto or None

    article_body = soup.select_one('#content-core [property="rnews:articleBody"]')
    if not article_body:
        return None
    for tag in article_body(["script", "style"]):
        tag.decompose()
    return article_body.get_text(" ", strip=True)


def coletar_xml(config: JornalXML, engine=None) -> pd.DataFrame:
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)

    headers = config.headers or {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(config.url, headers=headers, timeout=30)
    resp.raise_for_status()

    ns = {
        "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
        "news": "http://www.google.com/schemas/sitemap-news/0.9",
    }

    parser = ET.XMLParser(recover=True)
    root = ET.fromstring(resp.content, parser=parser)

    # Índice de sitemaps (Reuters: 15 páginas de 50 matérias): junta os <url>
    # dos filhos num único root, na ordem do índice (mais recentes primeiro).
    if root.tag.endswith("sitemapindex"):
        filhos = [(n.text or "").strip() for n in root.findall("sm:sitemap/sm:loc", ns) if (n.text or "").strip()][: config.max_sitemaps]
        root = ET.Element("urlset")
        for filho in filhos:
            try:
                r = requests.get(filho, headers=headers, timeout=30)
                r.raise_for_status()
            except Exception as e:
                print(f"  [AVISO] Falha no sitemap {filho}: {e}")
                continue
            for n in ET.fromstring(r.content, parser=parser).findall("sm:url", ns):
                root.append(n)

    limite = None
    if config.janela_horas:
        limite = datetime.now(timezone.utc) - timedelta(hours=config.janela_horas)
    existentes = _links_existentes(engine, config.fonte) if config.pular_existentes else set()
    excluidos = config.excluded_path_parts or []

    rows = []
    candidatos = 0
    ja_coletados = 0

    for url_node in root.findall("sm:url", ns):
        if candidatos >= config.max_urls:
            break

        loc = url_node.find("sm:loc", ns)
        news_node = url_node.find("news:news", ns)

        if loc is None or news_node is None:
            continue

        titulo_node = news_node.find("news:title", ns)
        pub_date_node = news_node.find("news:publication_date", ns)

        if pub_date_node is None:
            continue

        link = (loc.text or "").strip()
        try:
            publicado = datetime.fromisoformat((pub_date_node.text or "").strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if not link:
            continue

        if limite and publicado < limite:
            continue
        partes = [p for p in link.split("/")[3:] if p]
        if any(p in excluidos for p in partes):
            continue
        if config.incluir_path_parts and not any(p in config.incluir_path_parts for p in partes):
            continue
        if link in existentes:
            ja_coletados += 1
            continue

        candidatos += 1
        titulo = html.unescape((titulo_node.text or "").strip()) if titulo_node is not None else None
        if config.texto_do_titulo:
            rows.append(
                {
                    "titulo": titulo,
                    "link": link,
                    "data": publicado.strftime("%d/%m/%Y"),
                    "texto": titulo,
                    "fonte": config.fonte,
                    "setor": config.setor,
                }
            )
            continue

        try:
            r = requests.get(link, headers=headers, timeout=30)
            r.raise_for_status()
        except Exception as e:
            # Uma URL fora do ar não pode descartar as já baixadas.
            print(f"  [AVISO] Falha ao acessar {link}: {e}")
            continue

        soup = BeautifulSoup(r.text, "html.parser")

        rows.append(
            {
                # InfoMoney escapa duas vezes (S&amp;amp;P chega como S&amp;P).
                "titulo": titulo,
                "link": link,
                "data": publicado.strftime("%d/%m/%Y"),
                "texto": _extrair_texto(soup, config),
                "fonte": config.fonte,
                "setor": config.setor,
            }
        )

    print(f"  [{config.fonte}] {candidatos} URLs novas no sitemap, {len(rows)} baixadas")

    saude.links(candidatos + ja_coletados, ja_existentes=ja_coletados)
    saude.artigos(rows)

    df = pd.DataFrame(
        rows, columns=["titulo", "link", "data", "texto", "fonte", "setor"]
    )

    if df.empty:
        return df

    df = df.dropna(subset=["texto"])
    df = df[df["texto"].str.strip() != ""]

    if df.empty:
        print(
            f"  [AVISO] {config.fonte}: {len(rows)} artigos sem texto — "
            f"seletor de conteúdo pode ter quebrado"
        )
        return df

    df["data_coleta"] = date.today()

    with engine.begin() as conn:
        df.to_sql(
            name="noticias_raw",
            con=conn,
            if_exists="append",
            schema="noticias",
            index=False,
        )

    return df
