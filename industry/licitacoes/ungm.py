"""Coletor UNGM — United Nations Global Marketplace (compras das agências da ONU).

Portal comum de aquisições de ~30 agências (OMS, UNICEF, OIT, FAO, UNOPS, OMPI,
ONU Mulheres...). Empresas de qualquer país concorrem; "Call for individual
consultants" é para pessoa física. A busca pública é um POST JSON que devolve
HTML (15 avisos por página) e exige o token antiforgery da página.

Uma busca por termo no TÍTULO (``UNGM_TERMOS``), só avisos ativos, das mais
recentes para as mais antigas, até ``UNGM_MAX_PAGINAS`` páginas. Os do PNUD ficam
de fora: o ``pnud.py`` já cobre. Ficam os que casam com os temas
(``termos.termos_exterior``) — "data" e "digital" sozinhos não bastam.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import (
    DEST_DIR, REQUEST_HEADERS, UNGM_MAX_PAGINAS, UNGM_TERMOS, UNGM_URL,
)
from industry.licitacoes.net import ColetorBloqueado
from industry.licitacoes.termos import termos_exterior

FONTE = "UNGM - Agências da ONU"
BUSCA_URL = UNGM_URL + "/Search"
LINK = "https://www.ungm.org/Public/Notice/{id}"
FORA = {"UNDP"}


def _data(s: str) -> str:
    """'02-Nov-2026 16:30 (GMT 2.00) ...' -> '2026-11-02T16:30:00'; '13-Oct-2026' -> data."""
    m = re.match(r"(\d{2}-[A-Za-z]{3}-\d{4})(?:\s+(\d{2}:\d{2}))?", (s or "").strip())
    if not m:
        return ""
    try:
        if m.group(2):
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d-%b-%Y %H:%M"
                                     ).strftime("%Y-%m-%dT%H:%M:%S")
        return datetime.strptime(m.group(1), "%d-%b-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _corpo(termo: str, pagina: int) -> dict:
    return {"PageIndex": pagina, "PageSize": 15, "Title": termo, "Description": "",
            "Reference": "", "PublishedFrom": "", "PublishedTo": "", "DeadlineFrom": "",
            "DeadlineTo": "", "Countries": [], "Agencies": [], "UNSPSCs": [],
            "NoticeTypes": [], "SortField": "DatePublished", "SortAscending": False,
            "isPicker": False, "IsSustainable": False, "IsActive": True,
            "NoticeDisplayType": None, "NoticeSearchTotalLabelId": "noticeSearchTotal",
            "TypeOfCompetitions": []}


def _avisos(html: str) -> list[dict]:
    out = []
    for row in BeautifulSoup(html, "html.parser").select("div.tableRow"):
        c = [x.get_text(" ", strip=True) for x in row.select("div.tableCell")]
        if len(c) < 8:
            continue
        titulo = re.sub(r"\s*Open in a new window\s*$", "", c[1]).strip()
        out.append({"id": row.get("data-noticeid", ""), "titulo": titulo, "prazo": c[2],
                    "publicado": c[3], "agencia": c[4], "tipo": c[5], "referencia": c[6],
                    "pais": c[7]})
    return out


def collect_ungm(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    try:
        pagina = s.get(UNGM_URL, timeout=60)
        pagina.raise_for_status()
    except requests.RequestException as e:
        raise ColetorBloqueado(f"UNGM: {type(e).__name__}: {e}") from e
    m = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)"',
                  pagina.text)
    if not m:
        raise ColetorBloqueado("UNGM: token antiforgery não encontrado")
    cab = {"RequestVerificationToken": m.group(1), "X-Requested-With": "XMLHttpRequest"}

    rows: dict[str, dict] = {}
    vistos = 0
    for termo in UNGM_TERMOS:
        for p in range(UNGM_MAX_PAGINAS):
            try:
                r = s.post(BUSCA_URL, json=_corpo(termo.strip(), p), headers=cab, timeout=60)
                r.raise_for_status()
            except requests.RequestException as e:
                raise ColetorBloqueado(f"UNGM busca {termo!r}: {type(e).__name__}: {e}") from e
            avisos = _avisos(r.text)
            vistos += len(avisos)
            for a in avisos:
                if a["agencia"] in FORA or a["id"] in rows:
                    continue
                termos = termos_exterior(a["titulo"])
                if not termos:
                    continue
                rows[a["id"]] = {
                    "id": a["id"],
                    "fonte": FONTE,
                    "tipo_documento": "edital",
                    "tipo": a["tipo"],
                    "categoria": ("Pessoa física (consultor individual)"
                                  if "individual" in a["tipo"].lower() else "Empresa"),
                    "numero": a["referencia"],
                    "titulo": a["titulo"],
                    "objeto": a["titulo"],
                    "data_publicacao": _data(a["publicado"]),
                    "data_encerramento_proposta": _data(a["prazo"]),
                    "orgao": a["agencia"],
                    "pais": a["pais"],
                    "link": LINK.format(id=a["id"]),
                    "filtros_usados": "; ".join(termos),
                }
            if len(avisos) < 15:
                break
            time.sleep(1)
    df = pd.DataFrame(list(rows.values()))
    out = dest_dir / "ungm.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"UNGM: {vistos} avisos lidos; {len(df)} no tema -> {out}")
    return out


if __name__ == "__main__":
    print(collect_ungm())
