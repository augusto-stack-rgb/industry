"""
Tasks do agente de filtro de setor.
Cada função pública é chamada por uma @task da DAG.
"""

import re
import logging
from collections import Counter

import pandas as pd
import pendulum
from sqlalchemy import text

from industry.noticias.classificacao.config import (
    VALID_SECTORS,
    CONTEXTO,
    SECTOR_DESCRIPTIONS,
    TABELA_ORIGEM,
    TABELA_DESTINO,
    LLM_MODEL,
    LLM_MAX_TOKENS,
    LLM_REASONING_EFFORT,
    BATCH_SIZE,
    BATCH_SIZE_REPESCAGEM,
    RODADAS_REPESCAGEM,
    TIMEOUT_S,
)
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine
from industry.shared import llm

log = logging.getLogger(__name__)


# =============================================================================
# Helpers internos
# =============================================================================


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _fmt_set(s) -> str:
    return ",".join(sorted(s)) if s else "∅"


def _build_prompt(lote: list[tuple[int, str]]) -> tuple[str, str]:
    setores_str = "\n".join(f"- {s}" for s in VALID_SECTORS)
    descricoes_str = "\n".join(
        f"  {setor}: {desc}" for setor, desc in SECTOR_DESCRIPTIONS.items()
    )
    noticias_str = "\n".join(f"{i}# {titulo}" for i, titulo in lote)

    system_msg = (
        "Você é um classificador de notícias. "
        "Responda SOMENTE no formato solicitado. "
        "Qualquer texto fora do formato é proibido."
    )

    human_msg = f"""
Contexto:
{CONTEXTO}

Setores válidos (use APENAS estes valores literais):
{setores_str}
- Lixo

Descrição de cada setor (use para guiar a classificação):
{descricoes_str}

Tarefa:
Para CADA notícia abaixo, atribua o conjunto de setores apropriado.
Uma notícia pode pertencer a um ou mais setores.

Notícias (uma por linha, no formato ID# título):
{noticias_str}

REGRAS OBRIGATÓRIAS:
- Você DEVE retornar EXATAMENTE uma linha por notícia, começando pelo ID recebido.
- Os setores válidos são SOMENTE os listados acima; notícia que só caberia em outro
  setor (bancos, varejo, saúde, imobiliário, macroeconomia geral) é 'Lixo'.
- Use EXATAMENTE os valores literais da lista acima (ou 'Lixo').
- Não invente setores novos.
- Não comente, não explique, não adicione preâmbulo nem rodapé.
- Não repita o título: só o ID e os setores.

REGRAS DE DESEMPATE (setores que se sobrepõem):
- Tecnologia aplicada: IA, dados, software, automação ou IoT aplicados a agro,
  indústria, energia ou veículos → inclua Tecnologia E o setor de aplicação.
- Commodities: petróleo, gás, combustíveis e energia → Óleo e Gás; grãos,
  proteína animal, fertilizantes → Agro; aço, metais, química, mineração → Indústria.
- Veículos, autopeças, baterias e EVs → Automotivo (+ Indústria quando o foco é a
  fábrica/produção; + Tecnologia quando o foco é software/eletrônica embarcada).
- Fusão, aquisição ou investimento: classifique pelo setor das empresas envolvidas.

FORMATO EXATO (uma notícia por linha):
ID# setor1,setor2,setor3

Exemplos (entrada → saída):
  "1# John Deere lança plataforma de IA para colheita" → 1# Agro,Tecnologia
  "2# Governador participa de formatura" → 2# Lixo
  "3# Soja sobe em Chicago" → 3# Agro
  "4# Petrobras contrata sistema de monitoramento de plataformas" → 4# Óleo e Gás,Tecnologia
  "5# Copom mantém Selic em 10,5%" → 5# Lixo
  "6# Gerdau anuncia nova linha de laminação no RS" → 6# Indústria
""".strip()

    return system_msg, human_msg


def _parse_resposta(texto: str, lote: list[tuple[int, str]]) -> dict[int, list[str]]:
    """{id: [setores]} para os IDs do lote que voltaram no formato.

    Casa pelo ID, não pelo título: casar pelo título exigia que o LLM
    devolvesse o texto idêntico, e o que ele mudasse (aspas, espaço duplo,
    tradução) deixava a notícia sem setor.
    """
    permitidos = set(VALID_SECTORS) | {"Lixo"}
    titulo_por_id = dict(lote)
    classificacoes: dict[int, list[str]] = {}

    for linha in texto.splitlines():
        m = re.match(r"^\s*(\d+)\s*#\s*(.+?)\s*$", linha)
        if not m:
            continue
        noticia_id = int(m.group(1))
        if noticia_id not in titulo_por_id:
            log.warning("ID %s não pertence ao lote — ignorado.", noticia_id)
            continue
        titulo = titulo_por_id[noticia_id]
        setores = {s.strip() for s in m.group(2).split(",") if s.strip()}

        invalidos = setores - permitidos
        if invalidos:
            log.warning(
                "Setores inválidos %s em '%s' — descartados.",
                invalidos,
                titulo[:60],
            )
            setores -= invalidos

        if not setores:
            log.warning("Nenhum setor válido para '%s' — pulando.", titulo[:60])
            continue

        if "Lixo" in setores and len(setores) > 1:
            log.warning(
                "'Lixo' veio junto com %s em '%s' — usando apenas 'Lixo'.",
                setores - {"Lixo"},
                titulo[:60],
            )
            setores = {"Lixo"}

        # XCom não serializa set; converte pra list ordenada
        classificacoes[noticia_id] = sorted(setores)

    return classificacoes


def _classificar_lotes(client, itens, tamanho, rotulo) -> dict[int, list[str]]:
    """Uma passada em lotes de ``tamanho``. ``itens`` = [(id, titulo)]."""
    resultado: dict[int, list[str]] = {}
    n_lotes = (len(itens) + tamanho - 1) // tamanho
    for i in range(0, len(itens), tamanho):
        lote = itens[i : i + tamanho]
        idx = i // tamanho + 1
        try:
            system_msg, human_msg = _build_prompt(lote)
            resp = client.create(
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": human_msg},
                ],
            )
            classif = _parse_resposta(resp.choices[0].message.content.strip(), lote)
        except Exception as e:
            log.error("%s — lote %d/%d falhou (volta na repescagem): %s", rotulo, idx, n_lotes, e)
            continue

        log.info("=" * 72)
        log.info(
            "%s — lote %d/%d: %d enviados, %d classificados",
            rotulo, idx, n_lotes, len(lote), len(classif),
        )
        titulo_por_id = dict(lote)
        for noticia_id, setores in classif.items():
            log.info("  [%s] | %s", _fmt_set(setores), titulo_por_id[noticia_id][:90])
        resultado.update(classif)
    return resultado


# =============================================================================
# Tasks
# =============================================================================


def garantir_tabela() -> None:
    """Cria noticias_classificadas se não existir (idempotente)."""
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        ddl = text(f"""
            CREATE TABLE IF NOT EXISTS {TABELA_DESTINO} (
                id SERIAL PRIMARY KEY,
                titulo TEXT NOT NULL,
                link TEXT,
                data TEXT,
                texto TEXT,
                setor TEXT NOT NULL,
                fonte TEXT,
                data_classificacao TIMESTAMP DEFAULT NOW(),
                modelo TEXT,
                UNIQUE (titulo, setor)
            )
        """)
        with engine.begin() as conn:
            conn.execute(ddl)
        log.info("Tabela %s pronta.", TABELA_DESTINO)
    finally:
        engine.dispose()


def carregar_noticias_novas() -> list[dict]:
    """
    Retorna registros com `data` (do artigo) == hoje OU == ontem que ainda NÃO
    estão em noticias_classificadas. Cada item: {titulo, link, data, texto, fonte}.

    Janela de 2 dias (hoje + ontem): o run da manhã pegava só a fatia de hoje já
    coletada e o resto do dia ficava órfão para sempre. Incluir ontem faz o run
    de hoje recuperar as notícias de ontem que entraram após o run anterior — as
    ainda não classificadas (a exclusão por título evita reprocessar as já feitas).
    """
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        with engine.connect() as conn:
            df = pd.read_sql(
                text(f"""
                    SELECT titulo, link, data, texto, fonte
                      FROM {TABELA_ORIGEM}
                """),
                conn,
            )
            df_existentes = pd.read_sql(
                text(f"SELECT DISTINCT titulo FROM {TABELA_DESTINO}"),
                conn,
            )
    finally:
        engine.dispose()

    df["_data"] = pd.to_datetime(df["data"], dayfirst=True, errors="coerce").dt.normalize()
    hoje = pd.Timestamp(pendulum.now("America/Sao_Paulo").date())
    ontem = hoje - pd.Timedelta(days=1)
    df = df[df["_data"].isin([hoje, ontem])].drop(columns=["_data"])
    df = df.drop_duplicates(subset=["titulo"])

    ja_classificados = set(df_existentes["titulo"].astype(str))
    n_total = len(df)
    df = df[~df["titulo"].astype(str).isin(ja_classificados)]

    registros = df.fillna("").to_dict(orient="records")
    log.info(
        "data em {%s, %s}: %d títulos únicos | já classificados: %d | a processar: %d",
        ontem.date().isoformat(),
        hoje.date().isoformat(),
        n_total,
        n_total - len(registros),
        len(registros),
    )
    return registros


def classificar_titulos(registros: list[dict]) -> dict[str, list[str]]:
    """Chama o LLM (roteador de chaves) em lotes. Retorna {titulo: [setores]}.

    Uma passada em lotes de BATCH_SIZE e depois até RODADAS_REPESCAGEM
    passadas só com o que faltou, em lotes menores (lote menor falha menos e
    isola o título que confunde o modelo).
    """
    if not registros:
        log.info("Nada a classificar.")
        return {}

    # ``carregar_noticias_novas`` já deduplica por título; o ID é a posição.
    itens = list(enumerate((r["titulo"] for r in registros), start=1))
    # Antes: Ollama local (qwen 27B) numa GPU dedicada. Aqui o mesmo modelo via
    # API, pelo roteador de chaves (Groq/Cerebras, com troca automática de chave).
    client = llm.cliente(
        LLM_MODEL,
        timeout=TIMEOUT_S,
        temperature=0,
        max_tokens=LLM_MAX_TOKENS,
        reasoning_effort=LLM_REASONING_EFFORT,
    )

    por_id = _classificar_lotes(client, itens, BATCH_SIZE, "1ª passada")
    for rodada in range(1, RODADAS_REPESCAGEM + 1):
        faltando = [(i, t) for i, t in itens if i not in por_id]
        if not faltando:
            break
        log.warning(
            "%d de %d sem setor — repescagem %d/%d",
            len(faltando), len(itens), rodada, RODADAS_REPESCAGEM,
        )
        por_id.update(
            _classificar_lotes(client, faltando, BATCH_SIZE_REPESCAGEM, f"repescagem {rodada}")
        )

    faltando = [t for i, t in itens if i not in por_id]
    if faltando:
        log.error(
            "%d de %d títulos ficaram SEM SETOR após a repescagem (voltam no próximo run "
            "enquanto estiverem na janela de hoje+ontem):",
            len(faltando), len(itens),
        )
        for t in faltando:
            log.error("  - %s", t[:90])

    titulo_por_id = dict(itens)
    todas = {titulo_por_id[i]: setores for i, setores in por_id.items()}

    # resumo final
    if todas:
        contador = Counter(s for setores in todas.values() for s in setores)
        log.info("=" * 72)
        log.info("Distribuição entre %d títulos:", len(todas))
        for setor, n in contador.most_common():
            log.info("  %-15s : %d", setor, n)

    return todas


def inserir_classificadas(
    classificacoes: dict[str, list[str]],
    registros: list[dict],
) -> dict:
    """Persiste em noticias_classificadas. Idempotente via ON CONFLICT."""
    if not classificacoes:
        log.info("Nada a inserir.")
        return {"inseridas": 0}

    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        insert_sql = text(f"""
            INSERT INTO {TABELA_DESTINO}
                (titulo, link, data, texto, setor, fonte, modelo)
            VALUES
                (:titulo, :link, :data, :texto, :setor, :fonte, :modelo)
            ON CONFLICT (titulo, setor) DO NOTHING
        """)

        template_por_titulo = {r["titulo"]: r for r in registros}
        inseridas = 0

        with engine.begin() as conn:
            for titulo, setores in classificacoes.items():
                tmpl = template_por_titulo.get(titulo)
                if tmpl is None:
                    continue
                for setor in setores:
                    res = conn.execute(
                        insert_sql,
                        {
                            "titulo": titulo,
                            "link": tmpl.get("link") or None,
                            "data": tmpl.get("data") or None,
                            "texto": tmpl.get("texto") or None,
                            "setor": setor,
                            "fonte": tmpl.get("fonte") or None,
                            "modelo": LLM_MODEL,
                        },
                    )
                    inseridas += res.rowcount

        log.info("Inseridas %d linhas em %s.", inseridas, TABELA_DESTINO)
        return {"inseridas": inseridas, "titulos": len(classificacoes)}
    finally:
        engine.dispose()
