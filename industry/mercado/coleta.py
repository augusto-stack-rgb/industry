"""Coleta do universo de compras públicas no Postgres local (schema ``mercado``).

    python -m industry.mercado.coleta --dias 90                  # últimos 90 dias, todas as fontes
    python -m industry.mercado.coleta --inicio 2026-08-01 --fim 2026-08-31 --fontes contratos
    python -m industry.mercado.coleta --dias 3 --fontes contratacoes --refazer

Resumível: cada (fonte, dia, modalidade) pronto fica em ``mercado.coleta_log`` e não é
coletado de novo. Os últimos ``REFAZER_DIAS`` dias são refeitos quando a última coleta
deles tem mais de ``REFAZER_APOS_H`` horas, porque o PNCP ainda recebe atualizações. Vai do dia mais recente para o mais antigo, então uma
coleta interrompida já deixa o período recente completo.

Itens e resultados vêm do Compras.gov.br, que chega com cerca de 1 mês de atraso: os
dias ainda vazios lá não são marcados como prontos, para a próxima rodada tentar de novo.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from industry.mercado import fontes
from industry.shared import ddl
from industry.shared.connections import MERCADO_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

FONTES = ("contratacoes", "contratos", "itens", "resultados")
TABELA = {"contratacoes": "contratacao", "contratos": "contrato", "itens": "item", "resultados": "resultado"}
CHAVE = {"contratacao": ("numero_controle_pncp",), "contrato": ("numero_controle_pncp",),
         "item": ("id_compra_item",), "resultado": ("id_compra_item", "sequencial_resultado")}
REFAZER_DIAS = 7
REFAZER_APOS_H = 12
COMPRAS_GOV_ATRASO_DIAS = 45   # dia vazio mais recente que isto não conta como pronto


def gravar(conn, tabela: str, linhas: list[dict]) -> int:
    """Upsert pela chave primária; a última versão de cada linha vence."""
    if not linhas:
        return 0
    from psycopg2.extras import Json, execute_values
    chave = CHAVE[tabela]
    unicas = {tuple(l[c] for c in chave): l for l in linhas if all(l.get(c) is not None for c in chave)}
    cols = list(next(iter(unicas.values())).keys())
    atualiza = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in chave)
    valores = [[Json(l[c]) if c == "dados" else l[c] for c in cols] for l in unicas.values()]
    with conn.cursor() as cur:
        execute_values(cur, f"insert into mercado.{tabela} ({', '.join(cols)}) values %s "
                            f"on conflict ({', '.join(chave)}) do update set {atualiza}, coletado_em = now()",
                       valores, page_size=500)
    return len(unicas)


def _prontos(conn) -> dict[tuple, bool]:
    """{(fonte, dia, chave): coletado há menos de REFAZER_APOS_H horas}"""
    with conn.cursor() as cur:
        cur.execute(f"select fonte, dia, chave, terminado_em > now() - interval '{REFAZER_APOS_H} hours' "
                    f"from mercado.coleta_log where status = 'ok'")
        return {(f, d, c): fresco for f, d, c, fresco in cur.fetchall()}


def _registrar(conn, fonte, dia, chave, linhas, paginas, status, erro=None):
    with conn.cursor() as cur:
        cur.execute("""insert into mercado.coleta_log (fonte, dia, chave, linhas, paginas, status, erro, terminado_em)
                       values (%s, %s, %s, %s, %s, %s, %s, now())
                       on conflict (fonte, dia, chave) do update set linhas = excluded.linhas,
                         paginas = excluded.paginas, status = excluded.status, erro = excluded.erro,
                         terminado_em = now()""", (fonte, dia, chave, linhas, paginas, status, erro))


def coletar(inicio: date, fim: date, quais=FONTES, refazer: bool = False) -> dict:
    ddl.aplicar(Path(__file__).with_name("schema.sql"), MERCADO_PG)
    eng = get_sqlalchemy_engine(MERCADO_PG)
    raw = eng.raw_connection()
    s = requests.Session()
    hoje = date.today()
    total = {f: 0 for f in quais}
    try:
        prontos = {} if refazer else _prontos(raw)
        dia = fim
        while dia >= inicio:
            recente = (hoje - dia).days < REFAZER_DIAS
            for fonte in quais:
                chaves = [str(m) for m in fontes.MODALIDADES_PNCP] if fonte == "contratacoes" else [""]
                for chave in chaves:
                    k = (fonte, dia, chave)
                    if k in prontos and (not recente or prontos[k]):
                        continue
                    t0 = time.time()
                    try:
                        if fonte == "contratacoes":
                            linhas, pags = fontes.contratacoes(s, dia, int(chave))
                        else:
                            linhas, pags = getattr(fontes, fonte)(s, dia)
                        n = gravar(raw, TABELA[fonte], linhas)
                        atrasado = fonte in ("itens", "resultados") and not n and \
                            (hoje - dia).days < COMPRAS_GOV_ATRASO_DIAS
                        _registrar(raw, fonte, dia, chave, n, pags, "pendente" if atrasado else "ok")
                        raw.commit()
                        total[fonte] += n
                        log.info(f"{dia} {fonte:<12} {chave:>2} {n:>6} linhas {pags:>4} pág {time.time() - t0:5.0f}s")
                    except Exception as e:  # noqa: BLE001 — um dia ruim não para o resto
                        raw.rollback()
                        _registrar(raw, fonte, dia, chave, 0, 0, "erro", str(e)[:500])
                        raw.commit()
                        log.warning(f"{dia} {fonte} {chave}: {e}")
            dia -= timedelta(days=1)
    finally:
        raw.close()
        eng.dispose()
    return total


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dias", type=int, help="últimos N dias até ontem")
    p.add_argument("--inicio", type=date.fromisoformat)
    p.add_argument("--fim", type=date.fromisoformat)
    p.add_argument("--fontes", default=",".join(FONTES))
    p.add_argument("--refazer", action="store_true", help="ignora o que já está pronto no log")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    fim = a.fim or date.today() - timedelta(days=1)
    inicio = a.inicio or fim - timedelta(days=(a.dias or 1) - 1)
    quais = tuple(f for f in a.fontes.split(",") if f in FONTES)
    print(json.dumps(coletar(inicio, fim, quais, a.refazer), ensure_ascii=False))


if __name__ == "__main__":
    main()
