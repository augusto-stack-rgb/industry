"""Rodada de classificação de verticais (corpo da antiga task ``classificar_verticais``).

Lê do banco as licitações do gold sem vertical (mais relevantes primeiro), chama
o LLM em lotes pelo roteador de chaves (``industry.shared.llm``) e grava cada resposta em
``licitacoes.verticais`` (commit por resposta — a rodada é resumível).

Nunca derruba a pipeline: cota diária esgotada (``LimiteDiarioTokens``) encerra a
rodada e deixa o resto para a próxima execução.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


def classificar_pendentes(log) -> dict:
    from industry.licitacoes import db
    from industry.licitacoes.config import (
        LLM_MODEL, VERTICAIS_LIMITE, VERTICAIS_LOTE, VERTICAIS_PARALELISMO, vertical_nome,
    )
    from industry.licitacoes.verticais import (
        LimiteDiarioTokens, classificar_lote, classificar_objeto,
    )
    from industry.shared import llm

    pend = db.objetos_sem_vertical(VERTICAIS_LIMITE)
    if pend.empty:
        log.info("[vert] nada pendente — todo o gold já tem vertical.")
        return {"status": "nada_pendente", "classificadas": 0, "falhas": 0,
                "pendentes_restantes": 0}

    try:
        cliente = llm.cliente(LLM_MODEL, timeout=80)
    except llm.SemChavesValidas as e:
        log.warning(f"[vert] {e} — etapa pulada.")
        return {"status": "sem_chaves", "classificadas": 0, "falhas": 0,
                "pendentes_restantes": int(len(pend))}

    n_chaves = len(cliente.roteador.status())
    workers = min(n_chaves * VERTICAIS_PARALELISMO, 64)
    linhas = list(pend.itertuples())
    grupos = [linhas[i:i + VERTICAIS_LOTE] for i in range(0, len(linhas), VERTICAIS_LOTE)]

    def trabalhar(grupo):
        """[(linha, vertical, confianca)] — falha sai com vertical ''. Quem o
        modelo deixa de fora do lote é reperguntado individualmente."""
        if len(grupo) == 1 and VERTICAIS_LOTE == 1:
            vid, conf = classificar_objeto(grupo[0].objeto, cliente)
            return [(grupo[0], vid, conf)]
        res = classificar_lote([(l.id_unificado, l.objeto) for l in grupo], cliente)
        for l in grupo:
            if l.id_unificado not in res:
                res[l.id_unificado] = classificar_objeto(l.objeto, cliente)
        return [(l, *res.get(l.id_unificado, ("", 0.0))) for l in grupo]

    engine = db.get_engine()
    lock = threading.Lock()
    contagem: dict[str, int] = {}
    n_ok = n_falha = 0
    tpd = None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futuros = [ex.submit(trabalhar, g) for g in grupos]
        for fut in as_completed(futuros):
            try:
                resultados = fut.result()
            except LimiteDiarioTokens as e:
                if tpd is None:
                    tpd = str(e)
                    for f in futuros:
                        f.cancel()
                continue
            for linha, vid, conf in resultados:
                if not vid:
                    n_falha += 1
                    continue          # não persiste: a próxima execução retenta
                n_ok += 1
                with lock:
                    contagem[vid] = contagem.get(vid, 0) + 1
                    with engine.begin() as conn:
                        db.upsert_vertical(conn, linha.id_unificado, vid, conf, LLM_MODEL)

    if tpd:
        log.warning(f"[vert] parado: cota das chaves esgotada ({tpd})")
    for vid, n in sorted(contagem.items(), key=lambda x: -x[1]):
        log.info(f"[vert]   {vertical_nome(vid):<45} {n:5d}")
    n_gold, n_com_vert = db.cobertura_vertical()
    return {"status": "partial_tpd" if tpd else "success", "classificadas": n_ok,
            "falhas": n_falha, "pendentes_restantes": int(n_gold - n_com_vert)}
