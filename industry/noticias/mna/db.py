"""Camada de banco da DAG DATABASE_Noticias_OperacoesMnA (Postgres, schema noticias).

Funções puras sobre um Engine SQLAlchemy: DDL idempotente, leitura do pool de
candidatos, controle de resumo (links processados) e persistência/consolidação
das operações extraídas. Toda a escrita é do PIPELINE — o dashboard só lê.
"""

from __future__ import annotations

import json
import logging

import pandas as pd
from industry.noticias.mna import config as C
from industry.noticias.mna.pipeline import (
    agrupar_por_par_exato,
    coerce_data,
    coerce_valor,
    escolher_sobrevivente,
    mesclar,
)
from sqlalchemy import bindparam, text

log = logging.getLogger(__name__)

# Colunas da tabela de operações, na ordem canônica de inserção.
_COLS = [
    "empresa_adquirente", "empresa_alvo", "valor", "moeda", "setor", "status",
    "pais_adquirente", "pais_alvo", "descricao", "titulo_artigo", "link_artigo",
    "data_artigo", "fonte_artigo", "zs_score",
]


def garantir_tabelas(engine) -> None:
    """Cria ``mna_operations`` e ``mna_extraction_log`` se não existirem (idempotente)."""
    ddl_ops = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_OPERACOES} (
            op_id               BIGSERIAL PRIMARY KEY,
            empresa_adquirente  TEXT,
            empresa_alvo        TEXT,
            valor               DOUBLE PRECISION,
            moeda               TEXT,
            setor               TEXT,
            status              TEXT,
            pais_adquirente     TEXT,
            pais_alvo           TEXT,
            descricao           TEXT,
            titulo_artigo       TEXT,
            link_artigo         TEXT,
            data_artigo         DATE,
            fonte_artigo        TEXT,
            zs_score            DOUBLE PRECISION,
            computed_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    ddl_log = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_LOG} (
            link_artigo   TEXT PRIMARY KEY,
            n_ops         INTEGER,
            processed_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # motivo_falha/tentativas: distinguem "extraído com 0 operações" de "a chamada
    # ao LLM falhou" — sem isso o segundo caso virava um 0 silencioso e definitivo.
    # motivo_revisao é DIFERENTE de motivo_falha e não gera retentativa: as
    # operações já foram gravadas, só a validação/dedup é que não rodou. Retentar
    # o artigo nesse caso duplicaria as operações já inseridas.
    ddl_log_cols = text(f"""
        ALTER TABLE {C.TABELA_LOG}
            ADD COLUMN IF NOT EXISTS motivo_falha   TEXT,
            ADD COLUMN IF NOT EXISTS motivo_revisao TEXT,
            ADD COLUMN IF NOT EXISTS tentativas     INTEGER NOT NULL DEFAULT 1
    """)
    # Desfecho de cada rodada: o que parou por esgotamento de chave Groq (TPD)
    # tem de ficar visível na base, não só no log do Airflow.
    ddl_exec = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_EXECUCOES} (
            exec_id                  BIGSERIAL PRIMARY KEY,
            iniciado_em              TIMESTAMPTZ,
            terminado_em             TIMESTAMPTZ DEFAULT NOW(),
            status                   TEXT,
            motivo                   TEXT,
            pool_total               INTEGER,
            pool_novos               INTEGER,
            selecionados             INTEGER,
            processados              INTEGER,
            ops_aprovadas            INTEGER,
            ops_rejeitadas_validacao INTEGER,
            ops_rejeitadas_dedup     INTEGER,
            artigos_pendentes        INTEGER,
            artigos_com_falha        INTEGER,
            link_interrompido        TEXT,
            total_consolidado        INTEGER
        )
    """)
    # Coluna adicionada depois da criação original da tabela.
    ddl_exec_cols = text(f"""
        ALTER TABLE {C.TABELA_EXECUCOES}
            ADD COLUMN IF NOT EXISTS artigos_com_falha INTEGER
    """)
    # Estado ATUAL das pendências (reescrito a cada rodada): responde "o que ficou
    # sem processar agora", sem crescer indefinidamente.
    ddl_pend = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_PENDENCIAS} (
            pend_id       BIGSERIAL PRIMARY KEY,
            exec_id       BIGINT,
            link_artigo   TEXT,
            titulo_artigo TEXT,
            motivo        TEXT,
            criado_em     TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Operações descartadas pela validação/dedup: guardadas para auditoria (medir
    # a taxa de falso positivo e revisar o critério), NUNCA lidas pelo relatório.
    ddl_rej = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_REJEITADAS} (
            rej_id              BIGSERIAL PRIMARY KEY,
            motivo_rejeicao     TEXT,
            etapa               TEXT,
            empresa_adquirente  TEXT,
            empresa_alvo        TEXT,
            valor               DOUBLE PRECISION,
            moeda               TEXT,
            setor               TEXT,
            status              TEXT,
            pais_adquirente     TEXT,
            pais_alvo           TEXT,
            descricao           TEXT,
            titulo_artigo       TEXT,
            link_artigo         TEXT,
            data_artigo         DATE,
            fonte_artigo        TEXT,
            zs_score            DOUBLE PRECISION,
            computed_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    # Mescla de duplicatas (pipeline.mesclar): a linha é ATUALIZADA no lugar quando
    # uma notícia nova do mesmo deal traz status mais avançado ou o valor ausente.
    ddl_ops_cols = text(f"""
        ALTER TABLE {C.TABELA_OPERACOES}
            ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS n_noticias    INTEGER NOT NULL DEFAULT 1
    """)
    # Histórico do deal: 1 linha por notícia mesclada numa operação (pelo dedup da
    # rodada, pela consolidação ou pela limpeza retroativa). ``alteracoes`` = JSON
    # {campo: [antes, depois]}; ``divergencias`` = o que conflitou e NÃO foi aplicado.
    # ``rej_id`` liga à linha de auditoria em TABELA_REJEITADAS (quando houver) e
    # torna a mescla retroativa idempotente.
    ddl_eventos = text(f"""
        CREATE TABLE IF NOT EXISTS {C.TABELA_EVENTOS} (
            evento_id           BIGSERIAL PRIMARY KEY,
            op_id               BIGINT,
            rej_id              BIGINT,
            origem              TEXT,
            empresa_adquirente  TEXT,
            empresa_alvo        TEXT,
            valor               DOUBLE PRECISION,
            moeda               TEXT,
            status              TEXT,
            descricao           TEXT,
            titulo_artigo       TEXT,
            link_artigo         TEXT,
            data_artigo         DATE,
            fonte_artigo        TEXT,
            alteracoes          TEXT,
            divergencias        TEXT,
            criado_em           TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    ddl_eventos_idx = text(f"""
        CREATE INDEX IF NOT EXISTS mna_operations_eventos_op_id_idx
            ON {C.TABELA_EVENTOS} (op_id)
    """)
    with engine.begin() as conn:
        conn.execute(ddl_ops)
        conn.execute(ddl_ops_cols)
        conn.execute(ddl_eventos)
        conn.execute(ddl_eventos_idx)
        conn.execute(ddl_log)
        conn.execute(ddl_log_cols)
        conn.execute(ddl_rej)
        conn.execute(ddl_exec)
        conn.execute(ddl_exec_cols)
        conn.execute(ddl_pend)
    log.info("Tabelas %s, %s, %s, %s, %s e %s prontas.", C.TABELA_OPERACOES, C.TABELA_LOG,
             C.TABELA_REJEITADAS, C.TABELA_EXECUCOES, C.TABELA_PENDENCIAS, C.TABELA_EVENTOS)


def links_processados(engine) -> set[str]:
    """Links a NÃO reprocessar: extraídos com sucesso (inclusive com 0 operações).

    Exclui deliberadamente os que registraram ``motivo_falha`` e ainda têm
    tentativas disponíveis — esses voltam ao pool na próxima rodada, em vez de
    ficarem marcados como "processados, 0 operações" para sempre.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"""
                SELECT link_artigo FROM {C.TABELA_LOG}
                 WHERE link_artigo IS NOT NULL
                   AND (motivo_falha IS NULL OR tentativas >= :max_tent)
            """),
            {"max_tent": C.MAX_TENTATIVAS_ARTIGO},
        ).fetchall()
    return {r[0] for r in rows}


def carregar_pool(engine) -> pd.DataFrame:
    """Lê o pool de candidatos: notícias classificadas nos setores de M&A.

    Retorna um DataFrame com colunas ``titulo, link, data, texto, fonte, setor``,
    com ``data`` normalizada para ISO ``YYYY-MM-DD`` (a origem é VARCHAR
    ``DD/MM/YYYY``) e deduplicada por ``link`` (uma notícia pode aparecer em mais
    de um setor via linhas duplicadas em noticias_classificadas).
    """
    stmt = text(f"""
        SELECT titulo, link, data, texto, fonte, setor
          FROM {C.TABELA_POOL}
         WHERE setor IN :setores
    """).bindparams(bindparam("setores", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql(stmt, conn, params={"setores": list(C.SETORES_MNA)})
    if df.empty:
        return df
    # data: VARCHAR DD/MM/YYYY -> ISO (para _coerce_date/armazenamento em DATE).
    dt = pd.to_datetime(df["data"], dayfirst=True, errors="coerce")
    df["data"] = dt.dt.strftime("%Y-%m-%d")
    df = df.drop_duplicates(subset=["link"]).reset_index(drop=True)
    return df


def registrar_processado(engine, link: str, n_ops: int,
                         motivo_falha: str | None = None,
                         motivo_revisao: str | None = None) -> None:
    """Marca (idempotente) que ``link`` foi submetido ao LLM.

    Args:
        motivo_falha: a EXTRAÇÃO não rodou (nada foi gravado). Incrementa
            ``tentativas`` e o link volta ao pool enquanto não atingir
            ``MAX_TENTATIVAS_ARTIGO`` (ver :func:`links_processados`).
        motivo_revisao: a extração rodou e as operações FORAM gravadas, mas a
            validação/dedup falhou. Fica registrado para auditoria e NÃO gera
            retentativa — reprocessar duplicaria as operações já inseridas.
    """
    if not link:
        return
    tabela = C.TABELA_LOG.split(".")[-1]  # ON CONFLICT SET referencia sem schema
    with engine.begin() as conn:
        conn.execute(
            text(f"""
                INSERT INTO {C.TABELA_LOG}
                    (link_artigo, n_ops, motivo_falha, motivo_revisao, tentativas)
                VALUES (:link, :n, :motivo, :motivo_rev, 1)
                ON CONFLICT (link_artigo)
                DO UPDATE SET n_ops = EXCLUDED.n_ops,
                              motivo_falha = EXCLUDED.motivo_falha,
                              motivo_revisao = EXCLUDED.motivo_revisao,
                              tentativas = {tabela}.tentativas + 1,
                              processed_at = NOW()
            """),
            {"link": link, "n": int(n_ops), "motivo": motivo_falha,
             "motivo_rev": motivo_revisao},
        )


def append_operacoes(engine, ops: list[dict]) -> list[int]:
    """Insere (append) as operações extraídas de um lote e devolve os ``op_id``.

    Os ids servem de ``ref`` no dedup das notícias seguintes da mesma rodada — a
    duplicata aponta para a linha real e a mescla consegue atualizá-la.
    """
    if not ops:
        return []
    insert = text(f"""
        INSERT INTO {C.TABELA_OPERACOES}
            (empresa_adquirente, empresa_alvo, valor, moeda, setor, status,
             pais_adquirente, pais_alvo, descricao, titulo_artigo, link_artigo,
             data_artigo, fonte_artigo, zs_score)
        VALUES
            (:empresa_adquirente, :empresa_alvo, :valor, :moeda, :setor, :status,
             :pais_adquirente, :pais_alvo, :descricao, :titulo_artigo, :link_artigo,
             :data_artigo, :fonte_artigo, :zs_score)
        RETURNING op_id
    """)
    ids: list[int] = []
    with engine.begin() as conn:
        for op in ops:
            ids.append(int(conn.execute(insert, {
                "empresa_adquirente": op.get("empresa_adquirente"),
                "empresa_alvo": op.get("empresa_alvo"),
                "valor": coerce_valor(op.get("valor")),
                "moeda": op.get("moeda"),
                "setor": op.get("setor"),
                "status": op.get("status"),
                "pais_adquirente": op.get("pais_adquirente"),
                "pais_alvo": op.get("pais_alvo"),
                "descricao": op.get("descricao"),
                "titulo_artigo": op.get("titulo_artigo"),
                "link_artigo": op.get("link_artigo"),
                "data_artigo": coerce_data(op.get("data_artigo")),
                "fonte_artigo": op.get("fonte_artigo"),
                "zs_score": float(op.get("zs_score") or 0.0),
            }).scalar()))
    return ids


def append_rejeitadas(engine, ops: list[dict]) -> list[int]:
    """Grava em ``TABELA_REJEITADAS`` as operações descartadas.

    Cada dict deve trazer ``motivo_rejeicao`` e ``etapa`` ("validacao"/"dedup")
    além dos campos da operação. Devolve os ``rej_id``, na ordem de ``ops``.
    """
    if not ops:
        return []
    insert = text(f"""
        INSERT INTO {C.TABELA_REJEITADAS}
            (motivo_rejeicao, etapa, empresa_adquirente, empresa_alvo, valor, moeda,
             setor, status, pais_adquirente, pais_alvo, descricao, titulo_artigo,
             link_artigo, data_artigo, fonte_artigo, zs_score)
        VALUES
            (:motivo_rejeicao, :etapa, :empresa_adquirente, :empresa_alvo, :valor, :moeda,
             :setor, :status, :pais_adquirente, :pais_alvo, :descricao, :titulo_artigo,
             :link_artigo, :data_artigo, :fonte_artigo, :zs_score)
        RETURNING rej_id
    """)
    ids: list[int] = []
    with engine.begin() as conn:
        for op in ops:
            ids.append(int(conn.execute(insert, {
                "motivo_rejeicao": op.get("motivo_rejeicao"),
                "etapa": op.get("etapa"),
                "empresa_adquirente": op.get("empresa_adquirente"),
                "empresa_alvo": op.get("empresa_alvo"),
                "valor": coerce_valor(op.get("valor")),
                "moeda": op.get("moeda"),
                "setor": op.get("setor"),
                "status": op.get("status"),
                "pais_adquirente": op.get("pais_adquirente"),
                "pais_alvo": op.get("pais_alvo"),
                "descricao": op.get("descricao"),
                "titulo_artigo": op.get("titulo_artigo"),
                "link_artigo": op.get("link_artigo"),
                "data_artigo": coerce_data(op.get("data_artigo")),
                "fonte_artigo": op.get("fonte_artigo"),
                "zs_score": float(op.get("zs_score") or 0.0),
            }).scalar()))
    return ids


def carregar_indice_operacoes(engine) -> list[dict]:
    """Índice leve de TODAS as operações da base, para o blocking por nome.

    Sem filtro de data de propósito: um deal noticiado ao longo de meses (o caso
    Paramount/Warner tem linhas de abril a julho) não seria pego pela janela de
    ``DEDUP_LLM_DIAS``. São só 5 colunas — cabe em memória com folga.
    """
    stmt = text(f"""
        SELECT op_id, empresa_adquirente, empresa_alvo, valor, moeda, data_artigo
          FROM {C.TABELA_OPERACOES}
         ORDER BY op_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [{
        "ref": r["op_id"],
        "empresa_adquirente": r["empresa_adquirente"],
        "empresa_alvo": r["empresa_alvo"],
        "valor": r["valor"],
        "moeda": r["moeda"],
        "data_artigo": str(r["data_artigo"]) if r["data_artigo"] else None,
    } for r in rows]


def remover_operacoes(engine, op_ids: list[int]) -> int:
    """Remove operações por ``op_id``. Usada pela limpeza retroativa de duplicatas."""
    if not op_ids:
        return 0
    stmt = text(f"DELETE FROM {C.TABELA_OPERACOES} WHERE op_id IN :ids").bindparams(
        bindparam("ids", expanding=True))
    with engine.begin() as conn:
        res = conn.execute(stmt, {"ids": [int(i) for i in op_ids]})
    return int(res.rowcount or 0)


def carregar_operacoes_completas(engine) -> list[dict]:
    """Todas as operações com todos os campos (limpeza retroativa precisa do texto)."""
    stmt = text(f"""
        SELECT op_id, {', '.join(_COLS)}, computed_at, n_noticias
          FROM {C.TABELA_OPERACOES}
         ORDER BY op_id
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def operacoes_recentes(engine, dias: int, limite: int) -> list[dict]:
    """Operações cadastradas nos últimos ``dias`` (por ``computed_at``), p/ o dedup LLM.

    Ordena pelas mais recentes e corta em ``limite`` — o prompt de comparação não
    deve crescer sem teto quando o pipeline drena um lote grande do backlog.
    """
    stmt = text(f"""
        SELECT op_id, empresa_adquirente, empresa_alvo, valor, moeda, data_artigo
          FROM {C.TABELA_OPERACOES}
         WHERE computed_at >= NOW() - make_interval(days => :dias)
         ORDER BY computed_at DESC
         LIMIT :limite
    """)
    with engine.connect() as conn:
        rows = conn.execute(stmt, {"dias": int(dias), "limite": int(limite)}).mappings().all()
    return [{
        "ref": r["op_id"],
        "empresa_adquirente": r["empresa_adquirente"],
        "empresa_alvo": r["empresa_alvo"],
        "valor": r["valor"],
        "moeda": r["moeda"],
        "data_artigo": str(r["data_artigo"]) if r["data_artigo"] else None,
    } for r in rows]


def registrar_execucao(engine, stats: dict, *, iniciado_em, motivo: str | None,
                       link_interrompido: str | None) -> int:
    """Grava o desfecho da rodada em ``TABELA_EXECUCOES`` e devolve o ``exec_id``.

    Sempre chamada (sucesso, TPD ou erro): é o registro em base de que a rodada
    aconteceu e de quanto ficou de fora.
    """
    stmt = text(f"""
        INSERT INTO {C.TABELA_EXECUCOES}
            (iniciado_em, status, motivo, pool_total, pool_novos, selecionados,
             processados, ops_aprovadas, ops_rejeitadas_validacao,
             ops_rejeitadas_dedup, artigos_pendentes, artigos_com_falha,
             link_interrompido, total_consolidado)
        VALUES
            (:iniciado_em, :status, :motivo, :pool_total, :pool_novos, :selecionados,
             :processados, :ops_aprovadas, :ops_rej_val, :ops_rej_dup,
             :pendentes, :com_falha, :link_interrompido, :total_consolidado)
        RETURNING exec_id
    """)
    with engine.begin() as conn:
        exec_id = conn.execute(stmt, {
            "iniciado_em": iniciado_em,
            "status": stats.get("status"),
            "motivo": motivo,
            "pool_total": int(stats.get("pool_total") or 0),
            "pool_novos": int(stats.get("pool_novos") or 0),
            "selecionados": int(stats.get("selecionados") or 0),
            "processados": int(stats.get("processados") or 0),
            "ops_aprovadas": int(stats.get("ops_extraidas") or 0),
            "ops_rej_val": int(stats.get("ops_rejeitadas_validacao") or 0),
            "ops_rej_dup": int(stats.get("ops_rejeitadas_dedup") or 0),
            "pendentes": int(stats.get("artigos_pendentes") or 0),
            "com_falha": int(stats.get("artigos_com_falha") or 0),
            "link_interrompido": link_interrompido,
            "total_consolidado": int(stats.get("total_consolidado") or 0),
        }).scalar()
    log.info("[mna] execução %s registrada em %s (status=%s, pendentes=%d).",
             exec_id, C.TABELA_EXECUCOES, stats.get("status"),
             int(stats.get("artigos_pendentes") or 0))
    return int(exec_id)


def sincronizar_pendencias(engine, exec_id: int, pendencias: list[dict]) -> None:
    """Reescreve ``TABELA_PENDENCIAS`` com o que ficou sem processar NESTA rodada.

    DELETE + INSERT numa transação: a tabela responde "o que está pendente agora"
    (o histórico por rodada fica em ``TABELA_EXECUCOES``). Rodada que processa
    tudo limpa a tabela.

    Args:
        pendencias: dicts com ``link_artigo``, ``titulo_artigo`` e ``motivo``.
    """
    insert = text(f"""
        INSERT INTO {C.TABELA_PENDENCIAS} (exec_id, link_artigo, titulo_artigo, motivo)
        VALUES (:exec_id, :link, :titulo, :motivo)
    """)
    with engine.begin() as conn:
        conn.execute(text(f"DELETE FROM {C.TABELA_PENDENCIAS}"))
        for p in pendencias:
            conn.execute(insert, {
                "exec_id": exec_id,
                "link": p.get("link_artigo"),
                "titulo": p.get("titulo_artigo"),
                "motivo": p.get("motivo"),
            })
    if pendencias:
        log.warning("[mna] %d artigos PENDENTES registrados em %s (rodada %s).",
                    len(pendencias), C.TABELA_PENDENCIAS, exec_id)


# ── mescla de duplicatas (atualiza a operação cadastrada no lugar) ───────────────
def _json(v) -> str | None:
    return json.dumps(v, ensure_ascii=False, default=str) if v else None


def _aplicar_alteracoes(conn, op_id: int, alt: dict, *, n_noticias_extra: int) -> None:
    """UPDATE da operação com os campos de :func:`pipeline.mesclar`.

    Soma ``n_noticias_extra`` ao contador e marca ``atualizado_em`` só quando algum
    campo de fato mudou (o email/painel podem usar isso como "deal atualizado").
    """
    campos = [c for c in alt if c in _COLS]
    sets = [f"{c} = :{c}" for c in campos]
    sets.append("n_noticias = COALESCE(n_noticias, 1) + :extra")
    if campos:
        sets.append("atualizado_em = NOW()")
    params = {"op_id": int(op_id), "extra": int(n_noticias_extra)}
    for c in campos:
        v = alt[c]
        params[c] = (coerce_valor(v) if c == "valor"
                     else coerce_data(v) if c == "data_artigo" else v)
    conn.execute(text(f"UPDATE {C.TABELA_OPERACOES} SET {', '.join(sets)} "
                      f"WHERE op_id = :op_id"), params)


def _registrar_evento(conn, op_id: int, nova: dict, *, origem: str, alt: dict,
                      base: dict, divergencias: list[str], rej_id: int | None = None) -> None:
    """Grava em ``TABELA_EVENTOS`` a notícia ``nova`` mesclada na operação ``op_id``."""
    antes_depois = {c: [base.get(c), v] for c, v in alt.items()}
    conn.execute(text(f"""
        INSERT INTO {C.TABELA_EVENTOS}
            (op_id, rej_id, origem, empresa_adquirente, empresa_alvo, valor, moeda,
             status, descricao, titulo_artigo, link_artigo, data_artigo, fonte_artigo,
             alteracoes, divergencias)
        VALUES
            (:op_id, :rej_id, :origem, :empresa_adquirente, :empresa_alvo, :valor, :moeda,
             :status, :descricao, :titulo_artigo, :link_artigo, :data_artigo, :fonte_artigo,
             :alteracoes, :divergencias)
    """), {
        "op_id": int(op_id), "rej_id": rej_id, "origem": origem,
        "empresa_adquirente": nova.get("empresa_adquirente"),
        "empresa_alvo": nova.get("empresa_alvo"),
        "valor": coerce_valor(nova.get("valor")), "moeda": nova.get("moeda"),
        "status": nova.get("status"), "descricao": nova.get("descricao"),
        "titulo_artigo": nova.get("titulo_artigo"), "link_artigo": nova.get("link_artigo"),
        "data_artigo": coerce_data(nova.get("data_artigo")),
        "fonte_artigo": nova.get("fonte_artigo"),
        "alteracoes": _json(antes_depois),
        "divergencias": "; ".join(divergencias) or None,
    })


def _carregar_para_mescla(conn, op_id: int) -> dict | None:
    row = conn.execute(text(f"""
        SELECT op_id, {', '.join(_COLS)}, n_noticias
          FROM {C.TABELA_OPERACOES} WHERE op_id = :op_id FOR UPDATE
    """), {"op_id": int(op_id)}).mappings().first()
    return dict(row) if row else None


def mesclar_duplicata(engine, op_id: int, nova: dict, *, origem: str,
                      rej_id: int | None = None,
                      decisao: tuple[dict, list[str]] | None = None) -> dict | None:
    """Mescla a notícia ``nova`` (duplicata) na operação cadastrada ``op_id``.

    ``decisao`` = ``(alteracoes, divergencias)`` já calculadas pelo chamador (a
    limpeza retroativa descarta valor suspeito antes de aplicar); None = calcula
    aqui com :func:`pipeline.mesclar`.

    Returns:
        As alterações aplicadas ({} = só contou mais uma notícia), ou None se a
        operação não existe mais (o chamador loga; a duplicata segue auditada em
        TABELA_REJEITADAS).
    """
    with engine.begin() as conn:
        base = _carregar_para_mescla(conn, op_id)
        if base is None:
            return None
        alt, div = decisao if decisao is not None else mesclar(base, nova)
        _aplicar_alteracoes(conn, op_id, alt, n_noticias_extra=1)
        _registrar_evento(conn, op_id, nova, origem=origem, alt=alt, base=base,
                          divergencias=div, rej_id=rej_id)
    return alt


def fundir_operacoes(conn, manter: dict, absorvidas: list[dict], *, origem: str) -> dict:
    """Mescla ``absorvidas`` em ``manter`` e as remove (na transação ``conn``).

    Absorvidas entram em ordem de data do artigo, para que o desfecho mais novo
    prevaleça. Cada uma vira um evento no histórico. Devolve o estado final de
    ``manter``.
    """
    atual = dict(manter)
    for op in sorted(absorvidas, key=lambda o: str(o.get("data_artigo") or "")):
        alt, div = mesclar(atual, op)
        _registrar_evento(conn, manter["op_id"], op, origem=origem, alt=alt, base=atual,
                          divergencias=div, rej_id=op.get("rej_id"))
        atual.update(alt)
    final = {c: atual[c] for c in _COLS if atual.get(c) != manter.get(c)}
    extra = sum(int(o.get("n_noticias") or 1) for o in absorvidas)
    _aplicar_alteracoes(conn, manter["op_id"], final, n_noticias_extra=extra)
    ids = [int(o["op_id"]) for o in absorvidas if o.get("op_id") is not None]
    if ids:
        conn.execute(text(f"DELETE FROM {C.TABELA_OPERACOES} WHERE op_id IN :ids")
                     .bindparams(bindparam("ids", expanding=True)), {"ids": ids})
    return atual


def consolidar_tabela(engine) -> int:
    """Deduplica a tabela inteira por (adquirente, alvo) normalizados.

    NO LUGAR: em cada grupo do mesmo par, as repetidas são mescladas na
    sobrevivente (:func:`pipeline.escolher_sobrevivente`) e só elas são apagadas.
    Antes era DELETE + reinsert da tabela inteira: todo ``op_id`` mudava a cada
    rodada, o que impedia a mescla de achar a linha certa e deixava órfãs as
    referências "duplicata de id N" em TABELA_REJEITADAS. Linhas sem nome nenhum
    (adquirente e alvo vazios) continuam sendo descartadas, como antes.

    Returns:
        Número de operações após a consolidação.
    """
    ops = carregar_operacoes_completas(engine)
    if not ops:
        return 0
    grupos, sem_nome = agrupar_por_par_exato(ops)
    mescladas = 0
    with engine.begin() as conn:
        for grupo in grupos:
            manter = escolher_sobrevivente(grupo)
            absorvidas = [o for o in grupo if o["op_id"] != manter["op_id"]]
            fundir_operacoes(conn, manter, absorvidas, origem="consolidacao")
            mescladas += len(absorvidas)
        if sem_nome:
            conn.execute(text(f"DELETE FROM {C.TABELA_OPERACOES} WHERE op_id IN :ids")
                         .bindparams(bindparam("ids", expanding=True)),
                         {"ids": [int(o["op_id"]) for o in sem_nome]})
    total = len(ops) - mescladas - len(sem_nome)
    log.info("[mna] tabela consolidada: %d operações (%d mescladas em %d grupos, "
             "%d sem nome removidas).", total, mescladas, len(grupos), len(sem_nome))
    return int(total)
