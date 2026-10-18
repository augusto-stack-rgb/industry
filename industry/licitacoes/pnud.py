"""Coletor PNUD — avisos de aquisição do Programa das Nações Unidas para o
Desenvolvimento (procurement-notices.undp.org).

A página pública lista, de uma vez, todos os avisos abertos (~540 em 17/10/2026)
dos escritórios do PNUD no mundo, com país, tipo de processo e prazo. O processo
"IC - Individual contractor" é contratação de PESSOA FÍSICA (consultor
individual): não pede CNPJ. Os demais (RFP, RFQ, ITB, EOI) são para empresas.

Ficam os que casam com os temas no título (``termos.termos_exterior``). O título
é curto; o LLM do score julga com o que houver.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import DEST_DIR, PNUD_URL, REQUEST_HEADERS
from industry.licitacoes.net import ColetorBloqueado
from industry.licitacoes.termos import termos_exterior

FONTE = "PNUD - Avisos de aquisição"


def _data(s: str) -> str:
    """'02-Dec-26 05:00 AM (New York time)' -> '2026-12-02T05:00:00'."""
    m = re.match(r"(\d{2}-[A-Za-z]{3}-\d{2})(?:\s+(\d{1,2}:\d{2}\s*[AP]M))?", (s or "").strip())
    if not m:
        return ""
    try:
        if m.group(2):
            return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d-%b-%y %I:%M %p"
                                     ).strftime("%Y-%m-%dT%H:%M:%S")
        return datetime.strptime(m.group(1), "%d-%b-%y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _celulas(a) -> dict:
    """Cada célula tem um rótulo ("Title", "Ref No"...) seguido do valor."""
    out = {}
    for c in a.select("div.vacanciesTable__cell"):
        rotulo = c.select_one(".vacanciesTable__cell__label")
        r = rotulo.get_text(" ", strip=True) if rotulo else ""
        v = c.get_text(" ", strip=True)
        out[r] = v[len(r):].strip() if r and v.startswith(r) else v
    return out


def collect_pnud(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(PNUD_URL, headers=REQUEST_HEADERS, timeout=90)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ColetorBloqueado(f"PNUD: {type(e).__name__}: {e}") from e
    avisos = BeautifulSoup(r.text, "html.parser").select("a.vacanciesTableLink")
    if not avisos:
        raise ColetorBloqueado("PNUD: nenhum aviso na página (layout mudou?)")

    rows = []
    for a in avisos:
        c = _celulas(a)
        titulo = c.get("Title", "")
        termos = termos_exterior(titulo)
        if not termos:
            continue
        link = urljoin(PNUD_URL, a.get("href", ""))
        nego = parse_qs(urlparse(link).query).get("nego_id", [""])[0]
        escritorio = c.get("UNDP Office/Country", "")
        processo = c.get("Process", "")
        rows.append({
            "id": nego or link,
            "fonte": FONTE,
            "tipo_documento": "edital",
            "tipo": processo,
            "categoria": ("Pessoa física (consultor individual)"
                          if processo.upper().startswith("IC") else "Empresa"),
            "numero": c.get("Ref No", ""),
            "titulo": titulo,
            "objeto": titulo,
            "data_publicacao": _data(c.get("Posted", "")),
            "data_encerramento_proposta": _data(c.get("Deadline", "")),
            "orgao": escritorio.split("/")[0] or "PNUD",
            "pais": escritorio.split("/", 1)[1].title() if "/" in escritorio else "",
            "link": link,
            "filtros_usados": "; ".join(termos),
        })
    df = pd.DataFrame(rows)
    out = dest_dir / "pnud.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"PNUD: {len(avisos)} avisos abertos; {len(df)} no tema -> {out}")
    return out


if __name__ == "__main__":
    print(collect_pnud())
