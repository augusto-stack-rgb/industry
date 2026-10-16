"""Saúde de cada coleta: o que o coletor viu, gravado em monitor.coleta_noticias.

Fontes quebravam em silêncio — a task terminava "success" com 0 linhas
(JornalDoComercio de abr a out/2026, SUDENE nunca gravou nada) porque cada
coletor descarta artigo sem texto/título e segue. Aqui fica registrado, por
execução, quantos links a listagem deu e quantos artigos vieram sem título,
sem texto ou sem data — é a matéria-prima do INTERNO_MonitorFontes e do
painel do Grafana.

Os coletores só chamam ``links()`` / ``artigos()``; quem abre e grava o
registro é ``executar.executar_coleta``. Fora dela (testes, dry-run) as
chamadas não fazem nada.
"""

import contextvars
import logging
import re
import statistics

# Página de bloqueio anti-robô (Cloudflare/DataDome) tem título e <p> e
# entraria como notícia: Brasil Energia devolveu 60 "Sorry, you have been
# blocked" num teste de 07/10/2026.
PADRAO_BLOQUEIO = (
    r"you have been blocked|just a moment|access denied|attention required"
    r"|are you a robot|verify you are human|request rejected|requested url was rejected"
)

_atual: contextvars.ContextVar = contextvars.ContextVar("saude_coleta", default=None)

DDL = """
CREATE SCHEMA IF NOT EXISTS monitor;
CREATE TABLE IF NOT EXISTS monitor.coleta_noticias (
    id               BIGSERIAL PRIMARY KEY,
    executado_em     TIMESTAMP NOT NULL DEFAULT NOW(),
    run_id           TEXT,
    fonte            TEXT NOT NULL,
    tipo             TEXT,
    url              TEXT,
    itens_listagem   INTEGER,   -- links/entradas que a listagem devolveu
    ja_existentes    INTEGER,   -- pulados por já estarem em noticias_raw
    artigos          INTEGER,   -- artigos montados (antes dos filtros)
    com_titulo       INTEGER,
    com_texto        INTEGER,
    com_data         INTEGER,   -- data vinda do site (não o fallback "hoje")
    bloqueios        INTEGER,   -- páginas anti-robô
    gravados         INTEGER,   -- linhas que chegaram a noticias_raw
    texto_mediano    INTEGER,
    duracao_s        REAL,
    erro             TEXT
);
CREATE INDEX IF NOT EXISTS coleta_noticias_fonte_em
    ON monitor.coleta_noticias (fonte, executado_em DESC);
"""


class SaudeColeta:
    def __init__(self, fonte: str, tipo: str, url: str):
        self.fonte, self.tipo, self.url = fonte, tipo, url
        self.itens_listagem = None
        self.ja_existentes = None
        self.artigos = None
        self.com_titulo = None
        self.com_texto = None
        self.com_data = None
        self.bloqueios = None
        self.gravados = None
        self.texto_mediano = None
        self.duracao_s = None
        self.erro = None


def iniciar(fonte: str, tipo: str, url: str) -> SaudeColeta:
    s = SaudeColeta(fonte, tipo, url)
    _atual.set(s)
    return s


def links(n: int, ja_existentes: int | None = None) -> None:
    s = _atual.get()
    if s is not None:
        s.itens_listagem = int(n)
        if ja_existentes is not None:
            s.ja_existentes = int(ja_existentes)


def _vazio(v) -> bool:
    return v is None or str(v).strip() in ("", "None", "nan")


def artigos(rows: list[dict]) -> None:
    """Resumo dos artigos montados pelo coletor, antes de qualquer filtro."""
    s = _atual.get()
    if s is None:
        return
    s.artigos = len(rows)
    s.com_titulo = sum(not _vazio(r.get("titulo")) for r in rows)
    s.com_texto = sum(not _vazio(r.get("texto")) for r in rows)
    s.com_data = sum(not _vazio(r.get("data")) for r in rows)
    s.bloqueios = sum(
        bool(re.search(PADRAO_BLOQUEIO, str(r.get("titulo") or ""), re.I)) for r in rows
    )
    tamanhos = [len(str(r["texto"])) for r in rows if not _vazio(r.get("texto"))]
    s.texto_mediano = int(statistics.median(tamanhos)) if tamanhos else None


def gravar(s: SaudeColeta, run_id: str | None, engine) -> None:
    """Grava o registro. Nunca levanta: o monitor não pode derrubar a coleta."""
    from sqlalchemy import text

    try:
        with engine.begin() as conn:
            conn.execute(text(DDL))
            conn.execute(
                text(
                    """
                    INSERT INTO monitor.coleta_noticias
                        (run_id, fonte, tipo, url, itens_listagem, ja_existentes, artigos,
                         com_titulo, com_texto, com_data, bloqueios, gravados,
                         texto_mediano, duracao_s, erro)
                    VALUES
                        (:run_id, :fonte, :tipo, :url, :itens_listagem, :ja_existentes, :artigos,
                         :com_titulo, :com_texto, :com_data, :bloqueios, :gravados,
                         :texto_mediano, :duracao_s, :erro)
                    """
                ),
                {"run_id": run_id, **{k: v for k, v in vars(s).items()}},
            )
    except Exception as e:
        logging.error(f"[saude] falha ao gravar monitor.coleta_noticias ({s.fonte}): {e}")
