"""Coletor PRODOC — consultor individual (PESSOA FÍSICA, sem CNPJ) em projetos de
cooperação internacional executados com ministérios brasileiros.

UNESCO, IICA, PNUD, OEI, OPAS e FAO contratam "consultor por produto" para os
projetos de cooperação (PRODOC) que executam com os ministérios. Os editais
saem nas páginas dos ministérios no gov.br, um PDF por edital, só com "Edital
NN/AAAA" no link: objeto e prazo estão dentro do PDF. A API REST do Plone do
gov.br é fechada (401), então o coletor lê o HTML e abre cada PDF.

Ficam todos os editais com prazo aberto (ou sem prazo achado), sem filtro de
tema: são poucas dezenas por ano, a maioria de política pública, e o score de
relevância do pipeline ordena. Os termos de tema que aparecerem vão em
``filtros_usados``. Todos são pessoa física: ``pessoa_fisica = "sim"``.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import (DEST_DIR, PRODOC_MAX_PDFS, PRODOC_PAGINAS,
                                        REQUEST_HEADERS)
from industry.licitacoes.net import ColetorBloqueado
from industry.licitacoes.termos import termos_exterior

FONTE = "PRODOC - Consultor individual em projetos de cooperação internacional"

ORGANISMOS = ["UNESCO", "IICA", "PNUD", "OEI", "OPAS", "FAO", "UNODC", "OIT", "UNICEF", "UNFPA"]
_RX_NUMERO = re.compile(r"edital\s*(?:n[ºo°.]*\s*)?(\d{1,4})\s*[/_-]?\s*(\d{4})", re.I)
_RX_PROJETO = re.compile(r"\b(\d{3}\s?BRZ\s?\d{3,4}|BRA/[A-Z]{2,6}/\d{2}/\d{3}|BRA/\d{2}/\d{3})\b")
_RX_DATA = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
                                     "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}
_RX_DATA_EXTENSO = re.compile(r"\b(\d{1,2})[ºo°]?\s+de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|"
                              r"agosto|setembro|outubro|novembro|dezembro)\s+de\s+(\d{4})", re.I)
_RX_PRAZO = re.compile(r"(at[ée]|prazo|encerra|inscri[çc]|candidatura|envio|recebimento)", re.I)
_RX_OBJETO = re.compile(
    r"(?:objeto|objetivo da consultoria|objetivo|finalidade)\s*(?:do edital|da contrata[çc][ãa]o)?"
    r"\s*[:\-–]?\s*(.{40,700}?)(?:\n\s*\n|\n\s*\d+[.)]\s|$)", re.I | re.S)
_RX_CONTRATACAO = re.compile(r"((?:contrata[çc][ãa]o|sele[çc][ãa]o) de consultor.{20,500}?\.)", re.I | re.S)


def _organismo(*textos: str) -> str:
    junto = " ".join(textos).upper()
    return next((o for o in ORGANISMOS if o in junto), "")


def _listar(sess: requests.Session) -> list[tuple[str, str, str, str]]:
    """[(ministerio, pagina, texto do link, url do pdf)] das paginas configuradas."""
    hoje = date.today()
    achados, falhas = [], []
    for ministerio, modelo in PRODOC_PAGINAS:
        anos = [hoje.year, hoje.year - 1] if "{ano}" in modelo else [None]
        for ano in anos:
            pagina = modelo.format(ano=ano) if ano else modelo
            try:
                r = sess.get(pagina, timeout=60)
                r.raise_for_status()
            except requests.RequestException as e:
                falhas.append(f"{pagina}: {type(e).__name__}")
                continue
            sopa = BeautifulSoup(r.text, "html.parser")
            corpo = sopa.select_one("#content") or sopa
            for a in corpo.select("a[href]"):
                url = urljoin(pagina, a["href"])
                texto = a.get_text(" ", strip=True)
                if not url.lower().split("?")[0].endswith(".pdf"):
                    continue
                if "edital" not in (texto + url).lower():
                    continue
                achados.append((ministerio, pagina, texto, url))
    if not achados and falhas:
        raise ColetorBloqueado("PRODOC: " + "; ".join(falhas))
    vistos, saida = set(), []
    for item in achados:
        if item[3] not in vistos:
            vistos.add(item[3])
            saida.append(item)
    return saida


def _texto_pdf(sess: requests.Session, url: str, paginas: int = 30) -> str:
    """Texto do PDF inteiro (editais curtos): o prazo costuma estar no fim."""
    try:
        r = sess.get(url, timeout=90)
        r.raise_for_status()
        if r.content[:5] != b"%PDF-":
            # no Plone do gov.br o link .pdf as vezes abre a pagina do arquivo;
            # o binario fica em /@@download/file
            r = sess.get(url.rstrip("/") + "/@@download/file", timeout=90)
            r.raise_for_status()
        if r.content[:5] != b"%PDF-":
            return ""
        import pymupdf
        with pymupdf.open(stream=r.content, filetype="pdf") as doc:
            return "\n".join(doc[i].get_text() for i in range(min(paginas, len(doc))))
    except Exception:
        return ""


def _prazo(texto: str) -> str:
    """Maior data que aparece logo depois de 'até', 'prazo', 'inscrição'..."""
    datas = []
    for m in _RX_PRAZO.finditer(texto):
        trecho = texto[m.end():m.end() + 160]
        for d in _RX_DATA.finditer(trecho):
            try:
                datas.append(date(int(d.group(3)), int(d.group(2)), int(d.group(1))))
            except ValueError:
                pass
        for d in _RX_DATA_EXTENSO.finditer(trecho):
            try:
                datas.append(date(int(d.group(3)), MESES[d.group(2).lower().replace("ç", "c")],
                                  int(d.group(1))))
            except ValueError:
                pass
    return max(datas).isoformat() if datas else ""


def _objeto(texto: str) -> str:
    m = _RX_OBJETO.search(texto) or _RX_CONTRATACAO.search(texto)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:700] if m else ""


def _linha(ministerio, pagina, rotulo, url, texto) -> dict:
    numero = _RX_NUMERO.search(f"{rotulo} {url.rsplit('/', 1)[-1]}")
    numero = f"{int(numero.group(1)):02d}/{numero.group(2)}" if numero else rotulo
    org = _organismo(url, rotulo, texto[:3000])
    projeto = _RX_PROJETO.search(texto)
    objeto = _objeto(texto)
    return {
        "id": hashlib.sha1(url.encode()).hexdigest()[:16],
        "fonte": FONTE,
        "tipo_documento": "edital",
        "tipo": "Consultor individual (produto)",
        "categoria": "Pessoa física (consultor individual)",
        "pessoa_fisica": "sim",
        "numero": f"Edital {numero}",
        "processo": projeto.group(1).replace(" ", "") if projeto else "",
        "titulo": f"{org or 'Cooperação'} {ministerio} Edital {numero}: {objeto[:140]}".strip(),
        "objeto": objeto or rotulo,
        "data_encerramento_proposta": _prazo(texto),
        "orgao": f"{org} / {ministerio}" if org else ministerio,
        "pais": "Brasil",
        "link": url,
        "edital_url": url,
        "site_projeto": pagina,
    }


def collect_prodoc(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    sess = requests.Session()
    sess.headers.update(REQUEST_HEADERS)
    editais = _listar(sess)
    # mais novos primeiro: ano e numero do edital no link
    def chave(item):
        m = _RX_NUMERO.search(f"{item[2]} {item[3].rsplit('/', 1)[-1]}")
        return (int(m.group(2)), int(m.group(1))) if m else (0, 0)
    editais.sort(key=chave, reverse=True)

    hoje = date.today().isoformat()
    rows, lidos = [], 0
    for ministerio, pagina, rotulo, url in editais[:PRODOC_MAX_PDFS]:
        texto = _texto_pdf(sess, url)
        lidos += 1
        if not texto:
            continue
        linha = _linha(ministerio, pagina, rotulo, url, texto)
        if linha["data_encerramento_proposta"] and linha["data_encerramento_proposta"] < hoje:
            continue      # selecao encerrada
        if not linha["data_encerramento_proposta"] and chave((ministerio, pagina, rotulo, url))[0] < date.today().year:
            continue      # sem prazo achado e de ano anterior: quase certamente encerrado
        # sem filtro de tema (ver docstring); os termos achados ficam anotados
        linha["filtros_usados"] = "; ".join(termos_exterior(f"{linha['objeto']} {texto[:4000]}"))
        linha["data_coleta"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        rows.append(linha)
    df = pd.DataFrame(rows)
    out = dest_dir / "prodoc.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"PRODOC: {len(editais)} editais nas paginas, {lidos} PDFs lidos; {len(df)} abertos -> {out}")
    return out


if __name__ == "__main__":
    print(collect_prodoc())
