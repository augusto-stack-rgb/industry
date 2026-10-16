#!/usr/bin/env python3
"""Limpeza retroativa da base de operações de M&A já extraídas.

A validação e o dedup semântico da DAG só valem para operações NOVAS: o que já
estava em ``noticias.mna_operations`` antes deles nunca passou por revisão. Este
script aplica as duas revisões sobre a base existente.

Duas etapas independentes, escolhidas por ``--etapa``:

  dedup      Agrupa as operações que envolvem o MESMO par de empresas (blocking
             por nome, direção-insensitivo — ``pipeline.mesmo_par``) e pergunta ao
             LLM quais são a mesma transação. As duplicatas são MESCLADAS na linha
             que fica (``pipeline.mesclar``: status mais avançado, valor ausente),
             registradas em ``mna_operations_eventos`` e movidas para
             ``mna_operations_rejeitadas``.
             Caso real que motivou o script: a fusão Paramount/Warner tinha 8
             linhas, de abril a julho, que a janela de 5 dias do dedup em runtime
             nunca alcançaria.

  validacao  Reenvia cada operação ao LLM com o artigo de origem e remove as que
             não entram no relatório (compra de ativo físico, deal antigo citado
             como contexto, IPO/reorganização, extração desalinhada). Mais caro:
             1 chamada por operação, com o texto do artigo — use ``--dias`` para
             restringir ao recorte do PDF (ex.: ``--dias 90``).

  mesclar_rejeitadas
             Sem LLM. Até 02/10/2026 o dedup da DAG DESCARTAVA a duplicata em vez de
             atualizar a operação cadastrada — o deal ficava parado no primeiro
             status noticiado (Amil seguia "rumor" depois do cancelamento). Esta
             etapa relê as rejeições de dedup ainda não mescladas e aplica a
             mescla na operação do mesmo par. Rode DEPOIS de ``dedup``: com os
             grupos já fundidos, cada rejeição casa com uma linha só. Idempotente
             (pula ``rej_id`` que já tem evento).

As etapas com LLM são resumíveis e param sozinhas ao esgotar as chaves Groq (TPD) — re-execute
no dia seguinte. Por padrão rodam em SIMULAÇÃO (não alteram a base): use
``--aplicar`` para efetivar.

Ambiente: igual ao ``carga_historica.py``. Ex.:

    docker compose run --rm dagster-daemon python -m \\
        industry.noticias.mna.limpeza_base --etapa dedup --aplicar
"""

from __future__ import annotations

import argparse
import logging

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("limpeza_base_mna")


def _grupos_por_par(ops: list[dict]) -> list[list[dict]]:
    """Agrupa operações do mesmo par de empresas, SEM transitividade.

    Transitividade juntaria deals distintos numa corrente ("A compra B" +
    "C compra B" -> A, B e C no mesmo grupo). Aqui cada operação é comparada
    contra as que já sobreviveram, e o grupo cresce só com quem casa direto.
    """
    from industry.noticias.mna.pipeline import mesmo_par

    grupos: list[list[dict]] = []
    for op in ops:
        for g in grupos:
            if mesmo_par(op, g[0]):
                g.append(op)
                break
        else:
            grupos.append([op])
    return [g for g in grupos if len(g) > 1]


def _linha(op: dict) -> str:
    return (f"{op.get('op_id')}: {op.get('empresa_adquirente')} / "
            f"{op.get('empresa_alvo')} | {op.get('valor')} {op.get('moeda') or ''} | "
            f"{op.get('data_artigo')}")


def etapa_dedup(engine, extractor, *, aplicar: bool) -> dict:
    """Funde as duplicatas da base existente. Retorna estatísticas."""
    from industry.noticias.mna import config as C
    from industry.noticias.mna import db
    from industry.noticias.mna.pipeline import (
        LimiteDiario,
        escolher_sobrevivente,
        mesclar,
    )

    ops = db.carregar_operacoes_completas(engine)
    grupos = _grupos_por_par(ops)
    log.info("%d operações na base; %d grupos com o mesmo par de empresas (%d linhas).",
             len(ops), len(grupos), sum(len(g) for g in grupos))

    stats = {"grupos": len(grupos), "removidas": 0, "mantidas": 0, "atualizadas": 0,
             "tpd": False}
    fusoes: list[tuple[dict, list[dict]]] = []
    try:
        for n, grupo in enumerate(grupos, start=1):
            manter = escolher_sobrevivente(grupo)
            log.info("[grupo %d/%d] mantendo %s", n, len(grupos), _linha(manter))
            absorvidas: list[dict] = []
            for op in grupo:
                if op["op_id"] == manter["op_id"]:
                    continue
                # Pergunta explícita: esta linha é a MESMA transação da mantida?
                base = [{
                    "ref": manter["op_id"],
                    "empresa_adquirente": manter.get("empresa_adquirente"),
                    "empresa_alvo": manter.get("empresa_alvo"),
                    "valor": manter.get("valor"),
                    "moeda": manter.get("moeda"),
                    "data_artigo": str(manter.get("data_artigo") or ""),
                }]
                candidata = {**op, "data_artigo": str(op.get("data_artigo") or "")}
                duplicata, motivo, falha, _ = extractor.checar_duplicata(candidata, base)
                if falha:
                    log.warning("   ? %s — revisão indisponível (%s), MANTIDA",
                                _linha(op), falha)
                    stats["mantidas"] += 1
                    continue
                if duplicata:
                    log.info("   - duplicata: %s — %s", _linha(op), motivo)
                    absorvidas.append({**op, "motivo_rejeicao": motivo,
                                       "etapa": "dedup_retroativo"})
                    stats["removidas"] += 1
                else:
                    log.info("   = distinta: %s — %s", _linha(op), motivo or "não é o mesmo deal")
                    stats["mantidas"] += 1
            if absorvidas:
                # Prévia do que a fusão muda na linha mantida (mesma ordem do db).
                atual = dict(manter)
                for op in sorted(absorvidas, key=lambda o: str(o.get("data_artigo") or "")):
                    alt, div = mesclar(atual, op)
                    atual.update(alt)
                    for d in div:
                        log.info("     ! divergência não aplicada: %s", d)
                mudou = {c: (manter.get(c), atual.get(c)) for c in atual
                         if atual.get(c) != manter.get(c)}
                if mudou:
                    stats["atualizadas"] += 1
                    log.info("     > linha mantida passa a ter: %s", {
                        c: v[1] for c, v in mudou.items()
                        if c in ("status", "valor", "moeda", "data_artigo")})
                fusoes.append((manter, absorvidas))
    except LimiteDiario as exc:
        stats["tpd"] = True
        log.warning("Chaves Groq esgotadas — aplicando o parcial e parando. %s", exc)

    if not aplicar:
        log.warning("SIMULAÇÃO: %d duplicatas seriam fundidas em %d operações, %d delas "
                    "com status/valor atualizado (use --aplicar).",
                    stats["removidas"], len(fusoes), stats["atualizadas"])
        return stats

    for manter, absorvidas in fusoes:
        rej_ids = db.append_rejeitadas(engine, absorvidas)
        with engine.begin() as conn:
            db.fundir_operacoes(conn, manter,
                                [{**o, "rej_id": r} for o, r in zip(absorvidas, rej_ids)],
                                origem="dedup_retroativo")
    log.info("%d duplicatas fundidas em %d operações (arquivadas em %s).",
             stats["removidas"], len(fusoes), C.TABELA_REJEITADAS)
    return stats


def etapa_mesclar_rejeitadas(engine, *, aplicar: bool) -> dict:
    """Aplica a mescla às rejeições de dedup que só foram descartadas. Sem LLM."""
    from industry.noticias.mna import config as C
    from industry.noticias.mna import db
    from industry.noticias.mna.pipeline import (
        _norm_company,
        escolher_sobrevivente,
        mesclar,
        mesmo_par,
    )
    from sqlalchemy import text

    with engine.connect() as conn:
        rejeitadas = [dict(r) for r in conn.execute(text(f"""
            SELECT r.* FROM {C.TABELA_REJEITADAS} r
             WHERE r.etapa = 'dedup'
               AND NOT EXISTS (SELECT 1 FROM {C.TABELA_EVENTOS} e WHERE e.rej_id = r.rej_id)
             ORDER BY r.data_artigo, r.rej_id
        """)).mappings().all()]
    ops = db.carregar_operacoes_completas(engine)
    log.info("%d rejeições de dedup ainda não mescladas; %d operações na base.",
             len(rejeitadas), len(ops))

    def par(o):
        return {_norm_company(o.get("empresa_adquirente")), _norm_company(o.get("empresa_alvo"))}

    stats = {"rejeicoes": len(rejeitadas), "sem_par": 0, "ambiguas": 0,
             "atualizaram": 0, "so_contagem": 0}
    for rej in rejeitadas:
        cands = [o for o in ops if mesmo_par(rej, o)]
        if not cands:
            stats["sem_par"] += 1
            log.info("[rej %s] sem operação do mesmo par na base: %s / %s", rej["rej_id"],
                     rej.get("empresa_adquirente"), rej.get("empresa_alvo"))
            continue
        exatas = [o for o in cands if par(o) == par(rej)]
        if len(cands) > 1:
            stats["ambiguas"] += 1
        alvo = escolher_sobrevivente(exatas or cands)
        alt, div = mesclar(alvo, rej)
        # Valor que preencheria a linha mas destoa das OUTRAS linhas do mesmo par
        # (deal ainda não fundido, nomes diferentes) é suspeito de erro de
        # magnitude — caso real: US$ 8,1 bi num cluster Paramount/Warner de 81-111 bi.
        if "valor" in alt:
            outros = [o["valor"] for o in cands if o is not alvo and o.get("valor")]
            if outros and all(abs(alt["valor"] - v) / max(alt["valor"], v) > 0.05
                              for v in outros):
                div.append(f"valor {alt['valor']:,.0f} destoa das outras linhas do mesmo "
                           f"par ({', '.join(f'{v:,.0f}' for v in outros)}) — não aplicado")
                alt.pop("valor")
                alt.pop("moeda", None)
        if alt:
            stats["atualizaram"] += 1
            log.info("[rej %s] %s <- %s / %s (%s): %s", rej["rej_id"], _linha(alvo),
                     rej.get("empresa_adquirente"), rej.get("empresa_alvo"),
                     rej.get("data_artigo"),
                     {c: v for c, v in alt.items()
                      if c in ("status", "valor", "moeda", "data_artigo")})
        else:
            stats["so_contagem"] += 1
        for d in div:
            log.info("[rej %s]   ! divergência não aplicada: %s", rej["rej_id"], d)
        if aplicar:
            db.mesclar_duplicata(engine, int(alvo["op_id"]), rej,
                                 origem="mescla_retroativa", rej_id=int(rej["rej_id"]),
                                 decisao=(alt, div))
        alvo.update(alt)  # próximas rejeições do mesmo deal partem do estado novo

    if not aplicar:
        log.warning("SIMULAÇÃO: %d operações seriam atualizadas (use --aplicar).",
                    stats["atualizaram"])
    return stats


def etapa_validacao(engine, extractor, *, aplicar: bool, dias: int | None = None) -> dict:
    """Remove da base existente as operações que não são M&A. Retorna estatísticas.

    ``dias`` restringe às operações com ``data_artigo`` nos últimos N dias (ex.: 90
    = o recorte do PDF trimestral), para não gastar o orçamento Groq na base toda.
    """
    import datetime as dt

    from industry.noticias.mna import config as C
    from industry.noticias.mna import db
    from industry.noticias.mna.pipeline import LimiteDiario
    from sqlalchemy import text

    ops = db.carregar_operacoes_completas(engine)
    if dias is not None:
        corte = dt.date.today() - dt.timedelta(days=dias)
        ops = [o for o in ops if o.get("data_artigo") and o["data_artigo"] >= corte]
    # Texto do artigo por link, para dar contexto ao julgamento.
    with engine.connect() as conn:
        textos = {r[0]: r[1] for r in conn.execute(text(
            f"SELECT link, texto FROM {C.TABELA_POOL}")).fetchall()}
    log.info("%d operações a validar (%d artigos com texto disponível).",
             len(ops), len(textos))

    stats = {"total": len(ops), "removidas": 0, "mantidas": 0, "sem_texto": 0, "tpd": False}
    remover: list[int] = []
    rejeitadas: list[dict] = []
    try:
        for n, op in enumerate(ops, start=1):
            texto = textos.get(op.get("link_artigo"))
            if not texto:
                # Sem o artigo não há como julgar correspondência — mantém.
                stats["sem_texto"] += 1
                continue
            aceita, motivo, falha = extractor.validar(
                op, titulo=str(op.get("titulo_artigo") or ""),
                fonte=str(op.get("fonte_artigo") or ""),
                data=str(op.get("data_artigo") or ""), texto=str(texto))
            if falha:
                stats["mantidas"] += 1
                continue
            if aceita:
                stats["mantidas"] += 1
            else:
                log.info("[%d/%d] - não é M&A: %s — %s", n, len(ops), _linha(op), motivo)
                remover.append(int(op["op_id"]))
                rejeitadas.append({**op, "motivo_rejeicao": motivo,
                                   "etapa": "validacao_retroativa"})
                stats["removidas"] += 1
            if n % 50 == 0:
                log.info("[%d/%d] %d removidas até aqui.", n, len(ops), stats["removidas"])
    except LimiteDiario as exc:
        stats["tpd"] = True
        log.warning("Chaves Groq esgotadas — aplicando o parcial e parando. %s", exc)

    if not aplicar:
        log.warning("SIMULAÇÃO: %d operações seriam removidas (use --aplicar).",
                    len(remover))
        return stats

    if remover:
        db.append_rejeitadas(engine, rejeitadas)
        n = db.remover_operacoes(engine, remover)
        log.info("%d operações removidas da base e arquivadas.", n)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--etapa", choices=["dedup", "validacao", "mesclar_rejeitadas"],
                    required=True)
    ap.add_argument("--dias", type=int, default=None,
                    help="validacao: só operações com data_artigo nos últimos N dias")
    ap.add_argument("--aplicar", action="store_true",
                    help="efetiva as remoções (o padrão é apenas simular)")
    args = ap.parse_args()

    from industry.noticias.mna import task as t
    from industry.noticias.mna.pipeline import GroqExtractor
    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

    t.setup()  # garante mna_operations_rejeitadas, mna_operations_eventos e colunas novas
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        if args.etapa == "mesclar_rejeitadas":
            stats = etapa_mesclar_rejeitadas(engine, aplicar=args.aplicar)
            log.info("Etapa %s encerrada: %s", args.etapa, stats)
            return
        extractor = GroqExtractor()
        if args.etapa == "dedup":
            stats = etapa_dedup(engine, extractor, aplicar=args.aplicar)
        else:
            stats = etapa_validacao(engine, extractor, aplicar=args.aplicar, dias=args.dias)
        log.info("Etapa %s encerrada: %s", args.etapa, stats)
        if stats.get("tpd"):
            log.warning("Re-execute amanhã para continuar (resumível).")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
