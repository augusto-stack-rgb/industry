#!/usr/bin/env python3
"""Carga histórica / dreno retroativo do backlog de operações de M&A.

A DAG recorrente já processa TODAS as notícias de M&A ainda não processadas a
cada rodada (não só as do dia) — ou seja, o backlog histórico é drenado
naturalmente ao longo das rodadas diárias, limitado apenas pelo teto diário de
tokens da Groq (TPD).

Este script força esse dreno numa sessão só: chama ``task.extrair()`` em loop
até que (a) não reste nenhuma notícia nova a processar, ou (b) o limite diário de
tokens seja atingido. Como é resumível (via ``noticias.mna_extraction_log``),
pode ser re-executado no dia seguinte — quando as cotas Groq zeram — para
continuar de onde parou.

Ambiente: precisa de INDUSTRY_PG_URL e GROQ_API_KEY_* (ver .env.example) e das
dependências do gate zero-shot (torch/transformers). Rode dentro da imagem:

    docker compose run --rm dagster-daemon \
        python -m industry.noticias.mna.carga_historica

Ou materialize o asset ``mna_operacoes`` manualmente algumas vezes no Dagster
(mesma lógica, uma rodada por materialização).
"""

from __future__ import annotations

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("carga_historica_mna")

# Trava de segurança: nº máximo de iterações numa sessão (cada iteração é uma
# passada completa de extração). Evita loop infinito se algo não convergir.
MAX_ITERACOES = 200


def main() -> None:
    from industry.noticias.mna import task as t

    # Garante as tabelas antes do primeiro dreno.
    t.setup()

    total_ops = total_proc = 0
    for i in range(1, MAX_ITERACOES + 1):
        stats = t.extrair()
        proc = stats.get("processados", 0)
        ops = stats.get("ops_extraidas", 0)
        total_proc += proc
        total_ops += ops
        log.info("Iteração %d: processados=%d ops=%d novos_no_pool=%d tpd=%s consolidado=%d",
                 i, proc, ops, stats.get("pool_novos", 0), stats.get("tpd_atingido"),
                 stats.get("total_consolidado", 0))

        if stats.get("tpd_atingido"):
            log.warning("Limite diário de tokens (TPD) atingido — parando. Re-execute "
                        "amanhã para continuar o dreno (resumível).")
            break
        if proc == 0:
            log.info("Nada mais novo a processar — backlog drenado.")
            break

    log.info("Carga histórica encerrada: %d artigos processados, %d operações nesta sessão.",
             total_proc, total_ops)


if __name__ == "__main__":
    main()
