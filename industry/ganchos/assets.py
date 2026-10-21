"""Assets do grupo `ganchos`: um por fonte, cada um grava na tabela `ganchos`.

    ganchos_vagas          semanal   LinkedIn e Gupy pelo nome da empresa
    ganchos_rf             semanal   socios, capital, CNAE e regime (minhareceita.org)
    ganchos_sites          semanal   sitemap e PDFs do site
    ganchos_filiais        mensal    zips de Estabelecimentos da RF (~5 GB de passagem pelo HD)
    ganchos_financiamento  mensal    dados abertos do BNDES
"""
import dagster as dg

from ..supabase import Supabase
from . import comum, filiais, financiamento, rf, sites, vagas

SEMANAL = '0 6 * * 1'
MENSAL = '0 3 5 * *'   # dia 5: a RF e o BNDES ja publicaram o mes


def _asset(nome, modulo, agenda, descricao):
    @dg.asset(name=nome, group_name='ganchos', description=descricao)
    def _a(context: dg.AssetExecutionContext, supabase: Supabase) -> dg.MaterializeResult:
        with supabase.execucao(context, comum.FRENTE, agenda) as run:
            res = modulo.coletar(supabase, context.log)
            run['linhas'] = res.get('novos')
            run['mensagem'] = ', '.join(f'{k} {v}' for k, v in res.items())
        return dg.MaterializeResult(metadata=res)
    return _a


ganchos_vagas = _asset('ganchos_vagas', vagas, SEMANAL,
                       'Vagas abertas das empresas da página Clientes (LinkedIn e painel da Gupy) cujo título '
                       'revela dor de dados ou de documentação.')
ganchos_rf = _asset('ganchos_rf', rf, SEMANAL,
                    'Mudanças na Receita: sócio novo, saída do Simples, regime tributário, capital e CNAE novos.')
ganchos_sites = _asset('ganchos_sites', sites, SEMANAL,
                       'PDFs e páginas novos ou atualizados no site, pelo sitemap e pelo Last-Modified.')
ganchos_filiais = _asset('ganchos_filiais', filiais, MENSAL,
                         'Filiais abertas, lidas dos zips mensais de Estabelecimentos da Receita.')
ganchos_financiamento = _asset('ganchos_financiamento', financiamento, MENSAL,
                               'Operações de financiamento do BNDES contratadas nos últimos 24 meses.')

ASSETS = [ganchos_vagas, ganchos_rf, ganchos_sites, ganchos_filiais, ganchos_financiamento]

ganchos_semanal_job = dg.define_asset_job(
    'ganchos_semanal', selection=[ganchos_vagas, ganchos_rf, ganchos_sites],
    description='Ganchos semanais dos prospects clientes: vagas, Receita e sites.')
ganchos_mensal_job = dg.define_asset_job(
    'ganchos_mensal', selection=[ganchos_filiais, ganchos_financiamento],
    description='Ganchos mensais dos prospects clientes: filiais na Receita e financiamentos do BNDES.')
JOBS = [ganchos_semanal_job, ganchos_mensal_job]
SCHEDULES = [
    dg.ScheduleDefinition(job=ganchos_semanal_job, cron_schedule=SEMANAL,
                          execution_timezone='America/Sao_Paulo', default_status=dg.DefaultScheduleStatus.RUNNING),
    dg.ScheduleDefinition(job=ganchos_mensal_job, cron_schedule=MENSAL,
                          execution_timezone='America/Sao_Paulo', default_status=dg.DefaultScheduleStatus.RUNNING),
]
