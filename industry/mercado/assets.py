"""Dagster: coleta diária e incremental do universo de compras públicas.

O backfill (meses de histórico) roda à mão, fora do Dagster, porque leva horas:
``python -m industry.mercado.coleta --dias 90``. O asset só mantém o período recente
em dia; como a coleta é resumível, ele não repete os dias já prontos.
"""
from datetime import date, timedelta

import dagster as dg

GRUPO = "mercado"


class ColetaConfig(dg.Config):
    dias: int = 10   # janela olhada a cada execução (os 7 mais recentes são sempre refeitos)


@dg.asset(group_name=GRUPO, kinds={"postgres"})
def mercado_coleta(context: dg.AssetExecutionContext, config: ColetaConfig) -> dg.MaterializeResult:
    """Contratações e contratos do PNCP e itens e resultados do Compras.gov.br no
    Postgres local (schema ``mercado``)."""
    from industry.mercado.coleta import coletar

    fim = date.today() - timedelta(days=1)
    total = coletar(fim - timedelta(days=config.dias - 1), fim)
    context.log.info(f"[mercado] {total}")
    return dg.MaterializeResult(metadata={k: int(v) for k, v in total.items()})


mercado_job = dg.define_asset_job("mercado_job", selection=dg.AssetSelection.groups(GRUPO))
mercado_schedule = dg.ScheduleDefinition(
    job=mercado_job,
    cron_schedule="0 3 * * *",   # madrugada: menos concorrência na API do PNCP
    execution_timezone="America/Sao_Paulo",
    default_status=dg.DefaultScheduleStatus.RUNNING,
)
ASSETS = [mercado_coleta]
