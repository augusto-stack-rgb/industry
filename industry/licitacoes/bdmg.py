"""Coletor BDMG — Editais e Licitações (requests + bs4).

Listagem WordPress paginada (/editais-licitacoes/page/N/); para os anos-alvo abre
a página de detalhe, extrai objeto/data e as URLs de PDF (coluna `documentos`,
JSON) — o download/extração fica a cargo do anexos.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import DEST_DIR, REQUEST_HEADERS, REQUEST_TIMEOUT, ANOS_ALVO
from industry.licitacoes.net import ColetorBloqueado

FONTE = "BDMG - Banco de Desenvolvimento de Minas Gerais"
LISTA = "https://www.bdmg.mg.gov.br/editais-licitacoes/"


def _modalidade(titulo: str) -> str:
    t = titulo.lower()
    for chave, nome in [("inexig", "Inexigibilidade"), ("dispensa", "Dispensa"),
                        ("registro de pre", "Registro de Preços"), ("concorr", "Concorrência"),
                        ("preg", "Pregão"), ("tomada", "Tomada de Preços"),
                        ("credenciamento", "Credenciamento"), ("edital", "Edital")]:
        if chave in t:
            return nome
    return ""


def collect_bdmg(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)

    alvos = {}
    page = 1
    while page <= 200:
        u = LISTA if page == 1 else f"{LISTA}page/{page}/"
        r = s.get(u, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            if page == 1:
                # A 1ª página da listagem tem que responder. Desde ~01/10/2026 o
                # Akamai do BDMG devolve 403 ("Access Denied") e o coletor saía
                # com CSV vazio e task VERDE — 0 linhas por dias sem ninguém ver
                # (o bronze só logava "No columns to parse"). Ver DECISOES.md §27.
                raise ColetorBloqueado(
                    f"{FONTE}: listagem respondeu HTTP {r.status_code} "
                    f"(possível bloqueio/WAF). Início: {r.text[:120]!r}")
            break
        soup = BeautifulSoup(r.content, "lxml")
        novos = 0
        for a in soup.find_all("a", href=True):
            if "/licitacao/" in a["href"]:
                titulo = a.get_text(" ", strip=True)
                if titulo and a["href"] not in alvos:
                    alvos[a["href"]] = titulo
                    novos += 1
        if not novos:
            break
        page += 1

    if not alvos:
        # Página respondeu 200 mas sem nenhum link de licitação: layout mudou ou
        # página de bloqueio com status 200. A listagem nunca é vazia de verdade.
        raise ColetorBloqueado(f"{FONTE}: listagem sem nenhum link /licitacao/ "
                               "(layout mudou ou bloqueio com HTTP 200)")

    rows = []
    for url, titulo in alvos.items():
        anos = re.findall(r"\b(20\d{2})\b", titulo + " " + url)
        ano = anos[0] if anos else ""
        objeto, data, docs = "", "", []
        if ano in ANOS_ALVO:   # detalhe+PDFs só para os anos-alvo (só novos)
            try:
                rd = s.get(url, timeout=REQUEST_TIMEOUT)
                rd.raise_for_status()
                ds = BeautifulSoup(rd.content, "lxml")
                main = ds.find("main") or ds.find("article") or ds
                txt = re.sub(r"\s+", " ", main.get_text(" ", strip=True))
                mo = re.search(r"Objeto[:\s]+(.+?)(?:Modalidade|Abertura|Data|Situa|Edital|Anexo|$)",
                               txt, re.IGNORECASE)
                objeto = (mo.group(1).strip()[:1500] if mo else txt[:600])
                # A data vem como "DATA e HORÁRIO: 27/07/2026" (data de sessão): o
                # rótulo tem texto entre a palavra e a data ("e HORÁRIO") e há
                # espaços-zero (​), por isso o regex antigo ([:\s]+) falhava.
                md = re.search(
                    r"(?:abertura|sess[aã]o|data|hor[aá]rio)[^0-9]{0,20}([0-3]?\d/[0-1]?\d/20\d{2})",
                    txt, re.IGNORECASE)
                data = md.group(1) if md else ""
                for a in main.find_all("a", href=True):
                    h = a["href"]
                    if h.lower().endswith(".pdf") and h not in [d["url"] for d in docs]:
                        docs.append({"label": a.get_text(" ", strip=True)[:80], "url": h})
            except Exception as e:
                print(f"[bdmg] detalhe falhou {url}: {str(e)[:50]}")
        rows.append({
            "id": url.rstrip("/").split("/")[-1], "fonte": FONTE,
            "tipo": _modalidade(titulo), "titulo": titulo, "objeto": objeto or titulo,
            "link": url, "data": data, "ano": ano,
            "documentos": json.dumps(docs, ensure_ascii=False) if docs else "",
        })

    df = pd.DataFrame(rows)
    out = dest_dir / "bdmg_editais_licitacoes.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"BDMG: {len(df)} licitações -> {out}")
    return out


if __name__ == "__main__":
    print(collect_bdmg())
