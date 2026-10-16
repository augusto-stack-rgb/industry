"""Armazenamento de arquivos das pipelines em disco local (substitui o MinIO).

Layout: ``$INDUSTRY_DATA_DIR/<pipeline>/<run_id>/<arquivo>``. As funções devolvem
a "chave" relativa ao diretório base, no mesmo espírito do ``minio_utils`` de origem.
"""

from __future__ import annotations

import os
from pathlib import Path


def base_dir() -> Path:
    p = Path(os.environ.get("INDUSTRY_DATA_DIR", "/opt/industry/data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def object_key(pipeline: str, run_id: str, filename: str) -> str:
    return f"{pipeline}/{run_id}/{filename}"


def path_of(key: str) -> Path:
    return base_dir() / key


def upload_bytes(data: bytes, pipeline: str, run_id: str, filename: str) -> str:
    key = object_key(pipeline, run_id, filename)
    p = path_of(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return key


def upload_file(local_path: str, pipeline: str, run_id: str, filename: str) -> str:
    return upload_bytes(Path(local_path).read_bytes(), pipeline, run_id, filename)


def write_fixed(key: str, data: bytes) -> Path:
    """Grava num caminho fixo (sobrescreve) — usado por relatórios "mais recentes"."""
    p = path_of(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def download_bytes(key: str) -> bytes:
    return path_of(key).read_bytes()
