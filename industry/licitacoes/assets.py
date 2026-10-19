"""Assets Dagster da pipeline de licitações (medallion + score de relevância).

Porte da DAG Airflow ``COMERCIAL_Licitacoes`` (bateleur-pipelines):

    coletas (1 asset/fonte) -> bronze -> silver -> gold -> gold_filtros -> verticais
                                                              └-> score_atrasados / prazos

Diferenças em relação à DAG de origem:

- Dados entre etapas: o delta de cada camada é o VALOR do asset (IO manager do
  Dagster), não CSV no MinIO. Os arquivos brutos dos coletores vão para
  ``$INDUSTRY_DATA_DIR/licitacoes/<run_id>/``.
- ``trigger_rule="all_done"``: o Dagster não pula etapas a jusante de um passo
  que falhou só se ele não falhar — então cada coletor CAPTURA o próprio erro,
  registra no manifesto e termina; o asset check ``licitacoes_coletas_ok``
  (no bronze) fica vermelho listando as fontes que falharam ou vieram vazias.
  Faz o papel do ``alertar_falhas`` (que mandava e-mail).
- Sem e-mail: o resultado é lido pelo home (CRM pessoal) direto do Postgres.
- ``queue="gpu"``: não existe; o zero-shot roda em CPU (ver config.ZS_ATIVO).
"""

import importlib
import json
import logging
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import dagster as dg
import pandas as pd

from industry.shared import ddl, storage

logger = logging.getLogger(__name__)

GRUPO = "licitacoes"
GRUPO_COLETA = "licitacoes_coleta"
PREFIXO_MANIFESTO = "licitacoes/coletas"


@dataclass(frozen=True)
class Fonte:
    slug: str
    modulo: str
    funcao: str
    nome: str           # valor de `fonte` no bronze (chave de SOURCE_STABLE_ID)
    selenium: bool = False
    pncp: bool = False          # usa a API do PNCP (limite de requisições compartilhado)


FONTES = [
    Fonte("pncp", "pncp", "collect_pncp", "PNCP", pncp=True),
    Fonte("bcb", "bcb", "collect_bcb", "BCB - Banco Central do Brasil"),
    Fonte("brb", "brb", "collect_brb", "BRB - Banco de Brasília"),
    Fonte("banrisul", "banrisul_total", "collect_banrisul", "Banrisul"),
    Fonte("comprasnet", "comprasnet", "collect_comprasnet", "ComprasNet"),
    Fonte("banrisul_tce", "banrisul_tce", "collect_banrisul_tce",
          "Banrisul - TCE-RS (LicitaCon dados abertos)"),
    Fonte("bdmg", "bdmg", "collect_bdmg", "BDMG - Banco de Desenvolvimento de Minas Gerais"),
    Fonte("bndes", "bndes", "collect_bndes",
          "BNDES - Banco Nacional de Desenvolvimento Econômico e Social"),
    Fonte("b3", "b3", "collect_b3", "B3", selenium=True),
    Fonte("banestes", "banestes", "collect_banestes", "Banestes - Editais e Avisos", selenium=True),
    Fonte("banco_amazonia", "banco_amazonia", "collect_banco_amazonia",
          "Banco da Amazônia - Licitações", selenium=True),
    Fonte("caixa", "caixa", "collect_caixa",
          "CAIXA SICVE - Sistema de Compras Eletronicas", selenium=True),
    # fontes gerais (qualquer órgão), não só bancos
    Fonte("pncp_abertas", "pncp_abertas", "collect_pncp_abertas", "PNCP", pncp=True),
    Fonte("querido_diario", "querido_diario", "collect_querido_diario",
          "Querido Diário - Diários oficiais municipais"),
    Fonte("bndes_inovacao", "bndes_inovacao", "collect_bndes_inovacao",
          "BNDES - Chamadas públicas de inovação (CPSI)"),
    # fora do PNCP: Sistema S e estatal da Lei 13.303
    Fonte("sebrae", "sebrae", "collect_sebrae", "Sebrae - Canal do Fornecedor"),
    Fonte("petronect", "petronect", "collect_petronect", "Petronect - Petrobras e subsidiárias"),
    # internacionais que aceitam empresa do Brasil (PNUD e ONU também pessoa física)
    Fonte("banco_mundial", "banco_mundial", "collect_banco_mundial",
          "Banco Mundial - Avisos de aquisição"),
    Fonte("pnud", "pnud", "collect_pnud", "PNUD - Avisos de aquisição"),
    Fonte("ungm", "ungm", "collect_ungm", "UNGM - Agências da ONU"),
    # consultor individual (pessoa fisica) em projetos de cooperacao com ministerios
    Fonte("prodoc", "prodoc", "collect_prodoc",
          "PRODOC - Consultor individual em projetos de cooperação internacional"),
]
# banco_do_nordeste.py fica fora (como na origem): o download do CSV via Selenium
# remoto cai no container do Selenium, não no worker. O BNB é coberto pelo PNCP.


# ── helpers ──────────────────────────────────────────────────────────────────
def _contar_linhas(path: Path) -> int:
    try:
        return len(pd.read_csv(path, sep=";", dtype=str, keep_default_na=False))
    except pd.errors.EmptyDataError:
        return 0


def _como_texto(df: pd.DataFrame | None) -> pd.DataFrame:
    """Mesma tipagem do round-trip por CSV da origem (tudo str, vazio = "").

    O código das camadas foi escrito assumindo isso (fillna(""), comparações de
    string); o ``db.insert_df`` coage de volta para os tipos do banco.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=[] if df is None else df.columns)
    return df.astype(object).where(df.notna(), "").astype(str)


def _manifesto_key(slug: str) -> str:
    return f"{PREFIXO_MANIFESTO}/{slug}.json"


def _ler_manifestos() -> list[dict]:
    out = []
    for f in FONTES:
        p = storage.path_of(_manifesto_key(f.slug))
        if p.exists():
            out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


# ── Coleta (um asset por fonte) ──────────────────────────────────────────────
def _asset_coleta(fonte: Fonte):
    @dg.asset(
        name=f"licitacoes_coleta_{fonte.slug}",
        group_name=GRUPO_COLETA,
        kinds={"python"},
        op_tags=({"selenium": "true"} if fonte.selenium else {})
        | ({"pncp": "true"} if fonte.pncp else {}),
        description=f"Coleta bruta da fonte {fonte.nome} (CSV nativo do coletor).",
    )
    def _coleta(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
        run_id = context.run.run_id
        manifesto = {"slug": fonte.slug, "fonte": fonte.nome, "run_id": run_id,
                     "em": datetime.now().isoformat(timespec="seconds"),
                     "key": "", "linhas": 0, "erro": ""}
        try:
            mod = importlib.import_module(f"industry.licitacoes.{fonte.modulo}")
            with tempfile.TemporaryDirectory() as tmp:
                out_path = getattr(mod, fonte.funcao)(dest_dir=Path(tmp))
                manifesto["linhas"] = _contar_linhas(out_path)
                manifesto["key"] = storage.upload_file(
                    str(out_path), "licitacoes", run_id, out_path.name)
            context.log.info(f"[coleta:{fonte.nome}] {manifesto['linhas']} linha(s)")
        except Exception as e:  # noqa: BLE001 — ver docstring do módulo (all_done)
            manifesto["erro"] = f"{type(e).__name__}: {e}"[:600]
            context.log.error(f"[coleta:{fonte.nome}] FALHOU: {manifesto['erro']}")
        storage.write_fixed(_manifesto_key(fonte.slug),
                            json.dumps(manifesto, ensure_ascii=False).encode("utf-8"))
        return dg.MaterializeResult(metadata={
            "linhas": manifesto["linhas"],
            "erro": manifesto["erro"] or "—",
        })

    return _coleta


COLETAS = [_asset_coleta(f) for f in FONTES]


# ── Bronze ───────────────────────────────────────────────────────────────────
@dg.asset(group_name=GRUPO, deps=COLETAS, kinds={"postgres"})
def licitacoes_bronze(context: dg.AssetExecutionContext) -> dg.Output[pd.DataFrame]:
    """Normaliza a última coleta de cada fonte para o superset e insere os NOVOS
    (dedup por row_hash) em licitacoes.bronze. Valor: novos recentes."""
    from industry.licitacoes import bronze as bronze_mod
    from industry.shared.connections import LICITACOES_PG

    ddl.aplicar(Path(__file__).with_name("schema.sql"), LICITACOES_PG)
    dfs = []
    for m in _ler_manifestos():
        if not m.get("key") or m.get("erro"):
            continue
        try:
            nativo = pd.read_csv(storage.path_of(m["key"]), sep=";", dtype=str,
                                 keep_default_na=False)
            dfs.append(bronze_mod.normalizar_para_bronze(nativo, m["fonte"]))
        except Exception as e:  # noqa: BLE001
            context.log.warning(f"[bronze] falha lendo {m.get('fonte')}: {str(e)[:120]}")
    novos = bronze_mod.inserir_bronze(dfs, date.today())
    novos = _como_texto(bronze_mod.filtrar_recentes(novos))
    return dg.Output(novos, metadata={"novos": len(novos)})


@dg.asset_check(asset=licitacoes_bronze, description=(
    "Falha se algum coletor da última rodada deu erro; avisa se veio vazio."))
def licitacoes_coletas_ok() -> dg.AssetCheckResult:
    ms = _ler_manifestos()
    falhas = {m["fonte"]: m["erro"] for m in ms if m.get("erro")}
    vazias = [m["fonte"] for m in ms if not m.get("erro") and int(m.get("linhas") or 0) == 0]
    return dg.AssetCheckResult(
        passed=not falhas and not vazias,
        severity=dg.AssetCheckSeverity.ERROR if falhas else dg.AssetCheckSeverity.WARN,
        metadata={"falhas": json.dumps(falhas, ensure_ascii=False) or "{}",
                  "vazias": ", ".join(vazias) or "—"},
    )


# ── Silver ───────────────────────────────────────────────────────────────────
@dg.asset(group_name=GRUPO, kinds={"postgres"})
def licitacoes_silver(licitacoes_bronze: pd.DataFrame) -> dg.Output[pd.DataFrame]:
    from industry.licitacoes import silver as silver_mod

    if licitacoes_bronze.empty:
        return dg.Output(pd.DataFrame(), metadata={"novos": 0})
    silver = _como_texto(silver_mod.inserir_silver(licitacoes_bronze))
    return dg.Output(silver, metadata={"novos": len(silver)})


# ── Gold (filtro de palavras + "acionável") ──────────────────────────────────
@dg.asset(group_name=GRUPO, kinds={"postgres"})
def licitacoes_gold(context: dg.AssetExecutionContext,
                    licitacoes_silver: pd.DataFrame) -> dg.Output[pd.DataFrame]:
    from industry.licitacoes import anexos as anexos_mod
    from industry.licitacoes import gold as gold_mod
    from industry.licitacoes.config import FILTRAR_ANEXO

    if licitacoes_silver.empty:
        return dg.Output(pd.DataFrame(), metadata={"novos": 0, "acionaveis": 0})
    cand = gold_mod.filtrar_objeto(licitacoes_silver)
    if FILTRAR_ANEXO and not cand.empty:
        textos = anexos_mod.textos_por_id(cand["id_unificado"].tolist(),
                                          "licitacoes", context.run.run_id)
        cand = gold_mod.filtrar_anexo(cand, textos)
    gold = gold_mod.inserir_gold(cand)
    n_gold = len(gold)
    # Corte FINO "acionável": só segue ao score o que tem janela de proposta
    # aberta (ou recente sem prazo). A tabela gold guarda tudo. Ver DECISOES.md §6.
    gold = _como_texto(gold_mod.filtrar_acionaveis(gold))
    return dg.Output(gold, metadata={"novos": n_gold, "acionaveis": len(gold)})


# ── Gold_filtros (score kw + zero-shot + LLM) ────────────────────────────────
@dg.asset(group_name=GRUPO, kinds={"postgres", "groq"})
def licitacoes_gold_filtros(licitacoes_gold: pd.DataFrame) -> dg.Output[pd.DataFrame]:
    from industry.licitacoes import score as score_mod

    if licitacoes_gold.empty:
        return dg.Output(pd.DataFrame(), metadata={"pontuadas": 0})
    scored = score_mod.inserir_gold_filtros(licitacoes_gold, run_date=date.today(),
                                            usar_llm=True)
    relevantes = int((pd.to_numeric(scored.get("relevante_combinado"),
                                    errors="coerce") == 1).sum()) if not scored.empty else 0
    return dg.Output(scored, metadata={"pontuadas": len(scored), "relevantes": relevantes})


# ── Verticais (classificação do objeto por LLM) ──────────────────────────────
@dg.asset(group_name=GRUPO, deps=[licitacoes_gold_filtros], kinds={"postgres", "groq"})
def licitacoes_verticais(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Classifica em UMA vertical o objeto das licitações do gold ainda sem
    vertical (``licitacoes.verticais``). Resumível; teto VERTICAIS_LIMITE por
    execução por causa da cota diária de tokens da Groq."""
    from industry.licitacoes.verticais_runner import classificar_pendentes

    return dg.MaterializeResult(metadata=classificar_pendentes(context.log))


# ── Manutenção de backlog ────────────────────────────────────────────────────
MAX_SCORE_ATRASADO = 200


@dg.asset(group_name=GRUPO, deps=[licitacoes_verticais], kinds={"postgres", "groq"})
def licitacoes_score_atrasados(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Pontua linhas do gold que nunca passaram pelo gold_filtros (ou ficaram com
    score NULL). Teto MAX_SCORE_ATRASADO por execução. Ver db.gold_sem_score."""
    from industry.licitacoes import db
    from industry.licitacoes import score as score_mod

    atrasados = db.gold_sem_score(MAX_SCORE_ATRASADO)
    if atrasados.empty:
        return dg.MaterializeResult(metadata={"pendentes": 0, "pontuadas": 0})
    repontuadas = db.apagar_gold_filtros(atrasados["id_unificado"].tolist())
    scored = score_mod.inserir_gold_filtros(_como_texto(atrasados), run_date=date.today(),
                                            usar_llm=True)
    context.log.info(f"[atrasados] {len(atrasados)} sem score -> {len(scored)} pontuadas")
    return dg.MaterializeResult(metadata={"pendentes": len(atrasados), "pontuadas": len(scored),
                                          "repontuadas": int(repontuadas)})


@dg.asset(group_name=GRUPO, deps=[licitacoes_gold_filtros], kinds={"postgres"},
          op_tags={"pncp": "true"})
def licitacoes_prazos(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Consulta no PNCP o prazo de proposta dos itens do gold_filtros que ainda
    não têm (``licitacoes.prazos_proposta``)."""
    from industry.licitacoes import gold as gold_mod

    res = gold_mod.backfill_prazos()
    return dg.MaterializeResult(metadata={k: v for k, v in (res or {}).items()
                                          if isinstance(v, (int, float, str))})


@dg.asset(group_name=GRUPO, deps=[licitacoes_gold_filtros, licitacoes_score_atrasados],
          kinds={"postgres"}, op_tags={"pncp": "true"})
def licitacoes_detalhes(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Completa os itens pontuados com o que as listagens não trazem: critério de
    julgamento, ME/EPP, registro de preços, amparo legal, anexos e valor estimado
    (PNCP e, sem valor, o PDF do edital). Ver detalhes.py."""
    from industry.licitacoes.detalhes import coletar_detalhes

    return dg.MaterializeResult(metadata=coletar_detalhes())


@dg.asset(group_name=GRUPO, deps=[licitacoes_detalhes], kinds={"postgres"})
def licitacoes_nota_tecnica(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Divide a nota técnica das licitações abertas entre histórico e proposta, por
    regra explícita (critério de julgamento e pontos lidos no edital). Ver nota_tecnica.py."""
    from industry.licitacoes.nota_tecnica import calcular

    return dg.MaterializeResult(metadata=calcular())


ASSETS = [*COLETAS, licitacoes_bronze, licitacoes_silver, licitacoes_gold,
          licitacoes_gold_filtros, licitacoes_verticais, licitacoes_score_atrasados,
          licitacoes_prazos, licitacoes_detalhes, licitacoes_nota_tecnica]
CHECKS = [licitacoes_coletas_ok]

licitacoes_job = dg.define_asset_job(
    "licitacoes_job",
    selection=dg.AssetSelection.groups(GRUPO, GRUPO_COLETA),
    description="Coleta, filtra e pontua licitações (ML, IA, ciência de dados, forecasting e dados).",
    executor_def=dg.multiprocess_executor.configured({
        "max_concurrent": 4,
        # Um navegador por vez no Selenium remoto (era o selenium_pool da origem).
        "tag_concurrency_limits": [{"key": "selenium", "value": "true", "limit": 1},
                                   # a API do PNCP bloqueia rajadas (ver config)
                                   {"key": "pncp", "value": "true", "limit": 1}],
    }),
)

# Ligada: coleta diária e incremental (no máximo RETRO_DIAS para trás).
licitacoes_schedule = dg.ScheduleDefinition(
    job=licitacoes_job,
    cron_schedule="0 10 * * *",
    execution_timezone="America/Sao_Paulo",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
