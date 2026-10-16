"""Download + extração de texto dos anexos (PDF) das licitações novas.

Para cada licitação nova, junta as URLs de anexo (edital_url / documentos /
arquivos_baixados do bronze), baixa cada PDF, extrai o texto (PyMuPDF) e:
  - sobe o PDF     em  <pipeline>/<run_id>/anexos/<fonte>/<arquivo>.pdf
  - sobe o texto   em  <pipeline>/<run_id>/anexos_texto/<md5>.txt
Retorna {id_unificado: texto_concatenado_dos_anexos} para o filtro do gold.

Downloads diretos via requests (a maioria dos editais é link .pdf direto). Fontes
cujo anexo só é acessível por navegador ficam a cargo do próprio coletor.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re

import requests

from industry.licitacoes.config import (
    REQUEST_HEADERS, REQUEST_TIMEOUT, ANEXOS_PREFIX, ANEXOS_TEXTO_PREFIX, SCHEMA, TB_BRONZE,
)
from industry.licitacoes import db
from industry.shared import storage

logger = logging.getLogger(__name__)


def _urls_da_linha(row: dict) -> list[str]:
    urls = []
    u = str(row.get("edital_url") or "").strip()
    if u.lower().startswith("http"):
        urls.append(u)
    for campo in ("documentos", "arquivos_baixados"):
        raw = str(row.get(campo) or "").strip()
        if not raw:
            continue
        if raw.startswith("["):
            try:
                for d in json.loads(raw):
                    v = d.get("url") if isinstance(d, dict) else d
                    if str(v).lower().startswith("http"):
                        urls.append(str(v))
            except Exception:
                pass
        else:
            for part in re.split(r"[;\n]+", raw):
                if part.strip().lower().startswith("http"):
                    urls.append(part.strip())
    # saneia URLs com segmento duplicado (ex.: Banestes /publicacoes_legais/publicacoes_legais/)
    urls = [re.sub(r"/(publicacoes_legais)/\1/", r"/\1/", u) for u in urls]
    # só PDFs, dedup preservando ordem
    pdfs = [u for u in dict.fromkeys(urls) if u.lower().split("?")[0].endswith(".pdf")]
    return pdfs


def _baixar_pdf(session: requests.Session, url: str) -> bytes | None:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT, stream=True)
        r.raise_for_status()
        data = r.content
        return data if data[:5] == b"%PDF-" else None
    except Exception as e:
        logger.warning(f"[anexo] falha {url}: {str(e)[:60]}")
        return None


def _extrair_texto(pdf_bytes: bytes) -> str:
    try:
        import fitz  # PyMuPDF
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            return "\n".join(p.get_text() for p in doc)
    except Exception as e:
        logger.warning(f"[anexo] extração falhou: {str(e)[:60]}")
        return ""


def textos_por_id(ids_novos: list[str], dag_id: str, run_id: str) -> dict[str, str]:
    """Baixa/extrai os anexos das licitações novas; devolve {id: texto}."""
    if not ids_novos:
        return {}
    import pandas as pd
    engine = db.get_engine()
    # busca em lotes (IN-list gigante quebra/lento)
    frames = []
    for k in range(0, len(ids_novos), 1000):
        chunk = ids_novos[k:k + 1000]
        lista = "','".join(str(i).replace("'", "''") for i in chunk)
        with engine.connect() as conn:
            frames.append(pd.read_sql(
                f"SELECT id_unificado, fonte, edital_url, documentos, arquivos_baixados "
                f"FROM {SCHEMA}.{TB_BRONZE} WHERE id_unificado IN ('{lista}')", conn))
    bronze = pd.concat(frames, ignore_index=True).fillna("") if frames else pd.DataFrame()
    if bronze.empty:
        return {}

    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)
    out: dict[str, str] = {}
    n_pdf = 0
    for _, row in bronze.iterrows():
        idu = row["id_unificado"]
        fonte = re.sub(r"[^\w.-]+", "_", str(row.get("fonte") or "fonte"))[:40]
        textos = []
        for url in _urls_da_linha(dict(row)):
            data = _baixar_pdf(session, url)
            if not data:
                continue
            n_pdf += 1
            nome = re.sub(r"[^\w.-]+", "_", url.split("/")[-1].split("?")[0])[:120] or "anexo.pdf"
            storage.upload_bytes(data, dag_id, run_id, f"{ANEXOS_PREFIX}/{fonte}/{nome}")
            texto = _extrair_texto(data)
            if texto:
                key = hashlib.md5(url.encode()).hexdigest()
                storage.upload_bytes(texto.encode("utf-8"), dag_id, run_id,
                                         f"{ANEXOS_TEXTO_PREFIX}/{key}.txt")
                textos.append(texto)
        if textos:
            out[idu] = "\n".join(textos)
    logger.info(f"[anexos] {n_pdf} PDFs baixados; {len(out)} licitações com texto de anexo")
    return out
