"""Frente prospects: sobe para o home os recortes da base da Receita Federal.

A coleta e o filtro da RF NAO sao DAG: rodam a mao em
codigos/scripts/receita_federal (baixar.py, carregar.py, filtrar.py). Este
asset so le os parquets que filtrar.py exporta.
"""
import os
from pathlib import Path

import dagster as dg
import duckdb

from ..supabase import Supabase

# a base da RF fica no HD (D:), nunca no SSD; ver scripts/receita_federal
EXPORTS = Path(os.getenv('RF_EXPORTS_DIR', 'D:/receita_federal/exports'))


def ler_exports(pasta: str) -> list[dict]:
    arquivos = sorted((EXPORTS / pasta).glob('*.parquet'))
    if not arquivos:
        raise FileNotFoundError(f'nenhum parquet em {EXPORTS / pasta}; rode filtrar.py')
    lista = ', '.join(f"'{a.as_posix()}'" for a in arquivos)
    rel = duckdb.sql(f'select * from read_parquet([{lista}])')
    return [dict(zip(rel.columns, linha)) for linha in rel.fetchall()]


@dg.asset(group_name='prospects', description=(
    'Faz upsert na tabela prospects do home de todos os recortes em '
    'scripts/receita_federal/dados/exports/prospects/*.parquet (um por filtro). '
    'Preserva status, notas, site e contato editados no home.'))
def prospects_supabase(context: dg.AssetExecutionContext,
                       supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'prospects') as run:
        linhas = ler_exports('prospects')
        run['linhas'] = supabase.upsert('prospects', linhas, 'cnpj')
        por_segmento = {}
        for l in linhas:
            por_segmento[l['segmento']] = por_segmento.get(l['segmento'], 0) + 1
        context.log.info(f'{len(linhas)} prospects: {por_segmento}')
    return dg.MaterializeResult(metadata={'prospects': len(linhas), **por_segmento})


@dg.asset(group_name='prospects', description=(
    'Lista CHATBOT (separada dos prospects): faz upsert na tabela prospects_chatbot '
    'do recorte filtros/chatbot/sul.sql (clinicas, imobiliarias, educacao, '
    'concessionarias... so empresas grandes). Preserva o que o home edita.'))
def prospects_chatbot_supabase(context: dg.AssetExecutionContext,
                               supabase: Supabase) -> dg.MaterializeResult:
    with supabase.execucao(context, 'prospects') as run:
        linhas = ler_exports('chatbot')
        run['linhas'] = supabase.upsert('prospects_chatbot', linhas, 'cnpj')
        por_segmento = {}
        for l in linhas:
            por_segmento[l['segmento']] = por_segmento.get(l['segmento'], 0) + 1
        context.log.info(f'{len(linhas)} prospects da lista chatbot: {por_segmento}')
    return dg.MaterializeResult(metadata={'prospects_chatbot': len(linhas), **por_segmento})
