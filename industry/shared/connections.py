"""Resolução de conexões e segredos por variável de ambiente.

Substitui as Airflow Connections do repositório de origem (bateleur-pipelines).
Os nomes das funções foram mantidos para o código portado não precisar mudar:

- ``get_sqlalchemy_engine(LICITACOES_PG)`` -> ``LICITACOES_PG_URL``
- ``get_sqlalchemy_engine(NOTICIAS_PG)``   -> ``NOTICIAS_PG_URL``
  (ambos caem em ``SUPABASE_DB_URL`` quando não definidos)
- ``get_sqlalchemy_engine(MERCADO_PG)``    -> ``MERCADO_PG_URL``
  (cai no Postgres local do compose, NUNCA no Supabase: o volume não cabe no plano grátis)
- chaves de LLM (``GROQ_/CEREBRAS_/MISTRAL_API_KEY_N``) -> ``industry.shared.llm``
- ``get_selenium_command_executor()``      -> ``SELENIUM_URL``

Tudo vem do ``.env`` da raiz do repositório (``carregar_env``); veja ``.env.example``.
"""

from __future__ import annotations

import os
from pathlib import Path

LICITACOES_PG = "LICITACOES_PG_URL"
NOTICIAS_PG = "NOTICIAS_PG_URL"
MERCADO_PG = "MERCADO_PG_URL"
MERCADO_PG_PADRAO = "postgresql://industry:industry@localhost:5433/industry"


def get_pg_url(conn_id: str) -> str:
    carregar_env()
    padrao = MERCADO_PG_PADRAO if conn_id == MERCADO_PG else os.environ.get("SUPABASE_DB_URL")
    url = os.environ.get(conn_id) or padrao
    if not url:
        raise RuntimeError(f"{conn_id} (ou SUPABASE_DB_URL) não definida — ver .env.example.")
    # SQLAlchemy com psycopg2; a string do Supabase vem como postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def get_sqlalchemy_engine(conn_id: str, **engine_kwargs):
    """Engine SQLAlchemy do banco de uma pipeline."""
    from sqlalchemy import create_engine

    engine_kwargs.setdefault("pool_pre_ping", True)
    return create_engine(get_pg_url(conn_id), **engine_kwargs)


def get_selenium_command_executor(path: str = "") -> str:
    """URL do Selenium remoto (container ``selenium`` do docker-compose).

    ``path`` permite anexar sufixos como ``/wd/hub`` exigidos por alguns drivers.
    """
    carregar_env()
    base = os.environ.get("SELENIUM_URL", "http://localhost:4444").rstrip("/")
    return f"{base}{path}"


_ENV_CARREGADO = False


def carregar_env(caminho: Path | None = None) -> None:
    """Carrega o ``.env`` da raiz do repositório no ``os.environ``.

    Não sobrescreve variáveis já definidas (o docker-compose e o ``dagster dev``
    também injetam o ``.env``; o que vier deles prevalece). Aceita ``CHAVE=valor``,
    linhas em branco e comentários ``#``; aspas em volta do valor são removidas.
    """
    global _ENV_CARREGADO
    if _ENV_CARREGADO and caminho is None:
        return
    p = caminho or Path(__file__).resolve().parents[2] / ".env"
    if p.is_file():
        for linha in p.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            k, v = k.strip(), v.strip()
            if " #" in v and not v.startswith(("'", '"')):
                v = v.split(" #", 1)[0].strip()
            v = v.strip("'\"")
            if k and v and k not in os.environ:
                os.environ[k] = v
    _ENV_CARREGADO = True
