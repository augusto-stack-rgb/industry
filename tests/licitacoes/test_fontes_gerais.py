#!/usr/bin/env python3
"""Testes das fontes gerais (PNCP aberto, Querido Diário, BNDES inovação) — sem rede, sem banco.

    python tests/licitacoes/test_fontes_gerais.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

from industry.licitacoes import gold, pncp  # noqa: E402
from industry.licitacoes import querido_diario as qd  # noqa: E402
from industry.licitacoes.bndes_inovacao import _desembrulhar  # noqa: E402
from industry.licitacoes.pncp_abertas import _linha  # noqa: E402
from industry.licitacoes.termos import normalizar, termos_encontrados  # noqa: E402

FALHAS: list[str] = []


def checar(nome, cond, detalhe=""):
    print(("  ok   " if cond else "  FALHA ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


print("\n── termos (palavra inteira, sem acento)")
checar("acha IA e ML", termos_encontrados("Solução de Inteligência Artificial com aprendizado de máquina")
       == ["inteligencia artificial", "aprendizado de maquina"])
checar("sigla curta não casa dentro de palavra", termos_encontrados("Desfile patriota e carpa") == [])
checar("sigla curta casa sozinha", "iot" in termos_encontrados("plataforma IoT para sensores"))
checar("TI geral", "fabrica de software" in termos_encontrados("Contratação de Fábrica de Software"))
checar("objeto sem tema", termos_encontrados("Aquisição de cadeiras para escritório") == [])

print("\n── PNCP aberto: linha da listagem")
hoje = date.today()
it = {"orgaoEntidade": {"cnpj": "00394460000141", "razaoSocial": "Órgão X"},
      "unidadeOrgao": {"ufSigla": "DF", "municipioNome": "Brasília", "nomeUnidade": "U"},
      "anoCompra": 2026, "sequencialCompra": 77, "numeroControlePNCP": "00394460000141-1-000077/2026",
      "objetoCompra": "Desenvolvimento de modelos preditivos", "informacaoComplementar": "",
      "modalidadeNome": "Pregão - Eletrônico", "dataPublicacaoPncp": "2026-10-01T10:00:00",
      "dataEncerramentoProposta": f"{hoje + timedelta(days=10)}T09:00:00"}
ln = _linha(it, ["modelos preditivos"])
checar("link do portal", ln["link"] == "https://pncp.gov.br/app/editais/00394460000141/2026/77", ln["link"])
checar("controle PNCP vira id", ln["numero_controle_pncp"].endswith("/2026"))
checar("encerramento vai junto", ln["data_encerramento_proposta"].startswith(str(hoje + timedelta(days=10))))

print("\n── Querido Diário: marcador de contratação e ruído")
bom = normalizar("AVISO DE LICITAÇÃO Pregão Eletrônico 12/2026 contratação de empresa para "
                 "solução de inteligência artificial")
ruim = normalizar("CONTEÚDO PROGRAMÁTICO: inteligência artificial, aprendizado de máquina")
checar("trecho de licitação passa", bool(qd._CONTRATACAO.search(bom)) and not qd._RUIDO.search(bom))
checar("conteúdo de concurso é ruído", bool(qd._RUIDO.search(ruim)))
checar("modalidade inferida", qd._modalidade(bom) == "Pregão Eletrônico")
diario = ("DECRETO 1 dispõe sobre crédito suplementar. " + "x " * 800 +
          "AVISO DE LICITAÇÃO Pregão Eletrônico 7/2026 — contratação de empresa para solução "
          "de visão computacional. " + "y " * 800 +
          "Empresa vencedora: ACME, solução de inteligência artificial, valor R$ 80.025,00.")
jan = qd._janelas(diario)
checar("uma janela por ocorrência distante", len(jan) == 2, str(len(jan)))
checar("janela centrada no termo", "visao computacional" in jan[0] and "aviso de licitacao" in jan[0])
checar("resultado marcado como fechado", bool(qd._RESULTADO.search(jan[1]))
       and not qd._RESULTADO.search(jan[0]))

print("\n── BNDES inovação: SafeLinks")
sl = ("https://bra01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fwww.worldlabs.org"
      "%2Fopportunity%2Finteligencia-dados-saude&data=05")
checar("desembrulha o link", _desembrulhar(sl) == "https://www.worldlabs.org/opportunity/inteligencia-dados-saude")

print("\n── gold: encerramento da listagem dispensa a API de detalhe")
chamadas = []
orig = (gold.db.meta_bronze, gold.db.upsert_prazos, pncp.buscar_prazo_proposta)
gold.db.meta_bronze = lambda ids: {
    "a": {"numero_controle_pncp": "00394460000141-1-000077/2026",
          "data_encerramento_proposta": f"{hoje + timedelta(days=5)}T09:00:00"},
    "b": {"numero_controle_pncp": "00394460000141-1-000078/2026",
          "data_encerramento_proposta": f"{hoje - timedelta(days=1)}T09:00:00"},
}
gravados = []
gold.db.upsert_prazos = lambda rows: gravados.extend(rows) or len(rows)
pncp.buscar_prazo_proposta = lambda *a, **k: chamadas.append(a) or (False, None)
try:
    out = gold.filtrar_acionaveis(pd.DataFrame({"id_unificado": ["a", "b"]}))
finally:
    gold.db.meta_bronze, gold.db.upsert_prazos, pncp.buscar_prazo_proposta = orig
checar("aberto fica, fechado sai", out["id_unificado"].tolist() == ["a"], str(out["id_unificado"].tolist()))
checar("nenhuma consulta à API de detalhe", not chamadas, str(chamadas))
checar("prazos gravados", len(gravados) == 2)

print("\n" + "=" * 72)
if FALHAS:
    print(f"{len(FALHAS)} falha(s): {FALHAS}")
    sys.exit(1)
print("todos os testes passaram")
