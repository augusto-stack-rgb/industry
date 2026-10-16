import re
import time
import requests
import pandas as pd

from datetime import date, datetime

from .configuracoes import JornalPloneAPI
from . import saude
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine


def _url_api(url_pagina: str) -> str:
    # https://www.gov.br/<orgao>/pt-br/... -> https://www.gov.br/<orgao>/++api++/pt-br/...
    return re.sub(r"(https://www\.gov\.br/[^/]+)/", r"\1/++api++/", url_pagina, count=1)


def _texto_blocos(item: dict) -> str:
    """Junta o texto dos blocos de parágrafo (slate) na ordem do layout."""
    blocos = item.get("blocks") or {}
    ordem = (item.get("blocks_layout") or {}).get("items") or list(blocos)
    partes = [
        (blocos.get(b) or {}).get("plaintext", "").strip()
        for b in ordem
        if (blocos.get(b) or {}).get("@type") == "slate"
    ]
    return " ".join(p for p in partes if p)


def coletar_plone_api(config: JornalPloneAPI, engine=None) -> pd.DataFrame:
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)

    headers = {"Accept": "application/json", **(config.headers or {"User-Agent": "Mozilla/5.0"})}
    resp = requests.get(
        config.url,
        params={
            "portal_type": "News Item",
            "sort_on": "effective",
            "sort_order": "reverse",
            "b_size": config.max_itens,
            "metadata_fields": ["effective", "Title"],
        },
        headers=headers,
        timeout=config.timeout,
    )
    resp.raise_for_status()
    itens = resp.json().get("items", [])
    saude.links(len(itens))

    rows = []
    for it in itens:
        link = it.get("@id")
        if not link:
            continue
        try:
            r = requests.get(_url_api(link), headers=headers, timeout=config.timeout)
            r.raise_for_status()
            materia = r.json()
        except Exception as e:
            print(f"  [AVISO] Falha ao acessar {link}: {e}")
            continue

        efetiva = materia.get("effective") or it.get("effective")
        rows.append(
            {
                "titulo": (materia.get("title") or it.get("title") or "").strip(),
                "link": link,
                "data": datetime.fromisoformat(efetiva).strftime("%d/%m/%Y") if efetiva else None,
                "texto": _texto_blocos(materia) or (materia.get("description") or "").strip() or None,
                "fonte": config.fonte,
                "setor": config.setor,
            }
        )
        time.sleep(config.sleep_seconds)

    print(f"  [{config.fonte}] {len(itens)} itens na API, {len(rows)} baixados")

    saude.artigos(rows)
    df = pd.DataFrame(rows, columns=["titulo", "link", "data", "texto", "fonte", "setor"])
    df = df.dropna(subset=["texto"])
    df = df[df["texto"].str.strip() != ""]

    if df.empty:
        # Diferente do sitemap antigo (que devolvia 0 linhas como success),
        # API sem matéria nenhuma é sinal de mudança no site.
        raise RuntimeError(f"{config.fonte}: nenhuma matéria com texto em {config.url}")

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
