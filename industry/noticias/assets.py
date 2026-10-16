"""Assets Dagster das pipelines de notícias.

Porte de três DAGs Airflow do bateleur-pipelines:

- ``DATABASE_Noticias_COLETA``         -> ``noticias_coleta`` + ``noticias_limpas``
- ``DATABASE_Noticias_CLASSIFICACAO``  -> ``noticias_classificadas``
- ``DATABASE_Noticias_OperacoesMnA``   -> ``mna_operacoes`` + ``mna_relatorio``
- ``MARKETING_EnvioNoticias``          -> ``noticias_relatorios`` (e-mail desligado)

A coleta e a classificação rodam no mesmo job (na origem, a coleta disparava a
classificação via TriggerDagRun). O M&A tem job próprio de madrugada e consome o
backlog de notícias já classificadas como "M&A".

Setores ligados: Agro, Óleo e Gás, Automotivo, Tecnologia e Indústria (ver
coleta/config.SETORES_ATIVOS). GPU: a classificação usava um LLM local (Ollama)
e agora usa o mesmo modelo pelo roteador de chaves; o gate
zero-shot (mDeBERTa) do M&A roda em CPU, limitado por ``MNA_LIMIT`` por rodada.
Os prompts focam oportunidades de negócio e fatos de mercado para uma consultoria
de ML Engineering, Data Management e Data Engineering.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import dagster as dg

GRUPO = "noticias"
GRUPO_MNA = "noticias_mna"
GRUPO_RELATORIO = "noticias_relatorio"
# Paralelismo das fontes HTTP. Selenium e agrolink rodam em sequência (um
# navegador remoto só / mesmo site), como as cadeias da DAG de origem.
WORKERS_HTTP = 6


@dg.asset(group_name=GRUPO, kinds={"python", "postgres"})
def noticias_coleta(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Coleta as fontes dos setores ligados (``config.SETORES_ATIVOS``) para
    ``noticias.noticias_raw``.

    Falha de uma fonte não derruba as outras (era ``trigger_rule=all_done``): a
    saúde de cada execução vai para ``monitor.coleta_noticias``. O asset só falha
    se TODAS as fontes falharem.
    """
    from industry.noticias.coleta.coletas.configuracoes import JornalSelenium
    from industry.noticias.coleta.coletas.executar import executar_coleta
    from industry.noticias.coleta.config import jornais_ativos
    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

    from industry.shared import ddl

    ddl.aplicar(Path(__file__).with_name("schema.sql"), NOTICIAS_PG)
    run_id = context.run.run_id
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    jornais = jornais_ativos()
    sequenciais = [j for j in jornais
                   if isinstance(j, JornalSelenium) or j.fonte.startswith("agrolink")]
    paralelas = [j for j in jornais if j not in sequenciais]

    gravados: dict[str, int] = {}
    falhas: dict[str, str] = {}

    def rodar(j):
        try:
            gravados[j.fonte] = executar_coleta(j, run_id=run_id, engine=engine)["gravados"]
        except Exception as e:  # noqa: BLE001 — registrado em monitor.coleta_noticias
            falhas[j.fonte] = f"{type(e).__name__}: {e}"[:300]
            context.log.warning(f"[coleta] {j.fonte} falhou: {falhas[j.fonte]}")

    def rodar_sequencia():
        for j in sequenciais:
            rodar(j)

    with ThreadPoolExecutor(max_workers=WORKERS_HTTP + 1) as ex:
        futs = [ex.submit(rodar_sequencia)] + [ex.submit(rodar, j) for j in paralelas]
        for f in as_completed(futs):
            f.result()
    engine.dispose()

    if falhas and not gravados:
        raise dg.Failure(f"Todas as {len(falhas)} fontes falharam.",
                         metadata={"falhas": dg.MetadataValue.json(falhas)})
    return dg.MaterializeResult(metadata={
        "fontes_ok": len(gravados),
        "fontes_com_falha": len(falhas),
        "artigos_gravados": sum(gravados.values()),
        "falhas": dg.MetadataValue.json(falhas),
    })


@dg.asset(group_name=GRUPO, deps=[noticias_coleta], kinds={"postgres"})
def noticias_limpas() -> dg.MaterializeResult:
    """``noticias_raw`` do dia -> ``noticias.noticias`` (datas/títulos/texto
    normalizados) e remoção de títulos duplicados."""
    from industry.noticias.coleta.deduplicar_noticias import deduplicar_noticias
    from industry.noticias.coleta.salvar_dataframe_limpo import salvar_dataframe_limpo

    salvar_dataframe_limpo()
    deduplicar_noticias()
    return dg.MaterializeResult()


@dg.asset(group_name=GRUPO, deps=[noticias_limpas], kinds={"postgres", "groq"},
          retry_policy=dg.RetryPolicy(max_retries=2, delay=300))
def noticias_classificadas(context: dg.AssetExecutionContext) -> dg.MaterializeResult:
    """Classifica em setores (LLM pelo roteador de chaves) as notícias de hoje/ontem ainda não
    classificadas -> ``noticias.noticias_classificadas``."""
    from industry.noticias.classificacao import task as t

    t.garantir_tabela()
    registros = t.carregar_noticias_novas()
    classificacoes = t.classificar_titulos(registros)
    res = t.inserir_classificadas(classificacoes, registros)
    return dg.MaterializeResult(metadata={"a_classificar": len(registros),
                                          "classificadas": len(classificacoes),
                                          **{k: v for k, v in res.items()}})


class RelatorioConfig(dg.Config):
    quantos_dias_atras: int = 7
    numero_de_noticias: int = 5
    setores: list[str] = []          # vazio = config.SETORES
    dry_run: bool = False
    forcar_envio: bool = False


@dg.asset(group_name=GRUPO_RELATORIO, deps=[noticias_classificadas], kinds={"postgres", "llm"})
def noticias_relatorios(context: dg.AssetExecutionContext,
                        config: RelatorioConfig) -> dg.MaterializeResult:
    """Relatório por setor (texto por geografia Global/Brasil/Regional + fontes) em
    ``noticias.relatorios_email``. Nota de relevância voltada a oportunidades de
    negócio e fatos de mercado para consultoria de ML/dados. E-mail só com
    NOTICIAS_ENVIO_EMAIL=1 e SMTP no .env (desligado por padrão); vai para os
    destinatários fixos em envio/config.py, com todos os setores."""
    from industry.noticias.envio.classificador import task_classificador
    from industry.noticias.envio.relatorio import gerar_relatorios

    task_classificador(quantos_dias_atras=config.quantos_dias_atras,
                       setores=config.setores or None, run_id=context.run.run_id)
    res = gerar_relatorios(quantos_dias_atras=config.quantos_dias_atras,
                           numero_de_noticias=config.numero_de_noticias,
                           setores=config.setores or None, dry_run=config.dry_run,
                           forcar_envio=config.forcar_envio)
    return dg.MaterializeResult(metadata=res)


@dg.asset(group_name=GRUPO_MNA, deps=[noticias_classificadas], kinds={"postgres", "groq"},
          retry_policy=dg.RetryPolicy(max_retries=1, delay=300))
def mna_operacoes() -> dg.MaterializeResult:
    """Extrai operações de M&A das notícias do setor "M&A" (gate keyword +
    zero-shot em CPU, extração/validação/dedup por LLM) -> ``noticias.mna_operations``.
    Resumível: cada rodada retoma do que ficou pendente."""
    from industry.noticias.mna import task as t

    t.setup()
    stats = t.extrair() or {}
    return dg.MaterializeResult(metadata={k: v for k, v in stats.items()
                                          if isinstance(v, (int, float, str, bool))})


@dg.asset(group_name=GRUPO_MNA, deps=[mna_operacoes], kinds={"html"})
def mna_relatorio() -> dg.MaterializeResult:
    """Relatórios HTML/PDF das operações em ``$INDUSTRY_DATA_DIR/noticias_mna/``."""
    from industry.noticias.mna import task as t

    res = t.publicar_html() or {}
    return dg.MaterializeResult(metadata={"publicados": res.get("publicados", 0),
                                          "bytes": dg.MetadataValue.json(res.get("bytes", {})),
                                          "pdf_bytes": dg.MetadataValue.json(res.get("pdf_bytes", {}))})


ASSETS = [noticias_coleta, noticias_limpas, noticias_classificadas, noticias_relatorios,
          mna_operacoes, mna_relatorio]

noticias_job = dg.define_asset_job(
    "noticias_job",
    selection=dg.AssetSelection.assets(noticias_coleta, noticias_limpas, noticias_classificadas),
    description="Coleta, limpa e classifica notícias por setor.",
)
mna_job = dg.define_asset_job(
    "noticias_mna_job",
    selection=dg.AssetSelection.groups(GRUPO_MNA),
    description="Extrai operações de M&A das notícias classificadas e gera o relatório.",
)

relatorio_job = dg.define_asset_job(
    "noticias_relatorio_job",
    selection=dg.AssetSelection.groups(GRUPO_RELATORIO),
    description="Relatório de notícias por setor (e-mail desligado por padrão).",
)

noticias_schedule = dg.ScheduleDefinition(
    job=noticias_job, cron_schedule="0 5,20 * * *",
    execution_timezone="America/Sao_Paulo",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
relatorio_schedule = dg.ScheduleDefinition(
    job=relatorio_job, cron_schedule="0 8 * * *",
    execution_timezone="America/Sao_Paulo",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
mna_schedule = dg.ScheduleDefinition(
    job=mna_job, cron_schedule="30 3 * * *",
    execution_timezone="America/Sao_Paulo",
    default_status=dg.DefaultScheduleStatus.STOPPED,
)
