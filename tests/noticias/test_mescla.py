#!/usr/bin/env python3
"""Testes das regras puras da mescla de duplicatas e do blocking — sem banco, sem rede.

Casos tirados do histórico real de ``mna_operations_rejeitadas`` (04/08 a 02/10/2026).

    python tests/licitacoes/test_bndes.py
"""
from __future__ import annotations

import sys
from pathlib import Path


def _candidatos_dags():
    aqui = Path(__file__).resolve()
    if len(aqui.parents) >= 3:
        yield aqui.parents[2]


for _p in _candidatos_dags():
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from industry.noticias.mna import pipeline as P  # noqa: E402

FALHAS: list[str] = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        FALHAS.append(f"{nome}: esperado {esperado!r}, obtido {obtido!r}")


def op(adq, alvo, status=None, valor=None, data=None, **kw):
    return {"empresa_adquirente": adq, "empresa_alvo": alvo, "status": status,
            "valor": valor, "moeda": kw.pop("moeda", "USD" if valor else None),
            "data_artigo": data, "link_artigo": kw.pop("link", f"http://x/{adq}/{data}"),
            "titulo_artigo": kw.pop("titulo", f"{adq} {alvo}"), **kw}


# ── status ────────────────────────────────────────────────────────────────────
# Advent + Bain / Amil: base "rumor", notícia de cancelamento rejeitada como duplicata.
base = op("Advent International e Bain Capital", "Amil", "rumor", 10e9, "2026-06-29")
nova = op("Advent International e Bain Capital", "Amil", "cancelado", None, "2026-09-10")
alt, div = P.mesclar(base, nova)
check("amil status", alt.get("status"), "cancelado")
check("amil link", alt.get("link_artigo"), nova["link_artigo"])
check("amil data", alt.get("data_artigo"), "2026-09-10")
check("amil valor preservado", "valor" in alt, False)

# Weatherford / NCS: anunciado -> concluído.
alt, _ = P.mesclar(op("Weatherford", "NCS Multistage", "anunciado", None, "2026-06-02"),
                   op("Weatherford", "NCS Multistage", "concluído", None, "2026-09-01"))
check("weatherford", alt.get("status"), "concluído")

# Notícia de etapa ANTERIOR não rebaixa o deal.
alt, _ = P.mesclar(op("A Corp", "B Corp", "concluído", None, "2026-05-01"),
                   op("A Corp", "B Corp", "rumor", None, "2026-09-01"))
check("não rebaixa", "status" in alt, False)

# Mesma etapa: só conta notícia, não troca o artigo.
alt, _ = P.mesclar(op("A Corp", "B Corp", "anunciado", 5e9, "2026-05-01", setor="Energia"),
                   op("A Corp", "B Corp", "anunciado", 5e9, "2026-09-01", setor="Energia"))
check("mesma etapa sem alteração", alt, {})

# Cancelamento ANTIGO não derruba deal concluído depois.
alt, div = P.mesclar(op("A Corp", "B Corp", "concluído", None, "2026-08-01"),
                     op("A Corp", "B Corp", "cancelado", None, "2026-03-01"))
check("cancelado velho", "status" in alt, False)
check("cancelado velho registra", len(div), 1)

# Deal cancelado que reaparece "anunciado": não reabre sozinho, registra.
alt, div = P.mesclar(op("A Corp", "B Corp", "cancelado", None, "2026-08-01"),
                     op("A Corp", "B Corp", "anunciado", None, "2026-09-01"))
check("cancelado não reabre", "status" in alt, False)
check("cancelado não reabre registra", len(div), 1)

# Status desconhecido na base: qualquer etapa conhecida avança.
alt, _ = P.mesclar(op("A Corp", "B Corp", None), op("A Corp", "B Corp", "rumor"))
check("base sem status", alt.get("status"), "rumor")

# ── valor ─────────────────────────────────────────────────────────────────────
# Asur / Motiva: status E valor chegam juntos na duplicata.
alt, _ = P.mesclar(op("Asur", "Motiva", "anunciado", None, "2026-06-12"),
                   op("Asur", "Motiva", "concluído", 5.1e9, "2026-09-02"))
check("asur status", alt.get("status"), "concluído")
check("asur valor", alt.get("valor"), 5.1e9)
check("asur moeda", alt.get("moeda"), "USD")

# Tidewater: mesmo status, só o valor faltava.
alt, _ = P.mesclar(op("Tidewater", "Wilson Sons Ultratug", "concluído", None, "2026-02-27"),
                   op("Tidewater", "Wilson Sons Ultratug", "concluído", 5e8, "2026-09-03"))
check("tidewater só valor", set(alt), {"valor", "moeda"})

# Paramount / Warner com US$ 429 bi: divergência registrada, valor NÃO trocado.
alt, div = P.mesclar(op("Paramount", "Warner Bros. Discovery", "anunciado", 110e9, "2026-07-24"),
                     op("Paramount", "Warner Bros. Discovery", "em negociação", 429e9, "2026-09-18"))
check("warner valor intacto", "valor" in alt, False)
check("warner status não regride", "status" in alt, False)
check("warner divergência", len(div), 1)

# Diferença pequena (arredondamento) não é divergência.
_, div = P.mesclar(op("Tessenderlo", "FMC", "anunciado", 400e6),
                   op("Tessenderlo", "FMC", "concluído", 403e6))
check("tessenderlo arredondamento", div, [])

# Valor em texto vindo direto da extração.
alt, _ = P.mesclar(op("A Corp", "B Corp", "anunciado"),
                   op("A Corp", "B Corp", "anunciado", "US$ 2,5 bilhões"))
check("valor texto", alt.get("valor"), 2.5e9)

# ── complemento ───────────────────────────────────────────────────────────────
alt, _ = P.mesclar(op("A Corp", "B Corp", "anunciado", setor=None, pais_alvo="Brasil"),
                   op("A Corp", "B Corp", "anunciado", setor="Saúde", pais_alvo="Chile"))
check("complementa vazio", alt.get("setor"), "Saúde")
check("não sobrescreve preenchido", "pais_alvo" in alt, False)

# ── sobrevivente ──────────────────────────────────────────────────────────────
g = [dict(op("A", "B", "anunciado", None, "2026-01-01"), op_id=1),
     dict(op("A", "B", "concluído", None, "2026-02-01"), op_id=2),
     dict(op("A", "B", "rumor", 1e9, "2025-12-01"), op_id=3)]
check("sobrevivente prioriza valor", P.escolher_sobrevivente(g)["op_id"], 3)
check("sobrevivente desempata por etapa",
      P.escolher_sobrevivente(g[:2])["op_id"], 2)

# ── agrupamento exato ─────────────────────────────────────────────────────────
grupos, sem_nome = P.agrupar_por_par_exato([
    dict(op("Paramount Inc.", "Warner"), op_id=1), dict(op("paramount", "Warner"), op_id=2),
    dict(op("Netflix", "Warner"), op_id=3), dict(op(None, None), op_id=4)])
check("agrupa par exato", [sorted(o["op_id"] for o in g) for g in grupos], [[1, 2]])
check("sem nome", [o["op_id"] for o in sem_nome], [4])

# ── blocking com nomes curtos ─────────────────────────────────────────────────
check("token BP", P.tokens_empresa("BP"), {"bp"})
check("token C6 Bank", P.tokens_empresa("C6 Bank"), {"c6"})
check("token B3", P.tokens_empresa("B3 S.A."), {"b3"})
check("preposição curta fora", P.tokens_empresa("Banco do Brasil"), set())
check("mesmo_par Shell/BP", P.mesmo_par(op("Shell", "bp"), op("Shell plc", "BP")), True)
check("mesmo_par C6", P.mesmo_par(op("C6 Bank", "Alymente"), op("C6", "Alymente S.A.")), True)
check("par distinto", P.mesmo_par(op("BP", "Lightsource bp"), op("Shell", "BP")), False)


if __name__ == "__main__":
    if FALHAS:
        print(f"{len(FALHAS)} FALHA(S):")
        for f in FALHAS:
            print(" -", f)
        sys.exit(1)
    print("OK — todas as checagens da mescla passaram.")
