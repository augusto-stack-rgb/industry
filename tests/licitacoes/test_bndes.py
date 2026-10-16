#!/usr/bin/env python3
"""Testes das regras puras do coletor BNDES — sem rede.

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

from industry.licitacoes import bndes  # noqa: E402
from industry.licitacoes.config import GRUPOS, SOURCE_STABLE_ID  # noqa: E402

FALHAS: list[str] = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        FALHAS.append(f"{nome}: esperado {esperado!r}, obtido {obtido!r}")


def sec(t):
    print(f"\n── {t}")


sec("valor efetivo")
# Aberta: só estimado.
check("só estimado", bndes._valor_efetivo({"5": "65535087.71", "6": "0.00"}), "65535087.71")
# Encerrada: contratado vale, mesmo menor que o estimado.
check("estimado e contratado", bndes._valor_efetivo({"5": "100000", "6": "250000"}), "250000.0")
check("contratado menor", bndes._valor_efetivo({"5": "500000", "6": "250000"}), "500000.0")
# Sem informação não pode virar zero — zero é "custo nulo", não "não informado".
check("ambos zero", bndes._valor_efetivo({"5": "0.00", "6": "0.00"}), "")
check("ambos vazios", bndes._valor_efetivo({"5": "", "6": ""}), "")
check("ausentes", bndes._valor_efetivo({}), "")
check("lixo", bndes._valor_efetivo({"5": "n/d", "6": ""}), "")
check("formato BR", bndes._valor_efetivo({"5": "1.234.567,89"}), "1234567.89")

sec("_num")
check("positivo", bndes._num("280250.0"), 280250.0)
check("zero vira None", bndes._num("0.00"), None)
check("negativo vira None", bndes._num("-5"), None)
check("vazio", bndes._num(""), None)
check("None", bndes._num(None), None)

sec("data de publicação")
check("publicação presente", bndes._data_publicacao({"9": "08/09/2026", "7": "23/09/2026"}),
      "08/09/2026")
# Sem publicação NÃO cai para abertura: abertura é posterior e faria edital
# antigo parecer recente. Vazio deixa o pipeline usar run_date.
check("sem publicação não usa abertura", bndes._data_publicacao({"9": "", "7": "28/09/2026"}), "")
check("campo ausente", bndes._data_publicacao({}), "")

sec("contrato com o resto do pipeline")
# Os títulos nativos que o coletor emite têm que estar no GRUPOS, senão o bronze
# descarta a coluna em silêncio.
for titulo, canonico in [
    ("Identificação da licitação", "numero"),
    ("Modalidade", "tipo"),
    ("Descrição", "objeto"),
    ("Situação da licitação - PTP", "situacao"),
    ("Valor estimado da licitação", "valor_estimado"),
    ("Valor contratado", "valor_contratado"),
    ("Data de publicação do edital", "data_publicacao"),
    ("Data de abertura da licitação", "data_abertura"),
    ("Razão social do fornecedor", "contratado"),
]:
    check(f"GRUPOS[{canonico}] aceita {titulo!r}", titulo in GRUPOS[canonico], True)

check("fonte tem id estável", SOURCE_STABLE_ID.get(bndes.FONTE), ["id"])
check("CPF/CNPJ do fornecedor não é coletado", "17" in bndes.CAMPOS_IGNORADOS, True)
check("coluna template de arquivos é ignorada", "15" in bndes.CAMPOS_IGNORADOS, True)

sec("anexos")


class _RespFalsa:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _SessaoFalsa:
    def __init__(self, payload=None, erro=None):
        self._p, self._e = payload, erro

    def get(self, url, timeout=None):
        if self._e:
            raise self._e
        return _RespFalsa(self._p)


docs = bndes._listar_arquivos(
    _SessaoFalsa([{"fileName": "/arquivos/licitacoes/Pregão Eletrônico 026-2026/Edital.pdf",
                   "extension": "PDF"}]),
    "Pregão Eletrônico 026/2026")
check("1 anexo", len(docs), 1)
check("label é o nome do arquivo", docs[0]["label"], "Edital.pdf")
check("url absoluta e escapada", docs[0]["url"],
      "https://www.bndes.gov.br/arquivos/licitacoes/Preg%C3%A3o%20Eletr%C3%B4nico%20026-2026/Edital.pdf")

# Falha de uma pasta não pode derrubar a coleta.
check("erro de rede vira lista vazia",
      bndes._listar_arquivos(_SessaoFalsa(erro=RuntimeError("timeout")), "X"), [])
check("resposta não-lista vira lista vazia",
      bndes._listar_arquivos(_SessaoFalsa({"erro": "nao encontrado"}), "X"), [])
check("item sem fileName é ignorado",
      bndes._listar_arquivos(_SessaoFalsa([{"extension": "PDF"}]), "X"), [])

print("\n" + "=" * 72)
if FALHAS:
    print(f"FALHAS: {len(FALHAS)}")
    for f in FALHAS:
        print("  ✗", f)
    sys.exit(1)
print("todos os testes passaram")
