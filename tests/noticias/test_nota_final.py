#!/usr/bin/env python3
"""Testes da nota final do ranking (nota do fato + bônus de cobertura) — sem banco, sem LLM.

Caso real: multas da ANP a distribuidoras (Óleo e Gás, 23-25/09/2026) — 7 matérias,
5 portais, nenhuma no top 5 do envio de 28/09 com a nota da versão.

    python tests/noticias/test_nota_final.py
"""
from __future__ import annotations

import sys
from pathlib import Path


def _candidatos_dags():
    aqui = Path(__file__).resolve()
    if len(aqui.parents) >= 3:
        yield aqui.parents[2]


# A cópia do repositório vem antes de outras no sys.path (que já pode estar no
# PYTHONPATH do container); senão o teste roda contra o código implantado.
for _p in reversed(list(_candidatos_dags())):
    if _p.is_dir():
        if str(_p) in sys.path:
            sys.path.remove(str(_p))
        sys.path.insert(0, str(_p))

import pandas as pd  # noqa: E402

from industry.noticias.envio import classificador as tc  # noqa: E402

FALHAS: list[str] = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        FALHAS.append(f"{nome}: esperado {esperado!r}, obtido {obtido!r}")


def df_anp():
    linhas = [
        ("2026-09-23", 85, "https://www.gov.br/anp/a"),
        ("2026-09-23", 70, "https://portalnovarejo.com.br/b"),
        ("2026-09-23", 90, "https://eixos.com.br/c"),
        ("2026-09-23", 85, "https://clickpetroleoegas.com.br/d"),
        ("2026-09-23", 80, "https://eixos.com.br/e"),
        ("2026-09-25", 55, "https://portalnovarejo.com.br/f"),
        ("2026-09-25", 90, "https://www.infomoney.com.br/g"),
    ]
    d = pd.DataFrame(linhas, columns=["data", "ranking_llm", "link"])
    d["data"] = pd.to_datetime(d["data"])
    d["ranking_llm"] = d["ranking_llm"].astype(float)
    d["componente"] = "anp"
    # notícia sozinha, de outro fato
    d.loc[len(d)] = [pd.Timestamp("2026-09-24"), 88.0, "https://valor.globo.com/x", "braskem"]
    return d


def rodar(modo):
    tc.NOTA_DO_FATO = modo
    rec, rep, bonus, final = tc._nota_final(df_anp())
    return rec, rep, bonus, final


# ── portal ───────────────────────────────────────────────────────────────────
check("portal www", tc._portal("https://www.gov.br/anp/x"), "gov.br")
check("portal sem www", tc._portal("http://eixos.com.br/y"), "eixos.com.br")
check("portal vazio", tc._portal(None), "")

# ── comportamento anterior ("propria") ───────────────────────────────────────
rec, rep, bonus, final = rodar("propria")
check("propria: representante = infomoney 90", final[6], 90.0)
check("propria: sem bônus", float(bonus.sum()), 0.0)
check("propria: sozinha mantém", final[7], 88.0)

# ── média do fato + bônus ────────────────────────────────────────────────────
rec, rep, bonus, final = rodar("media")
# 5 portais -> (5-1)*5 = 20, teto 15
check("media: bônus no teto", bonus[6], 15.0)
check("media: só o representante tem bônus", int((bonus > 0).sum()), 1)
media = df_anp().loc[:6, "ranking_llm"].mean()  # 79,3
check("media: nota do representante", final[6], round(media * 1.0 + 15))
check("media: fato passa a notícia sozinha de 88", final[6] > final[7], True)
check("media: repetição segue penalizada", bool((rep[:6] < 1).all()), True)
check("media: sozinha sem bônus e com a própria nota", (bonus[7], final[7]), (0.0, 88.0))

# ── máximo do fato ───────────────────────────────────────────────────────────
rec, rep, bonus, final = rodar("max")
check("max: nota do representante", final[6], 105.0)

tc.NOTA_DO_FATO = "media"

if __name__ == "__main__":
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print(" -", f)
        sys.exit(1)
    print("OK — todas as checagens da nota final passaram.")
