#!/usr/bin/env python3
"""Testes do filtro de tema e da lista de exclusão (gold) — sem rede, sem banco.

    python tests/licitacoes/test_tema.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

from industry.licitacoes import gold  # noqa: E402
from industry.licitacoes.silver import bronze_para_silver  # noqa: E402
from industry.licitacoes.termos import termos_encontrados, termos_nucleo  # noqa: E402

FALHAS: list[str] = []


def checar(nome, cond, detalhe=""):
    print(("  ok   " if cond else "  FALHA ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


print("\n── termos")
checar("IA maiúsculo conta", termos_encontrados("Solução com IA para triagem") == ["ia"])
checar("ia minúsculo não conta", termos_encontrados("ia a reunião") == [])
checar("BI entra no tema", "power bi" in termos_encontrados("Painel em Power BI"))
checar("BI não é núcleo", termos_nucleo("Painel em Power BI") == [])
checar("IA é núcleo", termos_nucleo("Câmeras com IA") == ["ia"])

print("\n── lista de exclusão")
casos_mantidos = [
    "Solução de integração de dados para gestão da saúde, com licenciamento",
    "Videomonitoramento urbano com inteligência artificial e leitura de placas",
    "Manutenção preditiva com engenharia de dados",
    "Simulação de eventos discretos para o porto",
    "Hospedagem em nuvem de sistema de informação",
    "Avaliação técnica da qualidade das obras com processamento e análise de dados",
    "Câmeras com inteligência artificial para vigilância patrimonial",
]
for o in casos_mantidos:
    checar(f"mantém: {o[:50]}", not gold._objeto_sujo(o))
casos_excluidos = [
    "Serviço de limpeza de ar-condicionado da divisão de tecnologia da informação",
    "Prestação de serviços com dedicação exclusiva de mão de obra terceirizada",
    "Aquisição de notebooks e impressoras para a secretaria de tecnologia da informação",
    "Fornecimento de gêneros alimentícios",
]
for o in casos_excluidos:
    checar(f"exclui: {o[:50]}", gold._objeto_sujo(o))

print("\n── filtro de tema no gold")
df = pd.DataFrame({"id_unificado": ["a", "b", "c"],
                   "objeto": ["Plataforma de ciência de dados", "Aquisição de cadeiras", ""]})
checar("só fica o que está no tema", gold.filtrar_tema(df)["id_unificado"].tolist() == ["a"])

print("\n── silver marca no_tema")
s = bronze_para_silver(pd.DataFrame({"id_unificado": ["a", "b"], "fonte": ["X", "X"],
                                     "objeto": ["Modelos preditivos de demanda", "Reforma do prédio"]}))
checar("no_tema", s["no_tema"].tolist() == [True, False], str(s["no_tema"].tolist()))

print("\n── portais de CPSI")
from industry.licitacoes import cpsi_portais as cp  # noqa: E402
from industry.licitacoes.config import FONTES_INOVACAO  # noqa: E402

checar("prazo dd/mm sem ano", cp._prazo_no_texto("Inscrições até 02/11", 2026) == "2026-11-02")
checar("prazo por extenso", cp._prazo_no_texto("inscrições até 15 de junho de 2026", 2026) == "2026-06-15")
checar("sem prazo", cp._prazo_no_texto("Edital publicado", 2026) == "")
checar("abertos tira os encerrados",
       [r["id"] for r in cp._abertos([{"id": "a", "data_encerramento_proposta": "2000-01-01"},
                                      {"id": "b", "data_encerramento_proposta": ""}])] == ["b"])
df = pd.DataFrame({"id_unificado": ["x"], "objeto": ["Cenas 3D para perícia"], "fonte": [cp.FONTE_GOVTECH]})
checar("portal de inovação passa pelo tema", gold.filtrar_tema(df)["id_unificado"].tolist() == ["x"])
s2 = bronze_para_silver(pd.DataFrame({"id_unificado": ["x"], "fonte": [cp.FONTE_GOVTECH],
                                      "objeto": ["Cenas 3D para perícia"]}))
checar("portal de inovação marca no_tema", s2["no_tema"].tolist() == [True])
checar("fontes de inovação registradas", cp.FONTE_GOVTECH in FONTES_INOVACAO and cp.FONTE_COPASA in FONTES_INOVACAO)

print("\n── CPSI e prazo aberto")
from datetime import date, timedelta  # noqa: E402
from industry.licitacoes.bronze import filtrar_recentes  # noqa: E402
from industry.licitacoes.pncp_cpsi import _linha as linha_cpsi  # noqa: E402
from industry.licitacoes.termos import e_cpsi  # noqa: E402

checar("CPSI sem termo de tema conta como tema",
       e_cpsi("Contratação do teste de solução integrada de qualificação profissional"))
checar("LC 182 conta como tema", e_cpsi("licitação especial (Lei Complementar nº 182)"))
checar("objeto comum não é CPSI", not e_cpsi("Aquisição de cadeiras"))
velho = (date.today() - timedelta(days=25)).isoformat()
futuro = (date.today() + timedelta(days=10)).isoformat()
df = pd.DataFrame({"id_unificado": ["aberto", "fechado"], "data": [velho, velho],
                   "data_encerramento_proposta": [futuro, ""], "run_date": [velho, velho]})
checar("publicado há 25 dias mas aberto passa a recência",
       filtrar_recentes(df)["id_unificado"].tolist() == ["aberto"])
it = {"numero_controle_pncp": "06553556000191-1-000023/2026", "item_url": "/compras/06553556000191/2026/23",
      "description": "CPSI para contratação de pessoa física ou jurídica", "data_fim_vigencia": "2026-11-05T18:00:00"}
ln = linha_cpsi(it)
checar("CPSI: link público", ln["link"] == "https://pncp.gov.br/app/editais/06553556000191/2026/23", ln["link"])
checar("CPSI: aceita pessoa física", ln["pessoa_fisica"] == "sim")
checar("CPSI: prazo", ln["data_encerramento_proposta"].startswith("2026-11-05"))

print("\n" + "=" * 72)
if FALHAS:
    print(f"{len(FALHAS)} falha(s): {FALHAS}")
    sys.exit(1)
print("todos os testes passaram")
