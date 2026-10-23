"""Camada de acesso ao Postgres (schema licitacoes) para o medallion.

Conexão: INDUSTRY_PG_URL. bronze/silver/gold são criadas por sql/init.sql;
`gold_filtros`, `prazos_proposta` e `verticais` são criadas aqui se faltarem.

Deduplicação "só novos": cada linha do bronze tem um `row_hash` calculado a
partir do ID ESTÁVEL da fonte (config.SOURCE_STABLE_ID); linhas cujo hash já
existe no bronze não são reinseridas — mesmo que status/valor mudem entre coletas.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date as _date

import pandas as pd

from industry.shared.connections import get_sqlalchemy_engine
from industry.licitacoes.config import (
    PG_CONN, SCHEMA, TB_BRONZE, TB_SILVER, TB_GOLD, TB_GOLD_FILTROS, TB_PRAZOS,
    TB_VERTICAIS,
    SOURCE_STABLE_ID, BRONZE_COLS, GOLD_FILTROS_COLS, TEMAS, AREA_COL,
)

logger = logging.getLogger(__name__)


def get_engine():
    return get_sqlalchemy_engine(PG_CONN)


# ── hashing / dedup ───────────────────────────────────────────────────────────
def _safe(v) -> str:
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v)


def compute_row_hash(row: pd.Series) -> str:
    """Hash de dedup. Usa o ID estável da fonte quando configurado; senão, o
    conteúdo canônico inteiro (fallback)."""
    fonte = _safe(row.get("fonte"))
    id_cols = SOURCE_STABLE_ID.get(fonte)
    if id_cols and any(_safe(row.get(c)) for c in id_cols):
        chave = "|".join(_safe(row.get(c)) for c in id_cols)
    else:
        base = [c for c in BRONZE_COLS if c not in ("row_hash", "run_date",
                                                    "data_coleta", "id_unificado",
                                                    "dados_originais")]
        chave = "|".join(_safe(row.get(c)) for c in base)
    return hashlib.md5(f"{fonte}|{chave}".encode()).hexdigest()


def existing_bronze_hashes(engine) -> set[str]:
    with engine.connect() as conn:
        df = pd.read_sql(f"SELECT row_hash FROM {SCHEMA}.{TB_BRONZE}", conn)
    return set(df["row_hash"].dropna().tolist())


# ── tipagem silver/gold ───────────────────────────────────────────────────────
_DATA_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATA_BR = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def normalizar_modalidade(raw) -> str:
    """Agrupa as N variantes de `tipo`/modalidade num rótulo canônico.

    O campo `tipo` vem sujo das fontes: variações de caixa/grafia ("Pregão -
    Eletrônico", "Pregão eletrônico", "Pregão (Lei 13.303)"), sufixos de lei, e até
    valores malformados (ex.: "{'id':4,'tipo':'pregão'}"). Reduz tudo a um conjunto
    canônico para exibição/categorização. Ver DECISOES.md §10.
    """
    import ast
    s = _safe(raw).strip()
    if s.startswith("{"):                      # valor malformado (dict serializado)
        try:
            s = str(ast.literal_eval(s).get("tipo", ""))
        except Exception:
            pass
    t = s.lower()
    if not t:
        return "(vazio)"
    # Organismos internacionais (Banco Mundial, PNUD, ONU): rótulos em inglês.
    if "individual contractor" in t or "individual consultant" in t:
        return "Internacional - Consultor individual"
    if "expression of interest" in t:
        return "Internacional - Manifestação de interesse"
    if "request for proposal" in t or t.startswith("rfp"):
        return "Internacional - Proposta (RFP)"
    if "request for quotation" in t or t.startswith("rfq"):
        return "Internacional - Cotação (RFQ)"
    if ("invitation for bid" in t or "request for bids" in t or t.startswith("itb")
            or "invitation to bid" in t):
        return "Internacional - Licitação (ITB)"
    if "prequalification" in t:
        return "Pré-qualificação"
    if "cpsi" in t or "182/2021" in t or "desafio" in t:
        return "CPSI / Desafio de inovação"
    if "contrata" in t and "direta" in t:
        return "Dispensa"
    if "inexig" in t or "inaplicab" in t:
        return "Inexigibilidade"
    if "credenc" in t:
        return "Credenciamento"
    if "chamamento" in t:
        return "Chamamento Público"
    if "diálogo" in t or "dialogo" in t:
        return "Diálogo Competitivo"
    if "concurso" in t:
        return "Concurso"
    if "convite" in t:
        return "Convite"
    if "tomada" in t:
        return "Tomada de Preços"
    if "leil" in t or "concess" in t or "ppp" in t:
        return "Leilão/Concessão"
    if "empenho" in t:
        return "Empenho"
    if "contrato" in t:
        return "Contrato"
    if "concorr" in t:
        return "Concorrência"
    if "dispensa" in t:
        return "Dispensa Eletrônica" if "eletr" in t else "Dispensa"
    # Lei 13.303 é o regime das estatais. O Banrisul rotula a modalidade SEM a
    # palavra "pregão" ("Lei 13.303 - Eletrônico", "Licitação 13.303/16
    # Eletrônica"), então o tratamento não podia ficar aninhado dentro do ramo
    # `"preg" in t` — 98 linhas caíam em "Outros/Genérico", incluindo uma
    # "Lei 13.303 - Presencial" que é pregão presencial claro.
    if "pré-qualif" in t or "pre-qualif" in t or "pré qualif" in t:
        return "Pré-qualificação"
    if "manifesta" in t and "interesse" in t:
        return "Manifestação de Interesse"
    if "13.303" in t or "13303" in t:
        if t.startswith("licita") and "eletr" not in t and "presencial" not in t:
            return "Licitação (Lei 13.303)"
        if "eletr" in t:
            return "Pregão Eletrônico"
        if "presencial" in t:
            return "Pregão Presencial"
        # Sem qualificador o texto de origem não diz qual é — não assumir
        # "Eletrônico", que mostrava modalidade errada no e-mail.
        return "Pregão (Lei 13.303)"
    if "preg" in t:
        return "Pregão Presencial" if ("presencial" in t and "eletr" not in t) else "Pregão Eletrônico"
    return "Outros/Genérico"


def _data_valida(y: int, mo: int, d: int) -> bool:
    """A tripla existe no calendário? Barra 2026-45-16 e 31/02/2026 na origem.

    Usa o próprio `date` em vez de faixas (1-12 / 1-31): a checagem por faixa
    deixava passar 31 de fevereiro, que quebra igual lá na frente.
    """
    try:
        _date(y, mo, d)
        return True
    except ValueError:
        return False


def to_date(v):
    """Normaliza uma data (str variada) para 'YYYY-MM-DD' ou None.

    Duas garantias que o formato ISO não tinha e o BR sim:

    1. **Validação de calendário.** O ramo ISO só aplicava o regex, então
       "2026-45-16" voltava intacto e explodia mais adiante em
       `date.fromisoformat()` — que é chamado sem try em `_na_janela` (task.py)
       e no dashboard. Uma fonte com mês 13 derrubava a página inteira.
    2. **Busca em qualquer posição.** O ISO usava `.match` (só do início) e o BR
       `.search`; "publicado 2026-09-16" virava None enquanto
       "publicado 16/09/2026" funcionava. Agora os dois usam `.search`.
    """
    s = _safe(v).strip()
    if not s:
        return None
    m = _DATA_ISO.search(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _data_valida(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = _DATA_BR.search(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _data_valida(y, mo, d):
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def to_numeric(v):
    """Extrai valor numérico (pt-BR ou US) -> float ou None (>0)."""
    s = _safe(v).strip()
    if not s:
        return None
    # pt-BR: 1.234.567,89 ou 250000,00. O lookbehind impede casar só o fim de um
    # número sem separador de milhar ("250000,00" virava "000,00" = 0).
    m = re.search(r"(?<![\d.])(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}(?!\d)", s)
    if m:
        s = m.group(0).replace(".", "").replace(",", ".")
    else:
        m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", "."))
        if not m:
            return None
        s = m.group(0)
    try:
        val = float(s)
        return val if val > 0 else None
    except ValueError:
        return None


# ── inserts ───────────────────────────────────────────────────────────────────
# Colunas tipadas por tabela — coeragem antes do insert (o round-trip por CSV
# entre tasks transforma tudo em string; sem isso, "" iria p/ coluna int/date/num).
_TYPED = {
    "bronze": {"qtd_documentos": "int", "run_date": "date"},
    "silver": {"data": "date", "valor": "num"},
    "gold": {"data": "date", "valor": "num"},
    "gold_filtros": {
        "data": "date", "valor": "num", "run_date": "date",
        "s_kw": "num", "s_zs": "num", "s_llm": "num", "score_combinado": "num",
        "n_sinais_usados": "int", "usou_llm": "int", "relevante_combinado": "int",
        "tag_relevante": "int",
        **{f"tag_kw_{t}": "int" for t in TEMAS},
        **{f"tag_zs_{t}": "num" for t in TEMAS},
        **{c: "int" for c in AREA_COL.values()},
        "llm_viabilidade": "int", "s_viab_llm": "num", "s_viab_valor": "num",
        "score_viabilidade": "num",
    },
}


def _to_jsonb(v):
    import json as _json
    if isinstance(v, (dict, list)):
        return v
    s = str(v).strip()
    if not s:
        return None
    try:
        return _json.loads(s)
    except Exception:
        return None


def insert_df(df: pd.DataFrame, table: str, cols: list[str]) -> int:
    """Append de um DataFrame numa tabela do schema (só as colunas informadas),
    coagindo datas/números/jsonb para os tipos do banco."""
    if df.empty:
        return 0
    from sqlalchemy.dialects.postgresql import JSONB

    engine = get_engine()
    out = df[[c for c in cols if c in df.columns]].copy()

    dtype = None
    if "dados_originais" in out.columns:
        out["dados_originais"] = out["dados_originais"].apply(_to_jsonb)
        dtype = {"dados_originais": JSONB}

    for c, t in _TYPED.get(table, {}).items():
        if c not in out.columns:
            continue
        if t == "date":
            out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
        elif t == "num":
            out[c] = pd.to_numeric(out[c], errors="coerce")
        elif t == "int":
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")

    out = out.replace({"": None})
    out = out.where(pd.notnull(out), None)

    out.to_sql(table, engine, schema=SCHEMA, if_exists="append",
               index=False, method="multi", chunksize=500, dtype=dtype)
    logger.info(f"Inseridos {len(out)} registros em {SCHEMA}.{table}")
    return len(out)


def ensure_gold_filtros(engine=None) -> None:
    """Cria licitacoes.gold_filtros se não existir (colunas do gold_top5)."""
    engine = engine or get_engine()
    tipo = {
        "id_unificado": "text PRIMARY KEY",
        "data": "date", "valor": "numeric(18,2)",
        "s_kw": "double precision", "s_zs": "double precision",
        "s_llm": "double precision", "score_combinado": "double precision",
        "n_sinais_usados": "integer", "usou_llm": "integer",
        "relevante_combinado": "integer", "tag_relevante": "integer",
        "run_date": "date",
    }
    # zero-shot scores por tema são numéricos
    for c in AREA_COL.values():
        tipo[c] = "integer"
    tipo.update({"llm_viabilidade": "integer", "s_viab_llm": "double precision",
                 "s_viab_valor": "double precision", "score_viabilidade": "double precision"})
    for t in TEMAS:
        tipo[f"tag_zs_{t}"] = "double precision"
        tipo[f"tag_kw_{t}"] = "integer"
    linhas = [f'"{c}" {tipo.get(c, "text")}' for c in GOLD_FILTROS_COLS]
    ddl = (f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{TB_GOLD_FILTROS} (\n  "
           + ",\n  ".join(linhas) + "\n)")
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text(ddl))
    logger.info(f"gold_filtros garantida em {SCHEMA}.{TB_GOLD_FILTROS}")


def existing_ids(table: str) -> set[str]:
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(f"SELECT id_unificado FROM {SCHEMA}.{table}", conn)
    return set(df["id_unificado"].dropna().tolist())


def existing_native_ids(fontes: list[str]) -> set[tuple[str, str]]:
    """{(fonte, id_nativo)} já no bronze, para as fontes pedidas.

    Chave de identidade mais estável que o `id_unificado`: sobrevive a mudanças
    no esquema do id (base que passou de `numero` para `id_nativo`) e a mudanças
    de conteúdo (que alteram o row_hash). É o que impede uma correção de parser
    de reingerir a fonte inteira. Ver `bronze.inserir_bronze`.
    """
    if not fontes:
        return set()
    import sqlalchemy as sa
    engine = get_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            sa.text(f"SELECT fonte, id_nativo FROM {SCHEMA}.{TB_BRONZE} "
                    f"WHERE fonte = ANY(:f) AND coalesce(id_nativo, '') <> ''"),
            conn, params={"f": list(fontes)})
    return set(zip(df["fonte"], df["id_nativo"]))


def ensure_prazos(engine=None) -> None:
    """Cria licitacoes.prazos_proposta se não existir (idempotente)."""
    from sqlalchemy import text
    engine = engine or get_engine()
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{TB_PRAZOS} (\n"
        "  id_unificado text PRIMARY KEY,\n"
        "  data_abertura_proposta date,\n"
        "  data_encerramento_proposta date,\n"
        "  situacao_pncp text,\n"
        "  atualizado_em timestamptz DEFAULT now()\n)"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl))


def upsert_prazos(rows: list[dict]) -> int:
    """Upsert de prazos de proposta por id_unificado (usado pelo filtrar_acionaveis).

    rows: dicts com id_unificado, data_abertura_proposta, data_encerramento_proposta,
    situacao_pncp (datas em str variada — coagidas com to_date).
    """
    if not rows:
        return 0
    from sqlalchemy import text
    ensure_prazos()
    sql = text(
        f"INSERT INTO {SCHEMA}.{TB_PRAZOS} (id_unificado, data_abertura_proposta, "
        f"data_encerramento_proposta, situacao_pncp, atualizado_em) "
        f"VALUES (:id, :ab, :enc, :sit, now()) "
        f"ON CONFLICT (id_unificado) DO UPDATE SET "
        f"data_abertura_proposta = EXCLUDED.data_abertura_proposta, "
        f"data_encerramento_proposta = EXCLUDED.data_encerramento_proposta, "
        f"situacao_pncp = EXCLUDED.situacao_pncp, atualizado_em = now()"
    )
    engine = get_engine()
    with engine.begin() as conn:
        for r in rows:
            conn.execute(sql, {
                "id": r["id_unificado"],
                "ab": to_date(r.get("data_abertura_proposta")),
                "enc": to_date(r.get("data_encerramento_proposta")),
                "sit": r.get("situacao_pncp"),
            })
    return len(rows)


def meta_bronze(ids: list[str]) -> dict:
    """Retorna, por id, os campos do bronze necessários para o filtro 'acionável':
    {id_unificado: {orgao_cnpj, ano, numero_controle_pncp, data, data_publicacao, run_date}}.

    O silver/gold descartam esses campos (cnpj/ano/controle), mas o enriquecimento
    de prazo do PNCP precisa deles — então buscamos de volta pelo id.
    """
    if not ids:
        return {}
    engine = get_engine()
    lista = "','".join(str(i).replace("'", "''") for i in ids)
    with engine.connect() as conn:
        df = pd.read_sql(
            f"SELECT id_unificado, orgao_cnpj, ano, numero_controle_pncp, "
            f"data, data_publicacao, run_date, tipo_documento, tipo, "
            f"data_encerramento_proposta, data_abertura, situacao "
            f"FROM {SCHEMA}.{TB_BRONZE} WHERE id_unificado IN ('{lista}')", conn)
    return {r["id_unificado"]: r.to_dict() for _, r in df.iterrows()}


# ── Verticais (classificação do objeto por LLM) ───────────────────────────────
# A tabela é COMPARTILHADA com o dashboard (que só lê) e com o CLI manual
# `classificar_verticais.py` do bateleur-artifacts — DDL igual ao dele, de
# propósito: os dois criam a mesma tabela e o CREATE IF NOT EXISTS de um não
# briga com o do outro.
def ensure_verticais(engine=None) -> None:
    """Cria licitacoes.verticais se não existir (idempotente)."""
    from sqlalchemy import text
    engine = engine or get_engine()
    ddl = (
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{TB_VERTICAIS} (\n"
        "  id_unificado text PRIMARY KEY,\n"
        "  vertical text NOT NULL,\n"
        "  confianca double precision,\n"
        "  modelo text,\n"
        "  classificado_em timestamptz DEFAULT now()\n)"
    )
    with engine.begin() as conn:
        conn.execute(text(ddl))


def objetos_sem_vertical(limite: int | None = None) -> pd.DataFrame:
    """Licitações PONTUADAS (gold_filtros) ainda sem vertical. Colunas:
    id_unificado, objeto.

    Era o gold inteiro, por causa da aba "Todos da semana" do email da origem,
    que não existe mais; classificar o que fechou antes de ser pontuado era
    gasto de LLM (19/10/2026). O mais relevante primeiro: se o teto ou a cota
    cortar, corta o que menos importa.
    """
    ensure_verticais()
    extra = f" LIMIT {int(limite)}" if limite else ""
    sql = (
        f"SELECT gf.id_unificado, gf.objeto "
        f"  FROM {SCHEMA}.{TB_GOLD_FILTROS} gf "
        f"  LEFT JOIN {SCHEMA}.{TB_VERTICAIS} v ON v.id_unificado = gf.id_unificado "
        f" WHERE v.id_unificado IS NULL "
        f" ORDER BY gf.score_combinado DESC NULLS LAST{extra}"
    )
    with get_engine().connect() as conn:
        return pd.read_sql(sql, conn)


def upsert_vertical(conn, id_unificado: str, vertical: str, confianca: float,
                    modelo: str) -> None:
    """Grava UMA classificação (commit é do chamador, 1 por resposta do LLM).

    Só é chamada para resultado bem-sucedido: falha de LLM NÃO é persistida, para
    a rodada seguinte retentar em vez de congelar um 'outros' errado.
    """
    from sqlalchemy import text
    conn.execute(text(
        f"INSERT INTO {SCHEMA}.{TB_VERTICAIS} "
        f"       (id_unificado, vertical, confianca, modelo, classificado_em) "
        f"VALUES (:id, :vert, :conf, :modelo, now()) "
        f"ON CONFLICT (id_unificado) DO UPDATE SET "
        f"  vertical = EXCLUDED.vertical, confianca = EXCLUDED.confianca, "
        f"  modelo = EXCLUDED.modelo, classificado_em = now()"
    ), {"id": id_unificado, "vert": vertical, "conf": confianca, "modelo": modelo})


def cobertura_vertical() -> tuple[int, int]:
    """(linhas no gold, linhas com vertical) — usado no log da task."""
    ensure_verticais()
    with get_engine().connect() as conn:
        df = pd.read_sql(
            f"SELECT (SELECT count(*) FROM {SCHEMA}.{TB_GOLD}) AS gold, "
            f"       (SELECT count(*) FROM {SCHEMA}.{TB_VERTICAIS} v "
            f"         WHERE EXISTS (SELECT 1 FROM {SCHEMA}.{TB_GOLD} g "
            f"                        WHERE g.id_unificado = v.id_unificado)) AS com_vert",
            conn)
    return int(df["gold"].iloc[0]), int(df["com_vert"].iloc[0])


def gold_sem_score(limite: int) -> pd.DataFrame:
    """Linhas do gold que nunca entraram no gold_filtros (sem score).

    O `montar_gold_filtros` recebe só o delta da própria run (`gold_novos.csv`,
    saída do `inserir_gold`), e nada varre o que ficou para trás. Quando o gold é
    alimentado por fora do fluxo normal — a carga manual de 14/07/2026, feita
    porque a DAG semanal estava falhando havia 6 semanas no coletor do BNB —
    essas linhas ficam sem score PARA SEMPRE. Medido em 16/09/2026: 2.461 linhas,
    2.454 delas da carga de julho.

    Sem score a linha some das abas que filtram por `score_combinado` e aparece
    na aba "Todos da semana" (que lê o gold) sem pontuação, o que é pior do que
    ter nota baixa: não dá para ordenar nem para cortar.

    Duas populações, não uma:

    - **ausente do gold_filtros** — a carga manual descrita acima;
    - **presente com `score_combinado` NULL** — as 119 linhas que o bug do NaN
      produziu (ver `score._ausente`). Essas ESTÃO no gold_filtros, então um
      `WHERE gf.id_unificado IS NULL` nunca as alcançava: ficariam NULL para
      sempre mesmo depois do bug corrigido. Como NULL não é score, elas contam
      como "sem score" para todos os efeitos.

    Mais recente primeiro: se o teto cortar, corta o backlog mais frio.
    """
    return _read_df(
        f"SELECT g.* FROM {SCHEMA}.{TB_GOLD} g "
        f"  LEFT JOIN {SCHEMA}.{TB_GOLD_FILTROS} gf ON gf.id_unificado = g.id_unificado "
        f"  LEFT JOIN {SCHEMA}.{TB_BRONZE} b ON b.id_unificado = g.id_unificado "
        f" WHERE gf.id_unificado IS NULL OR gf.score_combinado IS NULL "
        f" ORDER BY b.run_date DESC NULLS LAST "
        f" LIMIT {int(limite)}")


def apagar_gold_filtros(ids: list[str]) -> int:
    """Remove ids do gold_filtros para que possam ser re-pontuados.

    `inserir_gold_filtros` descarta id que já existe, então re-pontuar uma linha
    com score NULL exige tirá-la antes. Só é chamada para linhas cujo score é
    NULL — não há score a perder.
    """
    if not ids:
        return 0
    import sqlalchemy as sa
    with get_engine().begin() as conn:
        r = conn.execute(
            sa.text(f"DELETE FROM {SCHEMA}.{TB_GOLD_FILTROS} "
                    f"WHERE id_unificado = ANY(:ids) AND score_combinado IS NULL"),
            {"ids": list(ids)})
    return r.rowcount or 0


def _read_df(sql: str) -> pd.DataFrame:
    with get_engine().connect() as conn:
        return pd.read_sql(sql, conn)


def completar_bronze(df: pd.DataFrame) -> int:
    """Preenche, em linhas que JÁ existem no bronze, o prazo de proposta vazio e a
    marca de pessoa física quando a coleta de agora traz o dado. Ex.: o CPSI da
    SEFAZ-PI entrou pelo pncp.py sem prazo e o pncp_cpsi.py traz o encerramento."""
    if df is None or df.empty or "id_unificado" not in df.columns:
        return 0
    from sqlalchemy import text
    cols = [c for c in ("data_encerramento_proposta", "pessoa_fisica") if c in df.columns]
    if not cols:
        return 0
    linhas = []
    for _, r in df.iterrows():
        enc = _safe(r.get("data_encerramento_proposta")).strip()
        pf = _safe(r.get("pessoa_fisica")).strip().lower()
        if enc or pf == "sim":
            linhas.append({"id": r["id_unificado"], "enc": enc or None, "pf": pf == "sim"})
    if not linhas:
        return 0
    sql = text(
        f"UPDATE {SCHEMA}.{TB_BRONZE} SET "
        f"data_encerramento_proposta = coalesce(nullif(data_encerramento_proposta, ''), :enc), "
        f"pessoa_fisica = CASE WHEN :pf THEN 'sim' ELSE pessoa_fisica END "
        f"WHERE id_unificado = :id AND (coalesce(data_encerramento_proposta, '') = '' AND :enc IS NOT NULL "
        f"OR (:pf AND coalesce(pessoa_fisica, '') <> 'sim'))")
    with get_engine().begin() as conn:
        n = sum(conn.execute(sql, p).rowcount for p in linhas)
    if n:
        logger.info(f"[bronze] {n} linha(s) existentes completadas (prazo ou pessoa física)")
    return n


def bronze_abertos_fora_da_silver() -> pd.DataFrame:
    """Linhas do bronze com prazo de proposta de hoje em diante que nunca foram
    para a silver: o corte de recência as tirou antes de se saber o prazo."""
    return _read_df(
        f"SELECT b.* FROM {SCHEMA}.{TB_BRONZE} b "
        f"  LEFT JOIN {SCHEMA}.{TB_SILVER} s ON s.id_unificado = b.id_unificado "
        f" WHERE s.id_unificado IS NULL "
        f"   AND b.data_encerramento_proposta ~ '^\d{{4}}-\d{{2}}-\d{{2}}' "
        f"   AND left(b.data_encerramento_proposta, 10)::date >= current_date")
