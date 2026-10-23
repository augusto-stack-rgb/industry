"""Coletor PNCP — CPSI (LC 182/2021) com proposta aberta, sem janela de publicação.

O ``pncp_abertas`` lê só o que foi publicado nos últimos dias, e o bronze corta o
que foi publicado há mais de 7. CPSI fica aberto 30 dias ou mais (art. 13, §2º,
da LC 182), então um edital publicado há três semanas e ainda aberto nunca
entrava: foi o caso da SEFAZ-PI (CPSI 001/2026, modelos preditivos de risco
fiscal, aceita pessoa física), achado à mão em 20/10/2026.

Aqui a busca do PNCP filtra só por "recebendo proposta", com termos de CPSI, e
fica o que de fato é CPSI. Sem ``data`` (a publicação vai em ``publicado_em``),
vale a primeira coleta; a fonte e o id são os do PNCP (número de controle), então
o que já entrou por outro coletor não duplica.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import DEST_DIR, PNCP_ABERTAS_PAUSA_S, PNCP_CPSI_TERMOS
from industry.licitacoes.net import ColetorBloqueado

BUSCA = "https://pncp.gov.br/api/search/"
_RX_CPSI = re.compile(r"(?i)solu[çc][ãa]o inovadora|solu[çc][õo]es inovadoras|\bcpsi\b|182/2021|"
                      r"lei complementar n?[º°.]?\s*182|startups?\b|teste de solu[çc]")
_RX_PF = re.compile(r"(?i)pessoas?\s+f[íi]sicas?")


def _get(s: requests.Session, params: dict) -> dict:
    for i in range(6):
        try:
            r = s.get(BUSCA, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(10 * (i + 1))
    raise ColetorBloqueado(f"PNCP busca: falhou para {params.get('q')!r}")


_RX_PROBLEMA = re.compile(r"(?i)(problema p[úu]blico|desafio\s*0?\d+\s*[–—:-]|\"como (?:podemos|conceber)|"
                          r"micro ?desafios?|resultados esperados|modelos? preditiv|intelig[êe]ncia artificial|"
                          r"aprendizado de m[áa]quina|machine learning|ci[êe]ncia de dados|an[áa]lise de dados|"
                          r"s[ée]ries? temporais|previs[ãa]o de)")
_RX_TR = re.compile(r"(?i)termo de refer|tr|desafio|edital|etp|estudo t[ée]cnico")


def _desafios_no_pdf(s: requests.Session, controle: str, limite: int = 2500) -> str:
    """Trechos dos PDFs (TR e edital) que descrevem o problema de cada desafio.

    Em CPSI o objeto é genérico ("serviços de desenvolvimento ... 2 desafios") e o
    que diz se o desafio é de ML está no TR: no da SEFAZ-PI, "ausência de modelos
    preditivos calibrados". Sem isto o LLM pontuava pelo objeto vago (38/55).
    """
    m = re.match(r"(\d{14})-\d-0*(\d+)/(\d{4})", controle or "")
    if not m:
        return ""
    cnpj, seq, ano = m.groups()
    try:
        import pymupdf
        arqs = s.get(f"https://pncp.gov.br/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos",
                     timeout=60).json()
    except Exception:  # noqa: BLE001 — sem PDF fica o objeto da listagem
        return ""
    trechos: list[str] = []
    for a in [a for a in arqs if _RX_TR.search(a.get("titulo", ""))][:5]:
        try:
            r = s.get(a["url"], timeout=120)
            if r.content[:5] != b"%PDF-":
                continue
            with pymupdf.open(stream=r.content, filetype="pdf") as doc:
                t = re.sub(r"\s+", " ", " ".join(doc[i].get_text() for i in range(min(len(doc), 15))))
        except Exception:  # noqa: BLE001
            continue
        for mm in _RX_PROBLEMA.finditer(t):
            trecho = t[mm.start():mm.start() + 450]
            if not any(trecho[:80] in x for x in trechos):
                trechos.append(trecho)
        time.sleep(PNCP_ABERTAS_PAUSA_S)
    return " | ".join(trechos[:8])[:limite]


def _linha(it: dict) -> dict:
    url = it.get("item_url") or ""
    texto = f"{it.get('title') or ''} {it.get('description') or ''}"
    return {
        "fonte": "PNCP",
        "tipo_documento": "edital",
        "numero_controle_pncp": it.get("numero_controle_pncp", ""),
        "tipo": "CPSI (LC 182/2021)",
        "categoria": "CPSI",
        "titulo": it.get("title", ""),
        "objeto": (it.get("description") or "").strip(),
        "situacao": it.get("situacao_nome", ""),
        "publicado_em": (it.get("data_publicacao_pncp") or "")[:10],
        "data_encerramento_proposta": (it.get("data_fim_vigencia") or "")[:19],
        "valor": it.get("valor_global") or "",
        "orgao": it.get("orgao_nome", ""),
        "orgao_cnpj": it.get("orgao_cnpj", ""),
        "uf": it.get("uf", ""),
        "municipio": it.get("municipio_nome", ""),
        "ano": str(it.get("ano", "")),
        # a busca devolve "/compras/<cnpj>/<ano>/<seq>"; a página pública é /app/editais/...
        "link": re.sub(r"^/(?:compras|editais)/", "https://pncp.gov.br/app/editais/", url),
        "pessoa_fisica": "sim" if _RX_PF.search(texto) else "nao",
        "filtros_usados": "cpsi",
    }


def collect_pncp_cpsi(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"
    achados: dict[str, dict] = {}
    for termo in PNCP_CPSI_TERMOS:  # noqa: B007
        for pagina in (1, 2, 3):
            d = _get(s, {"q": termo, "tipos_documento": "edital", "ordenacao": "-data",
                         "pagina": pagina, "tam_pagina": 50, "status": "recebendo_proposta"})
            itens = d.get("items") or []
            for it in itens:
                texto = f"{it.get('title') or ''} {it.get('description') or ''}"
                if _RX_CPSI.search(texto) and it.get("numero_controle_pncp"):
                    achados[it["numero_controle_pncp"]] = _linha(it)
            time.sleep(PNCP_ABERTAS_PAUSA_S)
            if len(itens) < 50:
                break
    for ln in achados.values():
        desafios = _desafios_no_pdf(s, ln["numero_controle_pncp"])
        if desafios:
            ln["objeto"] = f"{ln['objeto']} — Desafios (do TR): {desafios}"[:4000]
    df = pd.DataFrame(list(achados.values()))
    if df.empty:
        df = pd.DataFrame(columns=["fonte", "numero_controle_pncp", "objeto", "link"])
    out = dest_dir / "pncp_cpsi.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"PNCP CPSI abertos: {len(df)} -> {out}")
    return out


if __name__ == "__main__":
    print(collect_pncp_cpsi())
