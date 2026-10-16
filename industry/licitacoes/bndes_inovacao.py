"""Coletor BNDES — chamadas públicas de inovação (CPSI, LC 182/2021).

Página própria do BNDES, fora do ``licitacoes.json`` que o ``bndes.py`` lê. É
onde saem os "Desafios BNDES" de contratação de solução inovadora — vários de
dados e IA (ex.: "Inteligência de Dados em Saúde", "IA Jurídica", "Score
Inteligente de MEI"). Cada chamada é um ``<h4>`` seguido do objeto e do link.

A página não traz data nem situação. Para não trazer desafios antigos como
"novos" na primeira coleta, ficam só os dos últimos ``ANOS_RECENTES`` anos (pelo
ano no título); o resto da janela é o mesmo do bronze.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import BNDES_INOVACAO_URL, DEST_DIR, REQUEST_HEADERS
from industry.licitacoes.net import ColetorBloqueado

FONTE = "BNDES - Chamadas públicas de inovação (CPSI)"
ANOS_RECENTES = 2


def _desembrulhar(href: str) -> str:
    """Links da página passam pelo SafeLinks do Outlook; devolve a URL real."""
    if "safelinks.protection.outlook.com" in href:
        url = parse_qs(urlparse(href).query).get("url", [""])[0]
        return url or href
    return href


def collect_bndes_inovacao(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(BNDES_INOVACAO_URL, headers=REQUEST_HEADERS, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ColetorBloqueado(f"BNDES inovação: {type(e).__name__}: {e}") from e

    soup = BeautifulSoup(r.text, "html.parser")
    ano_min = date.today().year - (ANOS_RECENTES - 1)
    rows = []
    for h in soup.find_all("h4"):
        titulo = h.get_text(" ", strip=True).replace("\xa0", " ")
        if not re.search(r"\bCPSI\b|chamada p[úu]blica", titulo, re.I):
            continue
        m = re.search(r"(20\d{2})", titulo)
        if m and int(m.group(1)) < ano_min:
            continue
        textos, links = [], []
        for sib in h.find_next_siblings():
            if sib.name == "h4":
                break
            t = sib.get_text(" ", strip=True)
            if t:
                textos.append(t)
            links += [_desembrulhar(urljoin(BNDES_INOVACAO_URL, a["href"]))
                      for a in sib.find_all("a", href=True)]
        objeto = " ".join(t for t in textos if not t.lower().startswith("acesse"))
        numero = re.split(r"\s+[–-]\s+", titulo, maxsplit=1)[0]
        rows.append({
            "id": hashlib.sha1(titulo.encode()).hexdigest()[:16],
            "fonte": FONTE,
            "tipo_documento": "edital",
            "tipo": "CPSI (LC 182/2021)",
            "numero": numero,
            "titulo": titulo,
            "objeto": objeto or titulo,
            "link": links[0] if links else BNDES_INOVACAO_URL,
            "orgao": "BNDES",
            "orgao_cnpj": "33657248000189",
            "banco": "BNDES",
            "ano": m.group(1) if m else "",
        })

    df = pd.DataFrame(rows)
    out = dest_dir / "bndes_inovacao.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"BNDES inovação: {len(df)} chamada(s) (desde {ano_min}) -> {out}")
    return out


if __name__ == "__main__":
    print(collect_bndes_inovacao())
