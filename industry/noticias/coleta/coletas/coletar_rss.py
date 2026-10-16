import time
import requests
import feedparser
import pandas as pd

from bs4 import BeautifulSoup
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Optional

from .configuracoes import JornalRSS
from . import saude
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine


def _parse_data_rss(entry) -> Optional[str]:
    for campo in ("published", "updated"):
        valor = getattr(entry, campo, None)
        if valor:
            try:
                return parsedate_to_datetime(valor).date().isoformat()
            except Exception:
                return valor
    return None


def _extrair_texto_artigo(url: str, config: JornalRSS) -> Optional[str]:
    try:
        resp = requests.get(url, headers=config.headers, timeout=config.timeout)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [AVISO] Falha ao acessar {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    content = None
    if config.content_selector:
        for seletor in config.content_selector.split(","):
            content = soup.select_one(seletor.strip())
            if content:
                break

    if not content:
        for tag in ("article", "main"):
            content = soup.select_one(tag)
            if content:
                break

    if not content:
        return None

    paragrafos = content.find_all("p")
    texto = " ".join(
        p.get_text(" ", strip=True) for p in paragrafos if p.get_text(strip=True)
    )
    return texto if texto.strip() else None


def _extrair_data_pagina(url: str, config: JornalRSS) -> Optional[str]:
    if not config.data_selector:
        return None
    try:
        resp = requests.get(url, headers=config.headers, timeout=config.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tag = soup.select_one(config.data_selector)
        if tag:
            return tag.get("datetime") or tag.get_text(strip=True).split("|")[0].strip()
    except Exception:
        pass
    return None


def coletar_rss(config: JornalRSS, engine=None) -> pd.DataFrame:
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)
    feed = feedparser.parse(config.url, request_headers=config.headers)
    saude.links(len(feed.entries))

    if feed.bozo and not feed.entries:
        print(
            f"  [ERRO] Feed inválido ou inacessível ({config.fonte}): {feed.bozo_exception}"
        )
        return pd.DataFrame()

    rows = []

    for entry in feed.entries:
        titulo = entry.get("title", "").strip()
        link = entry.get("link", "").strip()

        if not link:
            continue

        data = _parse_data_rss(entry)

        if config.texto_do_titulo:
            # Google News termina o título com " - <fonte>".
            fonte_feed = (entry.get("source") or {}).get("title")
            if fonte_feed and titulo.endswith(f" - {fonte_feed}"):
                titulo = titulo[: -len(f" - {fonte_feed}")].strip()
            rows.append(
                {"titulo": titulo, "link": link, "data": data, "texto": titulo,
                 "fonte": config.fonte, "setor": config.setor}
            )
            continue

        texto = None
        summary = entry.get("summary", "")
        if summary and not config.texto_da_pagina:
            candidato = BeautifulSoup(summary, "html.parser").get_text(" ", strip=True)
            if len(candidato) > 100:
                texto = candidato

        if not texto:
            texto = _extrair_texto_artigo(link, config)
            time.sleep(config.sleep_seconds)

        if not data and config.data_selector:
            data = _extrair_data_pagina(link, config)

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
    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.dropna(subset=["texto"])
    df = df[df["texto"].str.strip() != ""]

    if df.empty:
        print(
            f"  [AVISO] {config.fonte}: {len(rows)} entradas sem texto — "
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
