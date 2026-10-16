#!/usr/bin/env python3
"""Testes das regras puras da pipeline de licitações — sem banco, sem rede.

O repo não tem pytest; isto roda com `python` puro e sai com código 1 se algo
falhar, para poder entrar em CI sem nova dependência.

    python tests/licitacoes/test_regras.py

Cobre os defeitos encontrados na auditoria de 16/09/2026:
  D2  score_combinado virava NaN -> NULL (guard de None não pegava NaN)
  B1  "Lei 13.303 - Eletrônico" caía em "Outros/Genérico"
  B2  to_date aceitava data fora do calendário ("2026-45-16")
  B3  to_date achava data BR no meio da string, mas não ISO
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from industry.licitacoes import db, score  # noqa: E402

FALHAS: list[str] = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        FALHAS.append(f"{nome}: esperado {esperado!r}, obtido {obtido!r}")


def sec(t):
    print(f"\n── {t}")


# ── D2 — combinação de sinais ────────────────────────────────────────────────
sec("D2 · combinar_sinais: ausência de sinal renormaliza, não vira NaN")

# O caso que produziu as 119 linhas NULL em produção: LLM pulado ("sem texto"),
# keyword 0, zero-shot alto. Antes: (nan, 3). Agora: média de kw e zs.
s, n = score.combinar_sinais(0.0, 0.9940, None)
check("sem llm · score", s, 0.497)
check("sem llm · n_sinais", n, 2)

# Idem quando o None já virou NaN pela coluna float64 do pandas — o caminho real.
col = pd.Series([None, 0.8], dtype="float64")
check("None na coluna float64 virou NaN", bool(pd.isna(col.iloc[0])), True)
check("e não é None", col.iloc[0] is None, False)
s, n = score.combinar_sinais(0.0, 0.9940, col.iloc[0])
check("nan do pandas · score", s, 0.497)
check("nan do pandas · n_sinais", n, 2)
check("np.nan cru · score", score.combinar_sinais(0.0, 0.9940, np.nan)[0], 0.497)

# Com nota do LLM os pesos são 0,05/0,05/0,9 (calibração de 15/10/2026):
# 0,05*1 + 0,05*0,5 + 0,9*0,9 = 0,885.
s, n = score.combinar_sinais(1.0, 0.5, 0.9)
check("3 sinais · score", s, 0.885)
check("3 sinais · n_sinais", n, 3)
# Sem LLM valem 0,15/0,15 renormalizados: (0,15*1 + 0,15*0,5) / 0,3 = 0,75.
check("sem LLM · score", score.combinar_sinais(1.0, 0.5, None)[0], 0.75)

# Nenhum sinal -> None de verdade (e não NaN), que é o único NULL legítimo.
s, n = score.combinar_sinais(None, None, None)
check("nenhum sinal · score", s, None)
check("nenhum sinal · n_sinais", n, 0)
check("nenhum sinal · não é NaN", s is None, True)

check("_ausente(None)", score._ausente(None), True)
check("_ausente(nan)", score._ausente(float("nan")), True)
check("_ausente(0.0)", score._ausente(0.0), False)

# ── D2 (parte 2) — texto curto não recebe sinal semântico ────────────────────
sec("D2 · objeto curto demais: nem LLM nem zero-shot opinam")
for curto in ["BENS", "ALIMENTOS", "TOKEN", "FECHADURA", "Reprografia", "", "   "]:
    check(f"insuficiente {curto!r}", score._texto_insuficiente(curto), True)
for longo in ["Contratação de consultoria atuarial",
              "Aquisição de mobiliário",       # 22 chars
              "x" * score.TEXTO_MIN_CHARS]:
    check(f"suficiente {longo[:24]!r}", score._texto_insuficiente(longo), False)
check("limite exato não é insuficiente",
      score._texto_insuficiente("a" * score.TEXTO_MIN_CHARS), False)
check("um abaixo do limite é insuficiente",
      score._texto_insuficiente("a" * (score.TEXTO_MIN_CHARS - 1)), True)

# Com texto curto sobra só a keyword. "BENS" (sem keyword) tem que ir a zero —
# e não a 0,497 como iria se o zero-shot continuasse opinando.
check("curto sem keyword · score", score.combinar_sinais(0.0, None, None)[0], 0.0)
check("curto sem keyword · n_sinais", score.combinar_sinais(0.0, None, None)[1], 1)
check("curto sem keyword fica abaixo do corte do e-mail",
      score.combinar_sinais(0.0, None, None)[0] > 0.35, False)
# Keyword é casamento exato de termo: continua valendo em texto curto.
check("curto COM keyword ainda pontua", score.combinar_sinais(1.0, None, None)[0], 1.0)

# Score válido nunca pode ser NaN — é o que o Postgres transforma em NULL.
for kw, zs, llm in [(0.0, 0.62, None), (0.0, 0.31, np.nan), (1.0, 0.0, None)]:
    sc, _ = score.combinar_sinais(kw, zs, llm)
    check(f"score nunca NaN ({kw},{zs},{llm})", bool(pd.isna(sc)) if sc is not None else False, False)

# ── B1 — modalidade Lei 13.303 ───────────────────────────────────────────────
sec("B1 · normalizar_modalidade: Lei 13.303 sem a palavra 'pregão'")
check("Lei 13.303 - Eletrônico", db.normalizar_modalidade("Lei 13.303 - Eletrônico"),
      "Pregão Eletrônico")
check("Lei 13.303 - Presencial", db.normalizar_modalidade("Lei 13.303 - Presencial"),
      "Pregão Presencial")
check("Licitação 13.303/16 Eletrônica",
      db.normalizar_modalidade("Licitação 13.303/16 Eletrônica"), "Pregão Eletrônico")
check("13303 sem separador", db.normalizar_modalidade("Licitação 13303 Eletrônica"),
      "Pregão Eletrônico")
# Sem qualificador continua no rótulo neutro (comportamento antigo preservado).
check("Pregão (Lei 13.303)", db.normalizar_modalidade("Pregão (Lei 13.303)"),
      "Pregão (Lei 13.303)")
check("Lei 13.303 puro", db.normalizar_modalidade("Lei 13.303"), "Pregão (Lei 13.303)")
# Modalidades mais específicas continuam ganhando de 13.303.
check("Dispensa 13.303 → Dispensa", db.normalizar_modalidade("Dispensa 13.303"), "Dispensa")
check("Concorrência 13.303 → Concorrência",
      db.normalizar_modalidade("Concorrência Lei 13.303"), "Concorrência")
check("Inexigibilidade 13.303 → Inexigibilidade",
      db.normalizar_modalidade("Inexigibilidade 13.303"), "Inexigibilidade")

sec("B1 · não-regressão do resto do normalizador")
for entrada, esperado in {
    "": "(vazio)", None: "(vazio)",
    "Inexigibilidade": "Inexigibilidade", "INAPLICABILIDADE": "Inexigibilidade",
    "Credenciamento": "Credenciamento", "Chamamento Público": "Chamamento Público",
    "Diálogo Competitivo": "Diálogo Competitivo", "Concurso": "Concurso",
    "Carta Convite": "Convite", "Tomada de Preços": "Tomada de Preços",
    "Leilão": "Leilão/Concessão", "Concessão": "Leilão/Concessão", "PPP": "Leilão/Concessão",
    "Nota de Empenho": "Empenho", "Contrato": "Contrato", "Concorrência": "Concorrência",
    "Dispensa": "Dispensa", "Dispensa Eletrônica": "Dispensa Eletrônica",
    "Pregão Eletrônico": "Pregão Eletrônico", "Pregão Presencial": "Pregão Presencial",
    "Pregão": "Pregão Eletrônico", "qualquer outra coisa": "Outros/Genérico",
    "{'tipo': 'Pregão Eletrônico'}": "Pregão Eletrônico",
    "{quebrado": "Outros/Genérico",
}.items():
    check(f"modalidade {entrada!r}", db.normalizar_modalidade(entrada), esperado)

# ── B2 — to_date valida o calendário ─────────────────────────────────────────
sec("B2 · to_date rejeita data que não existe")
check("mês 45 (ISO)", db.to_date("2026-45-16"), None)
check("dia 32 (ISO)", db.to_date("2026-09-32"), None)
check("31 de fevereiro (BR)", db.to_date("31/02/2026"), None)
check("29/02 em ano não bissexto", db.to_date("29/02/2027"), None)
check("29/02 em ano bissexto", db.to_date("29/02/2028"), "2028-02-29")

# Toda saída de to_date tem que sobreviver a date.fromisoformat — é o que os
# consumidores chamam sem try (task._na_janela, dashboard _aplicar_janela).
sec("B2 · toda saída é aceita por date.fromisoformat")
from datetime import date as _d  # noqa: E402
for cru in ["2026-45-16", "2026-09-32", "31/02/2026", "2026-09-16", "16/09/2026",
            "2026-02-29", "lixo", "", None, "2026-13-01", "00/00/0000"]:
    saida = db.to_date(cru)
    if saida is not None:
        try:
            _d.fromisoformat(saida)
        except ValueError:
            FALHAS.append(f"to_date({cru!r}) devolveu {saida!r}, que quebra fromisoformat")

# ── B3 — simetria ISO/BR ─────────────────────────────────────────────────────
sec("B3 · ISO e BR encontrados na mesma posição")
check("ISO com prefixo", db.to_date("publicado 2026-09-16"), "2026-09-16")
check("BR com prefixo", db.to_date("publicado 16/09/2026"), "2026-09-16")
check("ISO com sufixo", db.to_date("2026-09-16 (retificado)"), "2026-09-16")
check("ISO com hora", db.to_date("2026-09-16T10:30:00"), "2026-09-16")

sec("B3 · não-regressão do to_date")
check("iso simples", db.to_date("2026-09-16"), "2026-09-16")
check("br simples", db.to_date("16/09/2026"), "2026-09-16")
check("br 1 dígito", db.to_date("1/9/2026"), "2026-09-01")
check("vazio", db.to_date(""), None)
check("none", db.to_date(None), None)
check("nan", db.to_date(float("nan")), None)
check("lixo", db.to_date("sem data"), None)

# ── relatório ────────────────────────────────────────────────────────────────
print("\n" + "=" * 72)
if FALHAS:
    print(f"FALHAS: {len(FALHAS)}")
    for f in FALHAS:
        print("  ✗", f)
    sys.exit(1)
print("todos os testes passaram")
