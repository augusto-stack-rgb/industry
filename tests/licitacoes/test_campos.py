#!/usr/bin/env python3
"""Testes dos campos que as fontes entregam e o pipeline perdia — sem rede, sem banco.

Cobre:
  D5  ComprasNet gravava edital sem link nenhum, tendo o número de controle PNCP
  D6  `data` ficava vazia em fontes que TÊM data de publicação (Banrisul)
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

import pandas as pd  # noqa: E402

from industry.licitacoes.bronze import normalizar_para_bronze  # noqa: E402
from industry.licitacoes.comprasnet import _link_pncp  # noqa: E402

FALHAS: list[str] = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        FALHAS.append(f"{nome}: esperado {esperado!r}, obtido {obtido!r}")


def sec(t):
    print(f"\n── {t}")


# ── D5 — link do ComprasNet reconstruído do número de controle PNCP ──────────
sec("D5 · _link_pncp")
check("edital do BNB", _link_pncp("07237373000120-1-000082/2026"),
      "https://pncp.gov.br/app/editais/07237373000120/2026/82")
check("zeros à esquerda somem", _link_pncp("00360305000104-1-001155/2024"),
      "https://pncp.gov.br/app/editais/00360305000104/2024/1155")
check("modalidade 2 também vale", _link_pncp("00000000000191-2-000030/2025"),
      "https://pncp.gov.br/app/editais/00000000000191/2025/30")
# Contrato não tem controle PNCP: fica sem link, e isso é limitação da fonte.
check("vazio", _link_pncp(""), "")
check("None", _link_pncp(None), "")
check("formato errado", _link_pncp("lixo"), "")
check("cnpj curto", _link_pncp("123-1-1/2026"), "")
check("sem ano", _link_pncp("07237373000120-1-000082"), "")

# ── D6 — `data` cai para a publicação, nunca para abertura/sessão ───────────
sec("D6 · fallback de `data` no bronze")

# Banrisul: a chave nativa é `publishdate`, que GRUPOS["data"] não lista —
# por isso `data` ficava vazia com 5.433 datas de publicação reais no banco.
banrisul = pd.DataFrame([{
    "id": "354221", "publishdate": "2026-09-04", "startdate": "2026-09-23",
    "description": "Aquisição de veículo", "biddingtypename": "Pregão",
    "issuancenumber": "123/2026", "processnumber": "P1", "tradeoffice": "X",
}])
b = normalizar_para_bronze(banrisul, "Banrisul")
check("Banrisul · data = publicação", b["data"].iloc[0], "2026-09-04")
check("Banrisul · data_publicacao preservada", b["data_publicacao"].iloc[0], "2026-09-04")
check("Banrisul · data != abertura", b["data"].iloc[0] == b["data_abertura"].iloc[0], False)

# BCB só tem abertura (posterior à publicação). `data` fica vazia de propósito:
# preenchê-la com a abertura faria edital antigo parecer recente. O pipeline cai
# no run_date, que é a regra documentada.
bcb = pd.DataFrame([{
    "id": "9", "IdEdital": "9", "DataAbertura": "2026-10-01",
    "Objeto": "X", "NomeTipo": "Pregão", "Numero": "1", "Ano": "2026",
}])
c = normalizar_para_bronze(bcb, "BCB - Banco Central do Brasil")
check("BCB · data continua vazia", c["data"].iloc[0], "")
check("BCB · abertura preservada", c["data_abertura"].iloc[0], "2026-10-01")

# Fonte que já traz `data` própria não pode ser sobrescrita pela publicação.
pncp = pd.DataFrame([{"id": "1", "data": "2026-01-05",
                      "data_publicacao": "2026-02-09", "objeto": "X"}])
d = normalizar_para_bronze(pncp, "PNCP")
check("PNCP · data própria vence", d["data"].iloc[0], "2026-01-05")

# Sem nenhuma das duas, continua vazio (e o run_date assume lá na frente).
vazio = pd.DataFrame([{"id": "1", "objeto": "X"}])
e = normalizar_para_bronze(vazio, "Qualquer")
check("sem data nenhuma", e["data"].iloc[0], "")

print("\n" + "=" * 72)
if FALHAS:
    print(f"FALHAS: {len(FALHAS)}")
    for f in FALHAS:
        print("  ✗", f)
    sys.exit(1)
print("todos os testes passaram")
