"""Camada GOLD_FILTROS — score dos 3 filtros sobre as entradas do gold.

Para cada licitação (usa o `objeto` do gold como texto) calcula:
  1) KEYWORD  : tag_kw_<tema> (0/1) + termos casados        -> s_kw (0/1)
  2) ZERO-SHOT: tag_zs_<tema> (0..1) via mDeBERTa (CPU)      -> s_zs (maior nota)
  3) LLM      : llm_relevante / areas A-E via Groq           -> s_llm (0..1)
E o SCORE COMBINADO = média ponderada (pesos iguais) dos sinais disponíveis;
quando o LLM não avalia (texto curto), a média usa só kw+zs (renormalizada).

Saída: todas as entradas do gold + colunas de score -> licitacoes.gold_filtros.
Na origem o zero-shot rodava num worker com GPU; aqui roda em CPU (volume diário
pequeno) e pode ser desligado com LICITACOES_ZEROSHOT=0 — o score renormaliza.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata

import pandas as pd

from industry.licitacoes.config import (
    TEMAS, ZS_MODEL, ZS_THRESHOLD, ZS_HYPOTHESIS, ZS_BATCH, ZS_LABELS_EXTRA, ZS_ATIVO,
    ZS_MAX_CHARS,
    LLM_MODEL, LLM_BATCH, LLM_MAX_DESC, LLM_SYS, AREA_COL,
    W_KW, W_ZS, W_LLM, SCORE_THRESHOLD, GOLD_FILTROS_COLS, TB_GOLD_FILTROS,
    W_VIAB_LLM, W_VIAB_VALOR, VIAB_FAIXAS_VALOR,
    W_KW_COM_LLM, W_ZS_COM_LLM, W_LLM_COM_LLM, VIAB_TETO_NAO_TECNICO,
)
from industry.licitacoes import db

logger = logging.getLogger(__name__)


# ── 1) keyword ────────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


def _keyword_tags(texto_norm: str) -> dict:
    return {tema: [kw for kw in cfg["kw"] if kw in texto_norm]
            for tema, cfg in TEMAS.items()}


# ── 2) zero-shot (CPU; GPU se houver CUDA) ────────────────────────────────────────────────────────
def _zero_shot(textos: list[str]) -> tuple[dict[str, list[float]], list[float]]:
    """Roda o zero-shot contra os labels dos TEMAS + os ZS_LABELS_EXTRA.

    Retorna (scores_por_tema, extra_max): o dict por tema alimenta as colunas
    tag_zs_<tema>; extra_max[i] é o maior score entre os labels EXTRA (sub-temas
    dos serviços-alvo) — entra no s_zs sem virar coluna. Ver DECISOES.md §8.2.

    Com ZS_ATIVO=False devolve tudo None: o sinal fica ausente e o score combinado
    renormaliza sobre keyword + LLM.
    """
    if not ZS_ATIVO:
        logger.info("[zs] zero-shot desligado (LICITACOES_ZEROSHOT=0)")
        return {t: [None] * len(textos) for t in TEMAS}, [None] * len(textos)
    try:
        from transformers import pipeline
        import torch

        dev = 0 if torch.cuda.is_available() else -1
        logger.info(f"[zs] carregando {ZS_MODEL} (device={'GPU' if dev == 0 else 'CPU'})")
        zs = pipeline("zero-shot-classification", model=ZS_MODEL, device=dev)
    except Exception as e:  # noqa: BLE001 — ambiente quebrado não pode parar o score
        # Ex.: 20/10/2026 um pip install de OCR subiu o numpy para 2.x e o torch
        # 2.2 deixou de importar. O sinal fica ausente e o score renormaliza
        # sobre keyword + LLM, como com ZS_ATIVO=False.
        logger.warning(f"[zs] zero-shot indisponível ({type(e).__name__}: {str(e)[:120]}); "
                       f"seguindo sem o sinal")
        return {t: [None] * len(textos) for t in TEMAS}, [None] * len(textos)
    labels = [TEMAS[t]["label"] for t in TEMAS] + list(ZS_LABELS_EXTRA)
    scores = {t: [0.0] * len(textos) for t in TEMAS}
    extra_max = [0.0] * len(textos)
    for i in range(0, len(textos), ZS_BATCH):
        lote = [t[:ZS_MAX_CHARS] for t in textos[i:i + ZS_BATCH]]
        res = zs(lote, candidate_labels=labels, multi_label=True,
                 hypothesis_template=ZS_HYPOTHESIS)
        if isinstance(res, dict):
            res = [res]
        for j, r in enumerate(res):
            m = dict(zip(r["labels"], r["scores"]))
            for t in TEMAS:
                scores[t][i + j] = round(float(m.get(TEMAS[t]["label"], 0.0)), 4)
            if ZS_LABELS_EXTRA:
                extra_max[i + j] = round(max(float(m.get(lab, 0.0)) for lab in ZS_LABELS_EXTRA), 4)
    return scores, extra_max


# ── 3) LLM (gpt-oss-120b) pelo roteador de chaves ─────────────────────────────
def _parse_score(v) -> int | None:
    """Coage a nota do LLM para inteiro 0..100 (ou None se inválida)."""
    try:
        return max(0, min(100, int(round(float(v)))))
    except (TypeError, ValueError):
        return None


# Tentativas por lote quando a SAÍDA vem malformada (JSON quebrado ou ids
# faltando). Falhas de chave/rede/rate-limit são resolvidas dentro do roteador.
TENTATIVAS_LOTE = 3


def _classificar_llm(textos: dict[str, str]) -> dict[str, dict]:
    """textos: {id_unificado: texto}. Retorna {id: {score, areas, motivo}}.

    Na origem havia um laço próprio com requests: um 5xx ou um 401 no meio da
    rodada caía no ``except`` de parse e o lote inteiro saía SEM nota, sem nova
    tentativa. Agora a troca de chave/provedor é do roteador
    (``industry.shared.llm``); aqui só se repete o lote quando a saída do modelo
    vem malformada, e a rodada para quando a cota acaba ou não há chave viva —
    os itens restantes ficam sem nota (o score renormaliza sobre kw+zs).
    """
    from industry.shared import llm

    resultado: dict[str, dict] = {}
    # O texto já chega com o objeto cortado em LLM_MAX_DESC e o valor/modalidade
    # anexados (ver _texto_llm); aqui só colapsa espaços.
    pend = [(idu, re.sub(r"\s+", " ", t))
            for idu, t in textos.items() if not _texto_insuficiente(t)]
    pend_ids = {idu for idu, _ in pend}
    for idu in textos:
        if idu not in pend_ids:
            resultado[idu] = {"score": None, "areas": [], "motivo": "sem texto",
                              "viabilidade": None, "motivo_viabilidade": ""}
    if not pend:
        return resultado
    try:
        cli = llm.cliente(LLM_MODEL, timeout=90, temperature=0,
                          response_format={"type": "json_object"})
    except llm.SemChavesValidas as e:
        logger.warning(f"[llm] {e} — pulando etapa LLM")
        return resultado

    def pontuar(lote: list[tuple[str, str]]) -> dict[str, dict]:
        """{id_unificado: resposta} do lote. Junta as respostas parciais de até
        TENTATIVAS_LOTE tentativas (o modelo às vezes devolve 14 de 15 ids)."""
        lic = [{"id": j, "texto": t} for j, (_, t) in enumerate(lote)]
        res: dict[int, dict] = {}
        for tent in range(1, TENTATIVAS_LOTE + 1):
            try:
                r = cli.create(messages=[
                    {"role": "system", "content": LLM_SYS},
                    {"role": "user", "content": json.dumps(lic, ensure_ascii=False)}])
                data = json.loads(r.choices[0].message.content)
                for x in data.get("resultados", []):
                    if isinstance(x, dict) and x.get("id") in range(len(lote))                             and _parse_score(x.get("score")) is not None:
                        res.setdefault(x["id"], x)
            except (llm.CotaEsgotada, llm.SemChavesValidas):
                raise
            except Exception as e:  # noqa: BLE001 — saída malformada / pedido recusado
                logger.warning(f"[llm] lote de {len(lote)} tentativa {tent}: {str(e)[:100]}")
                continue
            if len(res) >= len(lote):
                break
        return {lote[j][0]: x for j, x in res.items()}

    respostas: dict[str, dict] = {}
    try:
        for i in range(0, len(pend), LLM_BATCH):
            respostas.update(pontuar(pend[i:i + LLM_BATCH]))
        # O que faltou volta em lotes menores, e por fim um a um.
        for tam in (5, 1):
            faltam = [p for p in pend if p[0] not in respostas]
            if not faltam:
                break
            logger.info(f"[llm] {len(faltam)} sem resposta — repergunta em lotes de {tam}")
            for i in range(0, len(faltam), tam):
                respostas.update(pontuar(faltam[i:i + tam]))
    except (llm.CotaEsgotada, llm.SemChavesValidas) as e:
        logger.warning(f"[llm] parando a etapa LLM: {e}")

    for idu, _ in pend:
        x = respostas.get(idu, {})
        resultado[idu] = {
            "score": _parse_score(x.get("score")) if x else None,
            "areas": [a for a in x.get("areas", []) if a in AREA_COL],
            "motivo": str(x.get("motivo", ""))[:120],
            "viabilidade": _parse_score(x.get("viabilidade")) if x else None,
            "motivo_viabilidade": str(x.get("motivo_viabilidade", ""))[:120],
        }
    return resultado

# ── orquestração do score ─────────────────────────────────────────────────────
# Abaixo disto o objeto é uma palavra solta ("BENS", "ALIMENTOS", "FECHADURA") e
# nenhum sinal semântico — nem LLM nem zero-shot — consegue julgar. Era um literal
# solto dentro de `_classificar_llm`; virou constante porque agora os dois usam.
TEXTO_MIN_CHARS = 12


def _texto_insuficiente(t: str) -> bool:
    return len(re.sub(r"\s+", " ", str(t or "")).strip()) < TEXTO_MIN_CHARS


def _ausente(v) -> bool:
    """Sinal indisponível. Tem que cobrir NaN, não só None.

    `df["s_llm"]` é montada a partir de uma lista com `None` (LLM pulado por
    "sem texto", ou chamada sem nota). O pandas tipa a coluna como float64 e
    converte esse `None` em `np.nan` — então o teste `v is None` NUNCA casava e
    o `continue` de `_combina` nunca rodava. Resultado: `num` virava NaN, o
    score saía NaN e ia pro Postgres como NULL, matando a linha em todo filtro
    `score_combinado > X` (NULL > X é NULL, não True). Eram 119 linhas, várias
    com `s_zs` alto (0,62-0,99). A renormalização que o comentário de `_combina`
    promete só passou a existir de fato com este guard.
    """
    return v is None or pd.isna(v)


def combinar_sinais(s_kw, s_zs, s_llm) -> tuple[float | None, int]:
    """Média ponderada dos 3 sinais, renormalizada sobre os que existem.

    Devolve (score, n_sinais_usados). Score é None só quando NENHUM sinal está
    disponível — nunca NaN, que o Postgres grava como NULL e some de qualquer
    filtro `> X`. Ver `_ausente`.
    """
    num = den = 0.0
    n = 0
    pesos = ((W_KW_COM_LLM, W_ZS_COM_LLM, W_LLM_COM_LLM) if not _ausente(s_llm)
             else (W_KW, W_ZS, W_LLM))
    for w, v in zip(pesos, (s_kw, s_zs, s_llm)):
        if _ausente(v):
            continue
        num += w * float(v)
        den += w
        n += 1
    return (round(num / den, 4) if den else None), n


def _valor_num(v) -> float | None:
    return db.to_numeric(v)


def _texto_llm(objeto: str, valor, tipo) -> str:
    """Objeto (cortado) + valor estimado + modalidade — o valor e a modalidade
    alimentam a nota de VIABILIDADE (time pequeno)."""
    partes = [re.sub(r"\s+", " ", str(objeto or ""))[:LLM_MAX_DESC]]
    v = _valor_num(valor)
    partes.append(f"valor estimado: R$ {v:,.0f}".replace(",", ".") if v else
                  "valor estimado: não informado")
    if str(tipo or "").strip():
        partes.append(f"modalidade: {str(tipo).strip()[:60]}")
    return " | ".join(partes)


def viabilidade_por_valor(valor) -> float | None:
    """Sinal de viabilidade pela faixa do valor estimado (None sem valor)."""
    v = _valor_num(valor)
    if not v:
        return None
    for teto, nota in VIAB_FAIXAS_VALOR:
        if v <= teto:
            return nota
    return 0.05


def combinar_viabilidade(s_llm, s_valor) -> float | None:
    """Média ponderada dos sinais de viabilidade disponíveis (None se nenhum).

    Se o LLM disse que nem é serviço técnico (nota <= VIAB_TETO_NAO_TECNICO), vale
    a nota dele: valor baixo não torna viável o que não é o tipo de trabalho.
    """
    # Sem nota do LLM não há como julgar se é o tipo de trabalho: fica vazio, em
    # vez de vir só do valor (item barato de qualquer coisa saía com 100).
    if _ausente(s_llm):
        return None
    if float(s_llm) <= VIAB_TETO_NAO_TECNICO:
        return round(float(s_llm), 4)
    num = den = 0.0
    for w, v in ((W_VIAB_LLM, s_llm), (W_VIAB_VALOR, s_valor)):
        if _ausente(v):
            continue
        num += w * float(v)
        den += w
    if not den:
        return None
    mistura = num / den
    # O valor só derruba: nunca passa da nota do LLM.
    return round(min(mistura, float(s_llm)), 4)


def calcular_gold_filtros(df_gold: pd.DataFrame, usar_llm: bool = True) -> pd.DataFrame:
    """Recebe as entradas do gold e devolve o df pronto p/ gold_filtros."""
    if df_gold is None or df_gold.empty:
        return pd.DataFrame(columns=GOLD_FILTROS_COLS)
    df = df_gold.fillna("").reset_index(drop=True)
    textos = [str(o) for o in df["objeto"]]
    norms = [_norm(t) for t in textos]

    # 1) keyword
    kw_res = [_keyword_tags(n) for n in norms]
    for tema in TEMAS:
        df[f"tag_kw_{tema}"] = [1 if kw_res[i][tema] else 0 for i in range(len(df))]
        df[f"tag_kw_{tema}_termos"] = ["; ".join(kw_res[i][tema]) for i in range(len(df))]

    # 2) zero-shot (temas + labels extras dos sub-temas dos serviços-alvo)
    zs_scores, zs_extra = _zero_shot(textos)
    for tema in TEMAS:
        df[f"tag_zs_{tema}"] = zs_scores[tema]

    # combinação kw+zs
    def _tags(i):
        return [t for t in TEMAS
                if df[f"tag_kw_{t}"].iloc[i] == 1
                or float(df[f"tag_zs_{t}"].iloc[i] or 0) >= ZS_THRESHOLD]
    todas = [_tags(i) for i in range(len(df))]
    df["tags"] = ["; ".join(t) for t in todas]
    df["tag_relevante"] = [1 if t else 0 for t in todas]
    df["tag_metodo"] = f"keywords + zero-shot({ZS_MODEL})"

    # 3) LLM
    if usar_llm:
        llm = _classificar_llm({
            df["id_unificado"].iloc[i]: (
                _texto_llm(textos[i], df["valor"].iloc[i] if "valor" in df else None,
                           df["tipo"].iloc[i] if "tipo" in df else None)
                if not _texto_insuficiente(textos[i]) else textos[i])
            for i in range(len(df))})
    else:
        llm = {}
    def g(i, campo, default=None):
        return llm.get(df["id_unificado"].iloc[i], {}).get(campo, default)
    # llm_relevante agora é DERIVADO da nota 0-100 (>=50 = sim); a nota crua vira s_llm.
    df["llm_relevante"] = [
        "" if (df["id_unificado"].iloc[i] not in llm or g(i, "score") is None)
        else ("sim" if g(i, "score") >= 50 else "nao")
        for i in range(len(df))]
    df["llm_areas"] = [";".join(g(i, "areas", []) or []) for i in range(len(df))]
    df["llm_motivo"] = [g(i, "motivo", "") for i in range(len(df))]
    df["llm_modelo"] = [LLM_MODEL if df["id_unificado"].iloc[i] in llm else "" for i in range(len(df))]
    for letra, col in AREA_COL.items():
        df[col] = [(1 if letra in (g(i, "areas", []) or [])
                    else (0 if (df["id_unificado"].iloc[i] in llm and g(i, "score") is not None) else ""))
                   for i in range(len(df))]

    # sub-scores + score combinado (renormaliza sobre os sinais disponíveis)
    df["s_kw"] = [1.0 if any(df[f"tag_kw_{t}"].iloc[i] == 1 for t in TEMAS) else 0.0
                  for i in range(len(df))]
    # s_zs = maior score zero-shot entre os TEMAS e os labels EXTRA (sub-temas).
    #
    # Objeto curto demais NÃO recebe sinal semântico. O LLM já era pulado abaixo de
    # TEXTO_MIN_CHARS (vira motivo "sem texto"), mas o zero-shot continuava opinando
    # sobre uma palavra solta — e opinando alto: "ALIMENTOS", "BENS", "TOKEN",
    # "FECHADURA" tiravam s_zs de 0,62 a 0,99. Isso é ruído do classificador, não
    # sinal: sem contexto ele não tem como julgar. Se o texto não dá para o LLM,
    # também não dá para o zero-shot — sobra a keyword, que é casamento exato de
    # termo e continua válida em texto curto.
    def _zs(i):
        if not ZS_ATIVO or zs_extra[i] is None or _texto_insuficiente(textos[i]):
            return None
        return max(max(float(df[f"tag_zs_{t}"].iloc[i] or 0) for t in TEMAS),
                   float(zs_extra[i]))
    df["s_zs"] = [_zs(i) for i in range(len(df))]
    # s_llm agora é CONTÍNUO: a nota 0-100 do LLM normalizada para 0-1 (era 1.0/0.0).
    def _sllm(i):
        sc = g(i, "score", None)
        return (sc / 100.0) if sc is not None else None
    df["s_llm"] = [_sllm(i) for i in range(len(df))]

    combinados = [combinar_sinais(df["s_kw"].iloc[i], df["s_zs"].iloc[i], df["s_llm"].iloc[i])
                  for i in range(len(df))]
    df["score_combinado"] = [c[0] for c in combinados]
    df["n_sinais_usados"] = [c[1] for c in combinados]
    df["usou_llm"] = [0 if _ausente(df["s_llm"].iloc[i]) else 1 for i in range(len(df))]
    df["relevante_combinado"] = [1 if (not _ausente(df["score_combinado"].iloc[i])
                                       and df["score_combinado"].iloc[i] >= SCORE_THRESHOLD)
                                 else 0 for i in range(len(df))]

    # SCORE DE VIABILIDADE (time pequeno) — mesmo desenho do combinado acima.
    df["llm_viabilidade"] = [g(i, "viabilidade", None) for i in range(len(df))]
    df["llm_viabilidade_motivo"] = [g(i, "motivo_viabilidade", "") for i in range(len(df))]
    df["s_viab_llm"] = [(v / 100.0) if v is not None else None for v in df["llm_viabilidade"]]
    df["s_viab_valor"] = [viabilidade_por_valor(df["valor"].iloc[i]) if "valor" in df else None
                          for i in range(len(df))]
    df["score_viabilidade"] = [combinar_viabilidade(df["s_viab_llm"].iloc[i],
                                                    df["s_viab_valor"].iloc[i])
                               for i in range(len(df))]

    for c in GOLD_FILTROS_COLS:
        if c not in df.columns:
            df[c] = None
    return df[GOLD_FILTROS_COLS]


def inserir_gold_filtros(df_gold: pd.DataFrame, run_date=None, usar_llm: bool = True) -> pd.DataFrame:
    db.ensure_gold_filtros()
    scored = calcular_gold_filtros(df_gold, usar_llm=usar_llm)
    if scored.empty:
        logger.info("[gold_filtros] nada a inserir")
        return scored
    scored["run_date"] = run_date
    existentes = db.existing_ids(TB_GOLD_FILTROS)
    scored = scored[~scored["id_unificado"].isin(existentes)].copy()
    # ordena pelo score da última etapa (desc; sem score no fim)
    scored = scored.sort_values("score_combinado", ascending=False, na_position="last")
    db.insert_df(scored, TB_GOLD_FILTROS, GOLD_FILTROS_COLS)
    return scored
