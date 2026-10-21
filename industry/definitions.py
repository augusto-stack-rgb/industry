import dagster as dg

from .assets import jobs, outsourcers, prospects
from .ganchos import assets as ganchos
from .licitacoes import assets as licitacoes
from .noticias import assets as noticias
from .supabase import Supabase

vagas_job = dg.define_asset_job('vagas', selection=dg.AssetSelection.groups('jobs'))
github_job = dg.define_asset_job('github',
                                 selection=[outsourcers.outsourcers_github])

defs = dg.Definitions(
    assets=[*dg.load_assets_from_modules([jobs, prospects, outsourcers]),
            *licitacoes.ASSETS, *noticias.ASSETS, *ganchos.ASSETS],
    asset_checks=licitacoes.CHECKS,
    jobs=[vagas_job, github_job,
          licitacoes.licitacoes_job, noticias.noticias_job, noticias.mna_job,
          noticias.relatorio_job, *ganchos.JOBS],
    # prospects_supabase e outsourcers_rf nao tem agenda: rodam a mao depois
    # de um filtrar.py em scripts/receita_federal
    schedules=[
        dg.ScheduleDefinition(job=vagas_job, cron_schedule=jobs.AGENDA,
                              execution_timezone='America/Sao_Paulo'),
        dg.ScheduleDefinition(job=github_job, cron_schedule=outsourcers.AGENDA_GITHUB,
                              execution_timezone='America/Sao_Paulo'),
        # licitacoes: diaria as 10h, ligada; noticias: desligadas (ligar na UI)
        licitacoes.licitacoes_schedule, noticias.noticias_schedule, noticias.mna_schedule,
        noticias.relatorio_schedule, *ganchos.SCHEDULES,
    ],
    resources={'supabase': Supabase(db_url=dg.EnvVar('SUPABASE_DB_URL'))},
)
