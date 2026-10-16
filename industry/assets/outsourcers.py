"""Frente outsourcers: quem poderia fazer outsourcing de MLOps, AI Engineering
e Research Operations.

Duas origens automaticas, mais o cadastro manual no home:
  - rf:     consultorias pequenas da base da Receita Federal (mesmo caminho dos
            prospects, filtros em scripts/receita_federal/filtros/outsourcers/)
  - github: donos de repositorios ativos com topicos de MLOps/LLMOps, com
            perfil publico (localizacao, bio, e-mail, hireable)

LinkedIn fica de fora da automacao: exige login e os termos de uso proibem
scraping. Perfis de la entram pelo formulario manual do home.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta

import dagster as dg

from ..supabase import Supabase
from .prospects import ler_exports

AGENDA_GITHUB = '0 8 * * 1'


@dg.asset(group_name='outsourcers', description=(
    'Faz upsert na tabela outsourcers das consultorias exportadas em '
    'scripts/receita_federal/dados/exports/outsourcers/*.parquet. Preserva status e notas.'))
def outsourcers_rf(context: dg.AssetExecutionContext, supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'outsourcers') as run:
        linhas = [{
            'id': f"rf:{r['cnpj']}", 'origem': 'rf',
            'nome': r['nome_fantasia'] or r['razao_social'], 'empresa': r['razao_social'],
            'localizacao': f"{r['municipio']}/{r['uf']}", 'url': None,
            'email': r['email'], 'telefone': r['telefone_1'], 'bio': r['cnae_descricao'],
            'sinais': {'cnpj': r['cnpj'], 'porte': r['porte'],
                       'capital_social': r['capital_social'],
                       'data_inicio': str(r['data_inicio']) if r['data_inicio'] else None},
            'segmento': r['segmento'],
        } for r in ler_exports('outsourcers')]
        run['linhas'] = supabase.upsert('outsourcers', linhas, 'id')
    return dg.MaterializeResult(metadata={'consultorias': len(linhas)})


class GithubConfig(dg.Config):
    # busca de pessoas: o GitHub casa o termo com login, nome e bio, e `location:` filtra
    termos: list[str] = ['mlops', 'llmops', 'machine learning engineer', 'ai engineer',
                         'llm engineer', 'operations research']
    topicos: list[str] = ['mlops', 'llmops', 'ml-engineering', 'model-serving',
                          'feature-store', 'rag', 'operations-research']
    # trecho que precisa aparecer na localizacao do perfil; lista vazia = qualquer lugar
    localizacoes: list[str] = ['brazil', 'brasil']
    pessoas_por_termo: int = 100
    ativo_nos_ultimos_dias: int = 180
    estrelas_min: int = 5
    max_perfis: int = 300   # cada perfil e uma chamada; sem token o limite e 60/h


def _gh(url, token):
    h = {'Accept': 'application/vnd.github+json', 'User-Agent': 'industry-outsourcers'}
    if token:
        h['Authorization'] = f'Bearer {token}'
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=30) as r:
        return json.loads(r.read())


@dg.asset(group_name='outsourcers', description=(
    'Busca no GitHub pessoas por termo (MLOps, LLM, AI/ML engineer, OR) e localizacao, '
    'mais donos de repositorios ativos com esses topicos; le o perfil publico e grava '
    'quem bate com a localizacao. '
    'Sinais: repositorios, topicos, estrelas, seguidores, hireable.'))
def outsourcers_github(context: dg.AssetExecutionContext, config: GithubConfig,
                       supabase: Supabase) -> dg.MaterializeResult:
    token = os.getenv('GITHUB_TOKEN')
    with supabase.execucao(context, 'outsourcers', AGENDA_GITHUB) as run:
        desde = (date.today() - timedelta(days=config.ativo_nos_ultimos_dias)).isoformat()
        donos = {}   # login -> sinais acumulados
        novo = lambda: {'repos': [], 'topicos': set(), 'termos': set(), 'estrelas': 0}
        pausa = 2.5 if token else 7   # search: 30/min com token, 10/min sem
        for termo in config.termos:
            for loc in config.localizacoes or ['']:
                q = f'"{termo}" type:user' + (f' location:{loc}' if loc else '')
                url = (f'https://api.github.com/search/users?per_page={config.pessoas_por_termo}'
                       '&sort=followers&q=' + urllib.parse.quote(q))
                for u in _gh(url, token).get('items', []):
                    donos.setdefault(u['login'], novo())['termos'].add(termo)
                time.sleep(pausa)
        for topico in config.topicos:
            q = f'topic:{topico} pushed:>={desde} stars:>={config.estrelas_min}'
            url = ('https://api.github.com/search/repositories?per_page=100&sort=updated&q='
                   + urllib.parse.quote(q))
            for repo in _gh(url, token).get('items', []):
                if repo['owner']['type'] != 'User':
                    continue
                d = donos.setdefault(repo['owner']['login'], novo())
                d['repos'].append(repo['full_name'])
                d['topicos'].add(topico)
                d['estrelas'] += repo['stargazers_count']
            time.sleep(pausa)

        locs = [l.lower() for l in config.localizacoes]
        linhas = []
        # quem veio da busca por pessoa ja bate com a localizacao: vai primeiro
        ordem = sorted(donos.items(), key=lambda kv: (-len(kv[1]['termos']), -kv[1]['estrelas'])
                       )[:config.max_perfis]
        for login, s in ordem:
            u = _gh(f'https://api.github.com/users/{login}', token)
            if locs and not any(l in (u.get('location') or '').lower() for l in locs):
                continue
            linhas.append({
                'id': f'github:{login}', 'origem': 'github', 'nome': u.get('name') or login,
                'empresa': u.get('company'), 'localizacao': u.get('location'),
                'url': u['html_url'], 'email': u.get('email'), 'telefone': None,
                'bio': u.get('bio'),
                'sinais': {'termos': sorted(s['termos']),
                           'repos': s['repos'][:10], 'topicos': sorted(s['topicos']),
                           'estrelas': s['estrelas'], 'seguidores': u.get('followers'),
                           'hireable': u.get('hireable'), 'blog': u.get('blog') or None},
                'segmento': 'github',
            })
        run['linhas'] = supabase.upsert('outsourcers', linhas, 'id')
        run['mensagem'] = f'{len(donos)} candidatos, {len(ordem)} perfis lidos'
    return dg.MaterializeResult(metadata={'donos': len(donos), 'gravados': len(linhas)})
