"""Aplica os arquivos schema.sql das pipelines (CREATE ... IF NOT EXISTS)."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from industry.shared.connections import get_sqlalchemy_engine


def aplicar(sql_path: Path, conn_id: str) -> None:
    engine = get_sqlalchemy_engine(conn_id)
    try:
        with engine.begin() as conn:
            conn.execute(text(Path(sql_path).read_text(encoding="utf-8")))
    finally:
        engine.dispose()
