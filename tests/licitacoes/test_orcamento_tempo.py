#!/usr/bin/env python3
"""Testes dos orçamentos de tempo das duas tasks que consultam o PNCP.

O repo não tem pytest; isto roda com `python` puro e sai com código 1 se algo
falhar, para poder entrar em CI sem nova dependência.

    python tests/licitacoes/test_orcamento_tempo.py

Motivo (medido em 17/09/2026, rodando a DAG inteira): a API de detalhe do PNCP
responde em ~0,3s quando está boa e custa ~15s por item quando recusa (4
tentativas com espera crescente). O `backfill_prazos` passou de 17 minutos e
gravou 1 prazo; com o teto de 300 itens, o pior caso passa de uma hora ocupando
um worker. E o `filtrar_acionaveis` roda DENTRO do `montar_gold`, que é a
montante do e-mail.

Nada aqui vai à rede nem ao banco: a consulta ao PNCP e a escrita são stubadas.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _candidatos_dags():
    """Raiz do repo (tests/<DAG>/este_arquivo.py) e o caminho dentro do container."""
    aqui = Path(__file__).resolve()
    if len(aqui.parents) >= 3:
        yield aqui.parents[2]


for _p in _candidatos_dags():
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from industry.licitacoes import db, gold, pncp  # noqa: E402

falhas: list[str] = []


def checa(rotulo: str, obtido, esperado) -> None:
    ok = obtido == esperado
    print(f"  {'ok  ' if ok else 'FALHA'} {rotulo}: {obtido!r}")
    if not ok:
        falhas.append(f"{rotulo}: obtido {obtido!r}, esperado {esperado!r}")


def controle(n: int) -> str:
    return f"33657248000189-1-{n:06d}/2026"


class Relogio:
    """time.monotonic falso: cada consulta ao PNCP custa `passo` segundos."""

    def __init__(self, passo: float):
        self.t, self.passo = 0.0, passo

    def monotonic(self):
        return self.t

    def avanca(self):
        self.t += self.passo


def roda_backfill(n_itens, passo, *, orcamento=None):
    """Executa backfill_prazos com relógio, rede e banco falsos."""
    rel = Relogio(passo)
    consultados, gravados = [], []
    orig = (gold.time, pncp.buscar_prazo_proposta, db.get_engine, db.meta_bronze,
            db.upsert_prazos, gold.MAX_BACKFILL_SEGUNDOS)

    class _T:
        monotonic = staticmethod(rel.monotonic)
        sleep = staticmethod(lambda *_a: None)

    ids = [f"pncp::2026-{i}" for i in range(n_itens)]

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): return [(i,) for i in ids]

    class _Eng:
        def connect(self): return _Conn()

    def _busca(cnpj, ano, seq, s=None):
        consultados.append(seq)
        rel.avanca()
        return (True, {"data_abertura_proposta": None,
                       "data_encerramento_proposta": "2026-12-01",
                       "situacao_pncp": "Divulgada no PNCP"})

    gold.time = _T
    pncp.buscar_prazo_proposta = _busca
    db.get_engine = lambda: _Eng()
    db.meta_bronze = lambda ids_: {i: {"numero_controle_pncp": controle(n)}
                                   for n, i in enumerate(ids_, start=1)}
    db.upsert_prazos = lambda lote: gravados.extend(lote)
    if orcamento is not None:
        gold.MAX_BACKFILL_SEGUNDOS = orcamento
    try:
        res = gold.backfill_prazos(limite=n_itens)
    finally:
        (gold.time, pncp.buscar_prazo_proposta, db.get_engine, db.meta_bronze,
         db.upsert_prazos, gold.MAX_BACKFILL_SEGUNDOS) = orig
    return res, consultados, gravados


print("\n── backfill_prazos · API rápida: processa tudo")
res, consultados, gravados = roda_backfill(60, passo=0.3, orcamento=600)
checa("consultou todos", len(consultados), 60)
checa("gravou todos", len(gravados), 60)
checa("não parou por tempo", res["parou_por_tempo"], False)
checa("o retorno conta os gravados", res["gravados"], 60)

print("\n── backfill_prazos · API em recusa: para no orçamento")
res, consultados, gravados = roda_backfill(300, passo=15, orcamento=600)
checa("parou por tempo", res["parou_por_tempo"], True)
checa("consultou só o que coube (600s / 15s)", len(consultados), 41)
checa("o retorno não mente sobre o total pendente", res["sem_prazo"], 300)

print("\n── backfill_prazos · o que já foi consultado NÃO se perde")
# Era o risco de cortar por tempo: a versão antiga acumulava tudo para gravar no
# fim, então parar no meio jogava fora o trabalho de API já feito.
checa("gravou em lote antes de parar", len(gravados) >= 25, True)
checa("gravou tudo que consultou", len(gravados), len(consultados))

print("\n── filtrar_acionaveis · orçamento próprio, e mantém quem não deu tempo")
rel = Relogio(30)
orig = (gold.time, pncp.buscar_prazo_proposta, db.meta_bronze, db.upsert_prazos,
        gold.MAX_ACIONAVEL_SEGUNDOS)


class _T2:
    monotonic = staticmethod(rel.monotonic)
    sleep = staticmethod(lambda *_a: None)


def _busca2(cnpj, ano, seq, s=None):
    rel.avanca()
    # janela FECHADA: sem o corte por tempo, todas seriam descartadas
    return (True, {"data_abertura_proposta": "2026-01-01",
                   "data_encerramento_proposta": "2026-01-02",
                   "situacao_pncp": "Divulgada no PNCP"})


df = pd.DataFrame([{"id_unificado": f"pncp::2026-{i}", "fonte": "PNCP",
                    "objeto": "x"} for i in range(20)])
gold.time = _T2
pncp.buscar_prazo_proposta = _busca2
db.meta_bronze = lambda ids_: {i: {"numero_controle_pncp": controle(n)}
                               for n, i in enumerate(ids_, start=1)}
db.upsert_prazos = lambda lote: None
gold.MAX_ACIONAVEL_SEGUNDOS = 100
try:
    out = gold.filtrar_acionaveis(df)
finally:
    (gold.time, pncp.buscar_prazo_proposta, db.meta_bronze, db.upsert_prazos,
     gold.MAX_ACIONAVEL_SEGUNDOS) = orig

# Orçamento 100s, 30s por consulta: a checagem vem ANTES da consulta, então
# passam os itens em t=0, 30, 60 e 90 — 4 consultadas, todas com janela fechada
# e portanto excluídas. Em t=120 o orçamento estourou e as 16 restantes ficam sem
# olhar e são MANTIDAS: nunca se descarta por não ter consultado.
checa("mantém quem não deu tempo de consultar", len(out), 16)

print("\n" + "=" * 72)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("todos os testes passaram")
