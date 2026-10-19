"""Frente jobs: roda os coletores do repositorio employment, classifica as
descricoes por LLM e sobe as vagas recentes para a secao Vagas do home.

    vagas_coletadas -> vagas_classificadas -> vagas_supabase
"""
import csv
import importlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import dagster as dg

from ..supabase import Supabase

AGENDA = '0 7 * * *'
EMPLOYMENT = Path(os.getenv('EMPLOYMENT_DIR', Path(__file__).resolve().parents[3] / 'employment'))
COLETORES = EMPLOYMENT / 'employment' / 'coletores'
COLETAS = EMPLOYMENT / 'employment' / 'coletas'
CLASSIFICACAO = EMPLOYMENT / 'employment' / 'classificacao'
CLASSIFICADAS = CLASSIFICACAO / 'classificacao_coletas.csv'
ENV = Path(__file__).resolve().parents[2] / '.env'


def _importar(nome, pasta=COLETORES):
    if str(pasta) not in sys.path:
        sys.path.insert(0, str(pasta))
    return importlib.import_module(nome)


class ColetaConfig(dg.Config):
    # gupy_empresa fica de fora: traz todas as vagas de uma empresa, sem termo
    fontes: list[str] = ['himalayas', 'gupy', 'greenhouse', 'arbeitnow',
                         'remoteok', 'remotive', 'hackernews', 'enlizt']
    termos: str = 'dados'   # conjunto em employment/coletores/termos.py


@dg.asset(group_name='jobs', description=(
    'Roda os coletores de employment (um por fonte) com um conjunto de termos e '
    'acrescenta em employment/coletas/<fonte>.json sem repetir id.'))
def vagas_coletadas(context: dg.AssetExecutionContext, config: ColetaConfig,
                    supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'jobs', AGENDA) as run:
        base, termos = _importar('base'), _importar('termos')
        lista = termos.CONJUNTOS[config.termos]
        resumo, novos_total, falhas = {}, 0, []
        for fonte in config.fontes:
            try:
                regs = _importar('fonte_' + fonte).coletar(termos=lista)
                _, novos, total = base.salvar(fonte, regs)
                resumo[fonte] = novos
                novos_total += novos
                context.log.info(f'{fonte}: {novos} novas, {total} no arquivo')
            except Exception as e:   # uma fonte fora do ar nao derruba as outras
                falhas.append(fonte)
                context.log.warning(f'{fonte} falhou: {type(e).__name__}: {e}')
        if len(falhas) == len(config.fontes):
            raise RuntimeError('todas as fontes falharam')
        run['linhas'] = novos_total
        run['mensagem'] = 'falharam: ' + ', '.join(falhas) if falhas else None
    return dg.MaterializeResult(metadata={'novas': novos_total, **resumo})


def _num(v):
    try:
        return float(v) if v not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _vaga(r):
    return {
        'id': f"{r['fonte']}:{r['id']}", 'fonte': r['fonte'], 'url': r.get('url'),
        'title': r.get('title'), 'company': r.get('company'),
        'location': r.get('location'), 'country': r.get('country'),
        'remote': r.get('remote') if isinstance(r.get('remote'), bool) else None,
        'seniority': r.get('seniority'),
        'salary_min': _num(r.get('salary_min')), 'salary_max': _num(r.get('salary_max')),
        'salary_currency': r.get('salary_currency'), 'salary_period': r.get('salary_period'),
        'posted': r.get('posted'), 'description': (r.get('description') or '')[:4000] or None,
        'termo_busca': r.get('termo_busca'), 'coletado_em': r.get('coletado_em'),
    }


class ClassificacaoConfig(dg.Config):
    # Groq (gpt-oss-120b), Cerebras e Mistral (ministral-14b), um worker por
    # chave; as chaves sao GROQ_API_KEY_N, CEREBRAS_API_KEY_N e MISTRAL_API_KEY_N
    provedores: list[str] = ['groq', 'cerebras', 'mistral']
    limite: int | None = None   # teto de vagas por execucao; None classifica tudo que falta


@dg.asset(group_name='jobs', deps=[vagas_coletadas], description=(
    'Le a DESCRICAO de cada vaga ainda nao classificada e grava, por LLM, a funcao '
    'real (que muitas vezes difere do titulo), os componentes do trabalho (ex.: Data '
    'Engineer com MLOps), o setor do empregador e a modalidade, em '
    'employment/classificacao/classificacao_coletas.csv. Incremental: pula ids ja feitos.'))
def vagas_classificadas(context: dg.AssetExecutionContext, config: ClassificacaoConfig,
                        supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'jobs', AGENDA) as run:
        cls = _importar('classificar_coletas', CLASSIFICACAO)
        r = cls.classificar(env=str(ENV), saida=CLASSIFICADAS, provedores=config.provedores,
                            limite=config.limite, log=context.log.info, intervalo_log=60)
        if r['pendentes'] and not r['chaves']:
            raise RuntimeError('ha vagas pendentes e nenhuma chave de LLM no ambiente')
        if r['pendentes'] and not r['classificadas']:
            raise RuntimeError(f'nenhuma vaga classificada; chaves fora: {r["aposentadas"]}')
        run['linhas'] = r['classificadas']
        run['mensagem'] = (f"{r['restantes']} ficaram para a proxima execucao"
                           if r['restantes'] else None)
    return dg.MaterializeResult(metadata={k: v for k, v in r.items() if k != 'aposentadas'})


def _classificacao():
    """id na fonte -> campos do LLM, mais o nivel normalizado (estagio..lead)."""
    if not CLASSIFICADAS.exists():
        return {}
    with CLASSIFICADAS.open(encoding='utf-8') as f:
        return {l['id']: l for l in csv.DictReader(f)}


class SyncConfig(dg.Config):
    dias: int = 30   # so vagas coletadas nesta janela sobem (limite de 500 MB do Supabase)


@dg.asset(group_name='jobs', deps=[vagas_classificadas], description=(
    'Sobe para a tabela jobs do home as vagas de employment/coletas coletadas nos '
    'ultimos N dias, com funcao, componentes, setor, modalidade e nivel. Preserva '
    'status e notas editados no home.'))
def vagas_supabase(context: dg.AssetExecutionContext, config: SyncConfig,
                   supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'jobs', AGENDA) as run:
        corte = (date.today() - timedelta(days=config.dias)).isoformat()
        cls = _classificacao()
        nivel = _importar('nivel', CLASSIFICACAO).nivel
        vagas = {}
        for arq in sorted(COLETAS.glob('*.json')):
            # .himalayas_cursor.json e .solides_pagina.json sao estado dos
            # coletores, nao vagas; o glob do pathlib nao pula arquivo oculto
            if arq.name.startswith('.'):
                continue
            for r in json.loads(arq.read_text(encoding='utf-8')):
                if r.get('id') and r.get('fonte') and (r.get('coletado_em') or '') >= corte:
                    v = _vaga(r)
                    c = cls.get(r['id'], {})
                    v.update(funcao=c.get('categoria') or None,
                             componentes=c.get('componentes') or None,
                             setor=c.get('setor') or None,
                             modalidade=c.get('modalidade') or None,
                             nivel=nivel(r.get('seniority'), r.get('title')))
                    vagas[v['id']] = v
        run['linhas'] = supabase.upsert('jobs', list(vagas.values()), 'id')
    com = sum(1 for v in vagas.values() if v['funcao'])
    return dg.MaterializeResult(metadata={'vagas': len(vagas), 'classificadas': com})
