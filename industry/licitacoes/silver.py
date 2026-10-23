"""Camada SILVER — processamento do bronze para o arquivo limpo (10 colunas).

Recebe as linhas NOVAS do bronze e as adequa ao schema do silver:
  id_unificado, fonte, tipo, objeto, situacao, data (date), valor (numeric), link,
  contratado, numero

Regras de processamento (comentadas):
  - `data`  : escolhe a melhor data disponível (data > data_publicacao > abertura)
              e tipa para DATE (strings não parseáveis viram NULL; o texto cru
              permanece no bronze).
  - `valor` : escolhe o melhor valor (valor > estimado > contratado > homologado)
              e tipa para NUMERIC (pt-BR ou US); 0/vazio viram NULL.
  - `objeto`: objeto canônico do bronze (já unificado de objeto/conteudo/descrição…).
  - demais : cópia direta do bronze.
Todas as entradas novas entram no silver (sem filtro — o filtro é no gold).
"""
from __future__ import annotations

import logging

import pandas as pd

from industry.licitacoes.config import FONTES_INOVACAO, SILVER_COLS, TB_SILVER
from industry.licitacoes import db
from industry.licitacoes.termos import e_cpsi, termos_encontrados

logger = logging.getLogger(__name__)


def bronze_para_silver(df_bronze: pd.DataFrame) -> pd.DataFrame:
    """Transforma linhas do bronze no formato limpo/tipado do silver."""
    if df_bronze is None or df_bronze.empty:
        return pd.DataFrame(columns=SILVER_COLS)
    b = df_bronze.fillna("")

    def melhor_data(r):
        for c in ("data", "data_publicacao", "data_abertura", "data_sessao"):
            d = db.to_date(r.get(c))
            if d:
                return d
        return None

    def melhor_valor(r):
        for c in ("valor", "valor_estimado", "valor_homologado", "valor_contratado"):
            v = db.to_numeric(r.get(c))
            if v is not None:
                return v
        return None

    out = pd.DataFrame({
        "id_unificado": b["id_unificado"],
        "fonte": b["fonte"],
        "tipo": b.get("tipo", ""),
        "objeto": b.get("objeto", ""),
        "situacao": b.get("situacao", ""),
        "data": b.apply(melhor_data, axis=1),
        "valor": b.apply(melhor_valor, axis=1),
        "link": b.get("link", ""),
        "contratado": b.get("contratado", ""),
        "numero": b.get("numero", ""),
        "no_tema": b.get("objeto", pd.Series("", index=b.index)).map(
            lambda t: bool(termos_encontrados(t)) or e_cpsi(t)) | b["fonte"].isin(FONTES_INOVACAO),
    })
    # strings vazias -> None (colunas de texto) para consistência no banco
    for c in ("tipo", "objeto", "situacao", "link", "contratado", "numero"):
        out[c] = out[c].apply(lambda x: (str(x).strip() or None))
    return out


def inserir_silver(df_bronze: pd.DataFrame) -> pd.DataFrame:
    """Processa os novos do bronze e insere no silver. Retorna o df do silver."""
    silver = bronze_para_silver(df_bronze)
    if silver.empty:
        logger.info("[silver] nada a inserir")
        return silver
    # não reinsere ids já presentes (idempotência entre re-runs)
    existentes = db.existing_ids(TB_SILVER)
    silver = silver[~silver["id_unificado"].isin(existentes)].copy()
    db.insert_df(silver, TB_SILVER, SILVER_COLS)
    return silver
