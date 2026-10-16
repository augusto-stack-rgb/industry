import re
import time
import requests
import pandas as pd

from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import date

from .configuracoes import JornalSublinks
from . import saude
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine


def coletar_sublinks(config: JornalSublinks, engine=None) -> pd.DataFrame:
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)

    resp = requests.get(config.url, headers=config.headers, timeout=config.timeout)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    rows = []
    artigos_listagem = soup.select(config.article_selector)
    saude.links(len(artigos_listagem))

    for article in artigos_listagem:
        if config.article_is_link:
            a = article
        else:
            a = article.select_one(config.title_link_selector)
        if not a:
            continue

        href = a.get("href")
        if not href:
            continue

        link = urljoin(config.url, href) if config.use_urljoin else href
        titulo = a.get_text(strip=True)
        if config.title_selector:
            title_tag = article.select_one(config.title_selector)
            if title_tag:
                titulo = title_tag.get_text(strip=True)

        data = None
        texto = None

        if config.data_selector or config.data_regex or config.content_selector:
            try:
                detail_resp = requests.get(
                    link, headers=config.headers, timeout=config.timeout
                )
                detail_resp.raise_for_status()
            except Exception as e:
                # Falha pontual (rede, 5xx intermitente) não pode derrubar
                # a task inteira e descartar os artigos já coletados.
                print(f"  [AVISO] Falha ao acessar {link}: {e}")
                time.sleep(config.sleep_seconds)
                continue

            detail_soup = BeautifulSoup(detail_resp.text, "html.parser")

            if config.data_selector:
                data_tag = detail_soup.select_one(config.data_selector)
                if data_tag:
                    data = data_tag.get_text(strip=True).split("|")[0].strip()
            elif config.data_regex:
                pattern = re.compile(config.data_regex)
                for p in detail_soup.find_all("p"):
                    m = pattern.search(p.get_text(" ", strip=True))
                    if m:
                        data = m.group(1) if m.lastindex else m.group(0)
                        break

            if config.content_selector:
                content_soup = detail_soup

                # Caso especial: conteúdo dentro de iframe com srcdoc
                if config.content_iframe_selector:
                    iframe = detail_soup.select_one(config.content_iframe_selector)

                    if iframe and config.content_iframe_srcdoc:
                        srcdoc = iframe.get("srcdoc")
                        if srcdoc:
                            content_soup = BeautifulSoup(srcdoc, "html.parser")

                content = content_soup.select_one(config.content_selector)

                if content:
                    paragrafos = content.find_all("p")
                    texto = " ".join(
                        p.get_text(" ", strip=True)
                        for p in paragrafos
                        if p.get_text(strip=True)
                    )

            time.sleep(config.sleep_seconds)

        rows.append(
            {
                "titulo": titulo,
                "link": link,
                "data": data,
                "texto": texto,
                "fonte": config.fonte,
                "setor": config.setor,
            }
        )

    saude.artigos(rows)

    if not rows:
        # article_selector não casou nada (site mudou o HTML) ou todos os
        # artigos falharam ao coletar detalhe. df vazio não tem coluna
        # "texto" e o dropna abaixo quebraria com KeyError.
        print(f"  [AVISO] Nenhum artigo coletado em {config.url}")
        return pd.DataFrame(columns=["titulo", "link", "data", "texto", "fonte", "setor"])

    df = pd.DataFrame(rows)

    df = df.dropna(subset=["texto"])
    df = df[df["texto"].str.strip() != ""]

    if df.empty:
        print(
            f"  [AVISO] {config.fonte}: {len(rows)} artigos sem texto — "
            f"content_selector '{config.content_selector}' pode ter quebrado"
        )

    df["data_coleta"] = date.today()

    with engine.begin() as conn:
        df.to_sql(
            name="noticias_raw",
            schema="noticias",
            con=conn,
            if_exists="append",
            index=False,
        )

    return df
