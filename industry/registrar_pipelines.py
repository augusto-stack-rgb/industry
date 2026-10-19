"""Registra no Supabase (tabela `pipelines`) as definições que o home mostra na
página Pipelines, lidas das próprias Definitions do Dagster.

    python -m industry.registrar_pipelines

Os assets de vagas, prospects e outsourcers se registram sozinhos quando rodam
(Supabase.execucao). Licitações e notícias não passam por ali, então entram
aqui no nível do job, com a agenda do schedule. Não mexe em `ultima_execucao`.
"""
import os

import psycopg

from industry.definitions import defs

# job ou asset -> frente no home
JOBS = {
    'licitacoes_job': 'licitacoes', 'noticias_job': 'noticias',
    'noticias_mna_job': 'noticias', 'noticias_relatorio_job': 'noticias',
}
ASSETS = {'vagas_coletadas': 'jobs', 'vagas_classificadas': 'jobs', 'vagas_supabase': 'jobs',
          'prospects_supabase': 'prospects', 'outsourcers_rf': 'outsourcers',
          'outsourcers_github': 'outsourcers'}


def main():
    repo = defs.get_repository_def()
    agendas = {s.job_name: s.cron_schedule for s in repo.schedule_defs}
    linhas = []
    for nome, frente in JOBS.items():
        job = repo.get_job(nome)
        linhas.append((nome, frente, job.description, agendas.get(nome)))
    grafo = repo.asset_graph
    job_de = {'jobs': 'vagas', 'outsourcers': 'github'}
    for chave in grafo.get_all_asset_keys():
        nome = chave.to_user_string()
        if nome in ASSETS:
            desc = grafo.get(chave).description
            agenda = agendas.get(job_de.get(ASSETS[nome])) if nome != 'outsourcers_rf' and nome != 'prospects_supabase' else None
            linhas.append((nome, ASSETS[nome], desc, agenda))
    with psycopg.connect(os.environ['SUPABASE_DB_URL'], autocommit=True) as c:
        c.cursor().executemany(
            'insert into pipelines (nome, frente, descricao, agenda) values (%s, %s, %s, %s) '
            'on conflict (nome) do update set frente = excluded.frente, descricao = excluded.descricao, '
            'agenda = excluded.agenda', linhas)
    for l in linhas:
        print(f'{l[0]:<24} {l[1]:<12} {l[3] or "manual"}')


if __name__ == '__main__':
    main()
