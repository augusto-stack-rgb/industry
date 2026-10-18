"""Coletor Petronect — oportunidades da Petrobras, Transpetro e subsidiárias.

As estatais da Lei 13.303 licitam pelo regulamento próprio; a Petrobras usa o
portal Petronect, e a maior parte não aparece no PNCP. A página pública "Lista de
Oportunidades Abertas para propostas" carrega tudo de um serviço OData do SAP,
num JSON único (~300 oportunidades com itens, notas e anexos em 17/10/2026).

Ficam as que casam com os temas (``termos.py``) no objeto, na descrição ou nos
itens. O detalhe de cada oportunidade só abre dentro do portal, por isso o link
é a lista pública; os anexos têm URL direta de download.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import (
    DEST_DIR, PETRONECT_ANEXO_URL, PETRONECT_LISTA_URL, PETRONECT_PAGINA, REQUEST_HEADERS,
)
from industry.licitacoes.net import get_json
from industry.licitacoes.termos import termos_encontrados

FONTE = "Petronect - Petrobras e subsidiárias"

# OPPORT_TYPE: 1ª letra L = licitação, D = dispensa, P = pré-qualificação/consulta
_TIPO = {"L": "Licitação (Lei 13.303)", "D": "Contratação direta (Lei 13.303)",
         "P": "Pré-qualificação/consulta (Lei 13.303)"}
_MODO_DISPUTA = {"01": "Aberto", "02": "Fechado", "03": "Aberto e fechado"}


def _texto(op: dict) -> str:
    itens = " ".join(i.get("ITEM_DESC", "") for i in op.get("ITEMS") or [])
    return " ".join([op.get("OPPORT_DESCR", ""), op.get("DESC_OBJ_CONTRAT", ""),
                     op.get("DESC_DETAIL", ""), itens])


def _quando(d: str, h: str) -> str:
    return f"{d}T{h or '00:00:00'}" if d and d != "0000-00-00" else ""


def _linha(op: dict, termos: list[str]) -> dict:
    anexos = [{"titulo": a.get("DESCRIPTION", ""),
               "url": PETRONECT_ANEXO_URL.format(id=a.get("PHIO_OBJID", ""))}
              for a in op.get("ANEXOS") or [] if a.get("PHIO_OBJID")]
    edital = next((a for a in anexos if a["titulo"].lower().startswith("edital")), None)
    return {
        "id": op.get("OPPORT_NUM", ""),
        "fonte": FONTE,
        "tipo_documento": "edital",
        "tipo": _TIPO.get(str(op.get("OPPORT_TYPE", ""))[:1], "Lei 13.303"),
        "numero": op.get("OPPORT_NUM", ""),
        "objeto": (op.get("DESC_OBJ_CONTRAT") or op.get("OPPORT_DESCR") or "").strip(),
        "situacao": op.get("STATUS_DESC", ""),
        "data_publicacao": op.get("POSTING_DATE", ""),
        "data_abertura": _quando(op.get("START_DATE", ""), op.get("START_HOUR", "")),
        "data_encerramento_proposta": _quando(op.get("END_DATE", ""), op.get("END_HOUR", "")),
        "data_sessao": _quando(op.get("OPEN_DATE", ""), op.get("OPEN_HOUR", "")),
        "modo_disputa": _MODO_DISPUTA.get(op.get("DISPUTE_MODE", ""), ""),
        "orgao": op.get("COMPANY_DESC", ""),
        "link": PETRONECT_PAGINA,
        "edital_url": edital["url"] if edital else "",
        "edital_nome": edital["titulo"] if edital else "",
        "documentos": json.dumps(anexos, ensure_ascii=False) if anexos else "",
        "filtros_usados": "; ".join(termos),
    }


def collect_petronect(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({**REQUEST_HEADERS, "Accept": "application/json"})
    d = get_json(PETRONECT_LISTA_URL, fonte="Petronect", session=s, timeout=120)
    ops = json.loads(d["d"]["EvXml"]).get("TAB") or []

    rows = []
    for op in ops:
        termos = termos_encontrados(_texto(op))
        if termos:
            rows.append(_linha(op, termos))
    df = pd.DataFrame(rows)
    out = dest_dir / "petronect.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Petronect: {len(ops)} oportunidades abertas; {len(df)} casam com os temas -> {out}")
    return out


if __name__ == "__main__":
    print(collect_petronect())
