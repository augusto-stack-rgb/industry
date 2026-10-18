"""Coletor Banco Mundial — avisos de aquisição dos projetos financiados.

Os projetos do Banco Mundial compram pelas regras do banco (Procurement
Regulations), abertas a empresas e consultores de qualquer país membro, o Brasil
incluso. A API pública de busca (``search.worldbank.org/api/v2/procnotices``)
aceita termo livre; cada termo de ``BANCO_MUNDIAL_TERMOS`` vira uma busca, das
mais recentes para as mais antigas.

Ficam os avisos que ainda aceitam proposta ou manifestação de interesse
(``BANCO_MUNDIAL_TIPOS`` com prazo futuro) e que casam com os temas em
inglês/espanhol/francês/português (``termos.termos_exterior``). O grupo de
aquisição CS (consulting services) é o que mais interessa: é serviço de
consultoria, inclusive de consultor individual.
"""
from __future__ import annotations

import html
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import (
    BANCO_MUNDIAL_API, BANCO_MUNDIAL_TERMOS, BANCO_MUNDIAL_TIPOS, DEST_DIR, REQUEST_HEADERS,
)
from industry.licitacoes.net import get_json
from industry.licitacoes.termos import termos_exterior

FONTE = "Banco Mundial - Avisos de aquisição"
LINK = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
POR_TERMO = 100
GRUPOS = {"CS": "Consultoria", "GO": "Bens", "NC": "Serviços (não consultoria)",
          "CW": "Obras"}


def _sem_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _linha(n: dict, termos: list[str]) -> dict:
    prazo = (n.get("submission_deadline_date") or "")[:10]
    hora = n.get("submission_deadline_time") or ""
    descricao = _sem_html(n.get("bid_description") or "")
    return {
        "id": n.get("id", ""),
        "fonte": FONTE,
        "tipo_documento": "edital",
        "tipo": f"{n.get('notice_type', '')} — {n.get('procurement_method_name', '')}".strip(" —"),
        "categoria": GRUPOS.get(n.get("procurement_group", ""), n.get("procurement_group", "")),
        "numero": n.get("bid_reference_no", ""),
        "titulo": descricao[:200],
        "objeto": f"{descricao} — Projeto: {n.get('project_name', '')}".strip(" —"),
        "observacao": _sem_html(n.get("notice_text") or "")[:3000],
        "situacao": n.get("notice_status", ""),
        "data_publicacao": (n.get("submission_date") or "")[:10],
        "data_encerramento_proposta": f"{prazo}T{hora}:00" if prazo and hora else prazo,
        "orgao": n.get("contact_organization") or n.get("project_name", ""),
        "pais": n.get("project_ctry_name", ""),
        "responsavel": n.get("contact_email", ""),
        "link": LINK.format(id=n.get("id", "")),
        "site_projeto": f"https://projects.worldbank.org/en/projects-operations/project-detail/"
                        f"{n.get('project_id', '')}",
        "filtros_usados": "; ".join(termos),
        # "Individual Consultant Selection" (INDV) contrata pessoa fisica, sem CNPJ
        "categoria": ("Pessoa física (consultor individual)"
                      if n.get("procurement_method_code") == "INDV" else "Empresa"),
        "pessoa_fisica": "sim" if n.get("procurement_method_code") == "INDV" else "nao",
    }


def collect_banco_mundial(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    hoje = date.today().isoformat()
    rows: dict[str, dict] = {}
    vistos = 0
    for termo in BANCO_MUNDIAL_TERMOS:
        d = get_json(BANCO_MUNDIAL_API, fonte="Banco Mundial", session=s, timeout=60,
                     params={"format": "json", "rows": POR_TERMO, "qterm": termo,
                             "srt": "submission_date", "order": "desc"})
        for n in d.get("procnotices") or []:
            vistos += 1
            if n.get("notice_type") not in BANCO_MUNDIAL_TIPOS:
                continue
            if (n.get("submission_deadline_date") or "")[:10] < hoje:
                continue      # prazo vencido ou sem prazo
            termos = termos_exterior(f"{n.get('bid_description', '')} "
                                     f"{_sem_html(n.get('notice_text') or '')}")
            if termos:
                rows[n["id"]] = _linha(n, termos)
        time.sleep(1)
    df = pd.DataFrame(list(rows.values()))
    out = dest_dir / "banco_mundial.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Banco Mundial: {vistos} avisos lidos; {len(df)} abertos e no tema -> {out}")
    return out


if __name__ == "__main__":
    print(collect_banco_mundial())
