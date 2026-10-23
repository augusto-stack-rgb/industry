"""Camada GOLD — aplica o filtro de PALAVRAS_EXCLUIR (no objeto E no texto dos
anexos) sobre as entradas novas do silver e insere as que sobrevivem em
licitacoes.gold.

Duas etapas (nessa ordem, de propósito):
  1) filtra pelo OBJETO (substring, case-insensitive — texto curto);
  2) só para os sobreviventes, baixa/analisa o ANEXO e filtra pelo texto do PDF
     (\\b word-boundary — texto longo, evita casar substring dentro de palavra).
Assim NÃO se baixa anexo de linha já eliminada pelo objeto (economia + menos 404).
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date

import pandas as pd
import requests

from industry.licitacoes.config import (
    PALAVRAS_EXCLUIR, GOLD_COLS, TB_GOLD, RECENCIA_DIAS, FONTES_INOVACAO,
)
from industry.licitacoes import db
from industry.licitacoes.termos import e_cpsi, termos_encontrados, termos_nucleo

logger = logging.getLogger(__name__)

# Ambos usam word-boundary (\b). Antes o objeto usava SUBSTRING, o que fazia
# palavras curtas casarem dentro de outras (ex.: "Sol" dentro de "solução",
# matando ~37 licitações relevantes silenciosamente). Ver DECISOES.md §13.
_OBJ = [re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE) for p in PALAVRAS_EXCLUIR]  # objeto: \b
_ANX = [re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE) for p in PALAVRAS_EXCLUIR]  # anexo: \b


def _objeto_sujo(texto: str) -> bool:
    """Tem termo excluído E nenhum termo forte do núcleo (TEMAS). "Câmeras com
    inteligência artificial para vigilância patrimonial" não sai por "Vigilância"."""
    return any(rx.search(texto or "") for rx in _OBJ) and not termos_nucleo(texto)


def _anexo_sujo(texto: str) -> bool:
    return any(rx.search(texto or "") for rx in _ANX)


def filtrar_tema(df_silver: pd.DataFrame) -> pd.DataFrame:
    """Etapa 0: fica só o que casa com os temas (TEMAS + TERMOS_TI, termos.py).

    As fontes amplas (PNCP aberto, Querido Diário, Sebrae, Petronect) já filtram
    na coleta; as de bancos (Banrisul, Banestes, BRB, BCB, ComprasNet, PNCP por
    CNPJ) trazem todas as compras. Medido em 19/10/2026: 183 de 797 do silver no
    tema; o resto ia para o filtro de palavras e para o LLM.
    """
    if df_silver is None or df_silver.empty:
        return pd.DataFrame(columns=GOLD_COLS)
    mask = df_silver["objeto"].fillna("").map(lambda t: bool(termos_encontrados(t)) or e_cpsi(t))
    if "fonte" in df_silver.columns:   # portais de inovação passam direto (config)
        mask = mask | df_silver["fonte"].isin(FONTES_INOVACAO)
    logger.info(f"[gold] filtro tema: {len(df_silver)} -> {int(mask.sum())}")
    return df_silver[mask].copy()


def filtrar_objeto(df_silver: pd.DataFrame) -> pd.DataFrame:
    """Etapa 1: remove linhas cujo OBJETO bate PALAVRAS_EXCLUIR."""
    if df_silver is None or df_silver.empty:
        return pd.DataFrame(columns=GOLD_COLS)
    mask = df_silver["objeto"].fillna("").map(_objeto_sujo)
    keep = df_silver[~mask].copy()
    logger.info(f"[gold] filtro objeto: {len(df_silver)} -> {len(keep)} "
                f"(removidas: {int(mask.sum())})")
    return keep


def filtrar_anexo(df: pd.DataFrame, textos_anexos: dict[str, str]) -> pd.DataFrame:
    """Etapa 2: remove linhas cujo ANEXO (texto do PDF) bate PALAVRAS_EXCLUIR."""
    if df is None or df.empty:
        return df if df is not None else pd.DataFrame(columns=GOLD_COLS)
    mask = df["id_unificado"].map(lambda i: _anexo_sujo(textos_anexos.get(i, "")))
    keep = df[~mask].copy()
    logger.info(f"[gold] filtro anexo: {len(df)} -> {len(keep)} "
                f"(removidas por anexo: {int(mask.sum())})")
    return keep


def inserir_gold(df_gold: pd.DataFrame) -> pd.DataFrame:
    """Insere no gold as linhas que sobreviveram (dedup por id_unificado)."""
    if df_gold is None or df_gold.empty:
        return pd.DataFrame(columns=GOLD_COLS)
    existentes = db.existing_ids(TB_GOLD)
    gold = df_gold[~df_gold["id_unificado"].isin(existentes)].copy()
    db.insert_df(gold, TB_GOLD, GOLD_COLS)
    return gold


def _recente(meta_row: dict, hoje: date, dias: int) -> bool:
    """Fallback de recência por publicação (reusa a lógica do bronze)."""
    from industry.licitacoes.bronze import _data_aparicao
    d = _data_aparicao(meta_row, hoje)
    return 0 <= (hoje - d).days <= (dias - 1)


# Orçamento de tempo do filtro "acionável". Ele consulta o PNCP item a item
# dentro do `montar_gold`, que é a montante do e-mail: um delta grande (o
# ComprasNet voltou e traz dezenas por dia) com a API em recusa atrasaria o envio.
# Medido em 17/09/2026: 49 itens levaram 2,5 min, com 6 recusas.
MAX_ACIONAVEL_SEGUNDOS = 420


def filtrar_acionaveis(df_gold: pd.DataFrame, dias_recencia: int = RECENCIA_DIAS) -> pd.DataFrame:
    """Filtra o que segue para score/email. Ver DECISOES.md §6 e §9.

    Regra (decidida com o usuário): descarta APENAS editais PNCP com janela de
    proposta JÁ FECHADA (dataEncerramentoProposta < hoje). Mantém todo o resto:
      - PNCP com janela aberta (>= hoje)  → oportunidade viva (Aba 1 no email);
      - PNCP sem janela (inexigibilidade/dispensa direta/contrato) → mantido (Aba 2);
      - sem número de controle do PNCP → mantido (assumido aberto — não temos o
        prazo exato, ver §9);
    "Ser PNCP" aqui é ter número de controle do PNCP, não a coluna `fonte` valer
    "PNCP": o ComprasNet entrega editais do próprio PNCP por outra porta.
      - falha da API → mantido (nunca descarta por erro).
    O corte de backlog muito antigo continua no bronze (JANELA_BRONZE_DIAS, §6).
    Persiste os prazos consultados em prazos_proposta. NÃO altera o gold (histórico).
    """
    if df_gold is None or df_gold.empty:
        return df_gold
    hoje = date.today()
    meta = db.meta_bronze(df_gold["id_unificado"].tolist())
    from industry.licitacoes import pncp as pncp_mod
    s = requests.Session()
    inicio = time.monotonic()
    keep_idx = []
    prazos = []   # persistidos em prazos_proposta (lido pela DAG de snapshot)
    st = {"aberto": 0, "fechado_excluido": 0, "sem_janela": 0,
          "sem_controle_pncp": 0, "api_falhou": 0, "sem_tempo": 0}
    for i, r in df_gold.iterrows():
        m = meta.get(r["id_unificado"], {})
        drop = False
        # O gate é ter número de controle do PNCP, não a fonte se chamar "PNCP".
        # O ComprasNet devolve editais do próprio PNCP (link pncp.gov.br/app/editais)
        # e antes caía no ramo "não-PNCP": nunca ganhava prazo e nunca era excluído
        # por janela fechada. Ver `pncp.identificadores_para_prazo`.
        ident = pncp_mod.identificadores_para_prazo(m)
        # A varredura de propostas abertas (pncp_abertas.py) já traz o
        # encerramento: usa direto, sem gastar a API de detalhe item a item.
        enc_listagem = db.to_date(m.get("data_encerramento_proposta"))
        if enc_listagem:
            ident = None
            prazos.append({"id_unificado": r["id_unificado"],
                           "data_abertura_proposta": m.get("data_abertura"),
                           "data_encerramento_proposta": enc_listagem,
                           "situacao_pncp": m.get("situacao")})
            if date.fromisoformat(enc_listagem) >= hoje:
                st["aberto"] += 1
            else:
                st["fechado_excluido"] += 1
                drop = True
        if ident and time.monotonic() - inicio > MAX_ACIONAVEL_SEGUNDOS:
            # Estourou o orçamento: para de consultar e MANTÉM o resto. Esta
            # função roda dentro do `montar_gold`, que é a montante do e-mail —
            # não pode virar refém da API de detalhe do PNCP, que custa 15s por
            # item quando recusa. Quem ficou sem prazo cai no `backfill_prazos`,
            # que existe exatamente para isso. Nunca descarta por não ter olhado.
            st["sem_tempo"] += 1
            ident = None
        if ident:
            cnpj, ano, seq = ident
            ok, info = pncp_mod.buscar_prazo_proposta(cnpj, ano, seq, s)
            time.sleep(0.3)   # pausa entre chamadas (API de detalhe é instável)
            if ok and info:
                prazos.append({"id_unificado": r["id_unificado"], **info})
                enc = info.get("data_encerramento_proposta")
                ed = db.to_date(enc) if enc else None
                if ed:
                    if date.fromisoformat(ed) >= hoje:
                        st["aberto"] += 1
                    else:
                        st["fechado_excluido"] += 1
                        drop = True          # edital PNCP com janela fechada → exclui
                else:
                    st["sem_janela"] += 1     # inexig/dispensa/contrato → mantém (Aba 2)
            elif not ok:
                st["api_falhou"] += 1         # mantém (não perde por erro)
        elif not enc_listagem:
            # sem controle PNCP (dispensa/contrato de fonte própria): assumido
            # aberto → mantém (Aba 1). Ver DECISOES.md §9.
            st["sem_controle_pncp"] += 1
        if not drop:
            keep_idx.append(i)
    if prazos:
        try:
            db.upsert_prazos(prazos)
        except Exception as e:
            logger.warning(f"[acionavel] falha ao gravar prazos: {str(e)[:80]}")
    out = df_gold.loc[keep_idx].copy()
    logger.info(f"[acionavel] {len(df_gold)} -> {len(out)} | prazos gravados: {len(prazos)} | {st}")
    return out


# ── backfill de prazos ────────────────────────────────────────────────────────
# Teto de itens por run: a API de detalhe do PNCP é instável, e o que falhar
# continua na lista dos "sem prazo" e é re-tentado na run seguinte.
MAX_BACKFILL_PRAZOS = 300

# Orçamento de tempo. O teto de itens não limita a DURAÇÃO: a API de detalhe do
# PNCP responde em ~0,3s quando está boa e custa 15s por item quando recusa (4
# tentativas com espera crescente). Medido em 17/09/2026, rodando a DAG inteira:
# a task passou de 17 minutos e gravou 1 prazo, com a API em recusa; no pior caso
# os 300 itens dariam mais de uma hora ocupando um worker. O que sobrar continua
# na lista dos "sem prazo" e é re-tentado na run seguinte — que é o desenho.
MAX_BACKFILL_SEGUNDOS = 600

# Grava de tantos em tantos itens. Sem isto, a task acumulava tudo para gravar no
# fim: cortar por tempo (ou por `execution_timeout`) jogava fora todo o trabalho
# de API já feito.
LOTE_GRAVACAO_PRAZOS = 25


def backfill_prazos(limite: int = MAX_BACKFILL_PRAZOS) -> dict:
    """Busca o prazo dos itens com controle PNCP que ainda não têm prazo.

    O `filtrar_acionaveis` só consulta o prazo dos itens que passam pelo delta do
    dia. Item cuja consulta falhou (o `api_falhou` do log) ficava
    sem prazo PARA SEMPRE: nada voltava nele. Resultado medido em 16/09/2026:
    58 ids em `prazos_proposta` para 2.412 linhas no gold_filtros, e 153 das 164
    com score > 0,35 exibidas como "prazo não consultado".

    Esta função existia, com este mesmo corpo, dentro da DAG
    INTERNO_licitacoes_Snapshot — que está pausada e nunca rodou (0 runs). Mudou
    de casa para cá (onde `prazos_proposta` é escrita) para poder ser chamada
    pela DAG diária sem depender daquela; o snapshot passa a importá-la.

    Bounded e paced. Idempotente: só olha quem não tem prazo.
    """
    from sqlalchemy import text
    from industry.licitacoes import pncp as pncp_mod
    from industry.licitacoes.config import SCHEMA, TB_GOLD_FILTROS, TB_PRAZOS, TB_BRONZE

    with db.get_engine().connect() as conn:
        ids = [r[0] for r in conn.execute(text(
            f"SELECT gf.id_unificado FROM {SCHEMA}.{TB_GOLD_FILTROS} gf "
            f"JOIN {SCHEMA}.{TB_BRONZE} b ON b.id_unificado = gf.id_unificado "
            f"LEFT JOIN {SCHEMA}.{TB_PRAZOS} p ON p.id_unificado = gf.id_unificado "
            # O critério é ter número de controle do PNCP, não a fonte se chamar
            # "PNCP": o ComprasNet traz editais do PNCP e ficava de fora daqui.
            # O número mora no bronze (o silver/gold descartam a coluna), por isso
            # o JOIN. Quem não tem controle não tem o que consultar.
            f"WHERE p.id_unificado IS NULL "
            f"AND coalesce(b.numero_controle_pncp, '') <> '' "
            # Mais relevante primeiro: se o teto cortar, corta o que menos importa.
            f"ORDER BY gf.score_combinado DESC NULLS LAST LIMIT {int(limite)}"))]
    if not ids:
        logger.info("[prazos] nenhum item PNCP sem prazo")
        return {"sem_prazo": 0, "gravados": 0, "api_falhou": 0}

    meta = db.meta_bronze(ids)
    s = requests.Session()
    prazos, falhas = [], 0
    sem_ident = 0
    gravados = 0
    inicio = time.monotonic()
    esgotou_tempo = False

    def _gravar(lote):
        # SEM try/except, de propósito — ver o comentário no fim da função.
        if lote:
            db.upsert_prazos(lote)
        return len(lote)

    for i, idu in enumerate(ids):
        if time.monotonic() - inicio > MAX_BACKFILL_SEGUNDOS:
            esgotou_tempo = True
            logger.info(f"[prazos] orçamento de {MAX_BACKFILL_SEGUNDOS}s esgotado em "
                        f"{i}/{len(ids)} itens; o resto vai na próxima run")
            break
        ident = pncp_mod.identificadores_para_prazo(meta.get(idu, {}))
        if not ident:
            # A consulta exige controle não-vazio, então isto só acontece com
            # número fora do formato. Contado à parte para não virar "falha de API".
            sem_ident += 1
            continue
        cnpj, ano, seq = ident
        ok, info = pncp_mod.buscar_prazo_proposta(cnpj, ano, seq, s)
        time.sleep(0.3)
        if ok and info:
            prazos.append({"id_unificado": idu, **info})
        elif not ok:
            falhas += 1
        if len(prazos) >= LOTE_GRAVACAO_PRAZOS:
            gravados += _gravar(prazos)
            prazos = []
    gravados += _gravar(prazos)
    # A gravação NÃO tem try/except, de propósito. Esta task existe só para
    # gravar: se a escrita falha, ela não fez nada e tem que aparecer como falha.
    # Engolir a exceção devolvia `gravados: 0` com a task em `success` —
    # indistinguível de "não havia nada a gravar", que é a falha silenciosa que
    # esta leva de correções foi caçar. Achado por teste: sem a PK em
    # prazos_proposta, as 4 consultas ao PNCP funcionavam e os prazos eram
    # perdidos em silêncio. A task é `all_done` e não tem nada a jusante, então
    # falhar aqui não segura o e-mail do dia.
    logger.info(f"[prazos] backfill: {len(ids)} sem prazo -> {gravados} gravados, "
                f"{falhas} falha(s) de API (re-tentadas na próxima run), "
                f"{sem_ident} sem identificador utilizável"
                + (f" | PAROU POR TEMPO ({MAX_BACKFILL_SEGUNDOS}s)" if esgotou_tempo else ""))
    return {"sem_prazo": len(ids), "gravados": gravados, "api_falhou": falhas,
            "sem_identificador": sem_ident, "parou_por_tempo": esgotou_tempo}
