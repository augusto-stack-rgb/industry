#!/usr/bin/env python3
"""Testes das fontes fora do PNCP (Sebrae, Petronect, Banco Mundial, PNUD, UNGM) e
da etapa de detalhes — sem rede, sem banco.

    python tests/licitacoes/test_fontes_externas.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

from industry.licitacoes import banco_mundial, detalhes, petronect, pnud, sebrae, ungm  # noqa: E402
from industry.licitacoes.bronze import normalizar_para_bronze  # noqa: E402
from industry.licitacoes.db import normalizar_modalidade  # noqa: E402
from industry.licitacoes.termos import termos_exterior  # noqa: E402

FALHAS: list[str] = []


def checar(nome, cond, detalhe=""):
    print(("  ok   " if cond else "  FALHA ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


print("\n── termos em outros idiomas")
checar("inglês", termos_exterior("Consultancy on machine learning for forecasting")
       == ["machine learning", "forecasting"])
checar("AI só em maiúsculas", termos_exterior("AI-powered speech tool") == ["ai"]
       and termos_exterior("j'ai une idée") == [])
checar("espanhol", "ciencia de datos" in termos_exterior("Consultoría en Ciencia de Datos"))
checar("português também", "inteligencia artificial" in termos_exterior("Inteligência Artificial"))
checar("supply chain sozinho não basta lá fora",
       termos_exterior("Procurement of servers for supply chain") == [])

print("\n── modalidade normalizada das fontes novas")
for bruto, esperado in [
    ("CPSI (LC 182/2021)", "CPSI / Desafio de inovação"),
    ("Desafio da Inovação", "CPSI / Desafio de inovação"),
    ("Licitação (Lei 13.303)", "Licitação (Lei 13.303)"),
    ("Contratação direta (Lei 13.303)", "Dispensa"),
    ("Lei 13.303 - Presencial", "Pregão Presencial"),
    ("IC - Individual contractor", "Internacional - Consultor individual"),
    ("Request for Expression of Interest — Consultant Qualification Selection",
     "Internacional - Manifestação de interesse"),
    ("RFP - Request for proposal", "Internacional - Proposta (RFP)"),
    ("Pré-qualificação", "Pré-qualificação"),
]:
    checar(f"{bruto} -> {esperado}", normalizar_modalidade(bruto) == esperado,
           normalizar_modalidade(bruto))

print("\n── Sebrae")
it = {"Id": 14971, "Numero": "CONC/90001-SEBRAE-SP-2026", "NumeroProcesso": "P1",
      "Objeto": "Consultoria em  Data Science e inteligência artificial",
      "StatusDescricao": "Em Andamento", "DataPublicacao": "/Date(1771470000000)/",
      "DataAbertura": "/Date(1775790000000)/", "DataEncerramento": None,
      "TipoJulgamentoDescricao": "Maior Pontuação Geral Global", "TipoMenorPrecoDescricao": "Nenhum",
      "Local": "PORTAL COMPRAS.GOV", "ZSebrae": {"Descricao": "SEBRAE SÃO PAULO"},
      "LicitacaoModalidade": {"Descricao": "Concorrência"}}
ln = sebrae._linha(it, ["data science"])
checar("UF pelo nome da unidade", ln["uf"] == "SP", ln["uf"])
checar("data em Brasília (meia-noite)", ln["data_publicacao"] == "2026-02-19T00:00:00",
       ln["data_publicacao"])
checar("sem encerramento: prazo = abertura", ln["data_encerramento_proposta"] == ln["data_abertura"])
checar("critério sem 'Nenhum'", ln["criterio_julgamento"] == "Maior Pontuação Geral Global")
checar("link do detalhe", ln["link"].endswith("Detalhe?id=14971"))
checar("UF de 'MATO GROSSO DO SUL'", sebrae._uf("SEBRAE MATO GROSSO DO SUL") == "MS")

print("\n── Petronect")
op = {"OPPORT_NUM": "7004662577", "OPPORT_TYPE": "LDNT", "COMPANY_DESC": "TRANSPETRO",
      "STATUS_DESC": "Publicado", "POSTING_DATE": "2026-09-24", "OPPORT_DESCR": "Serviços de TI",
      "DESC_OBJ_CONTRAT": "Serviços de tecnologia da informação", "START_DATE": "2026-09-25",
      "START_HOUR": "08:00:00", "END_DATE": "2026-10-23", "END_HOUR": "12:00:00",
      "OPEN_DATE": "0000-00-00", "DISPUTE_MODE": "02",
      "ANEXOS": [{"DESCRIPTION": "Edital-7004662577.pdf", "PHIO_OBJID": "ABC"}]}
ln = petronect._linha(op, ["tecnologia da informacao"])
checar("prazo com hora", ln["data_encerramento_proposta"] == "2026-10-23T12:00:00")
checar("data zerada vira vazio", ln["data_sessao"] == "")
checar("edital com URL de download", ln["edital_url"].endswith("attachmentSet('ABC')/$value"))
checar("modo de disputa", ln["modo_disputa"] == "Fechado")
checar("texto inclui itens", "rede" in petronect._texto({**op, "ITEMS": [{"ITEM_DESC": "rede"}]}))

print("\n── Banco Mundial")
n = {"id": "OP1", "notice_type": "Request for Expression of Interest",
     "procurement_method_name": "Individual Consultant Selection", "procurement_group": "CS",
     "bid_reference_no": "REF-1", "bid_description": "Data &amp; AI <b>strategy</b>",
     "project_name": "Digital Gov", "project_ctry_name": "Kenya", "notice_status": "Published",
     "submission_date": "2026-10-13T00:00:00Z", "submission_deadline_date": "2026-10-28T00:00:00Z",
     "submission_deadline_time": "10:00", "project_id": "P1"}
ln = banco_mundial._linha(n, ["ai"])
checar("HTML limpo", ln["objeto"].startswith("Data & AI strategy"), ln["objeto"])
checar("prazo com hora", ln["data_encerramento_proposta"] == "2026-10-28T10:00:00")
checar("país", ln["pais"] == "Kenya")
checar("sem INDV é empresa", ln["categoria"] == "Empresa" and ln["pessoa_fisica"] == "nao")
ln_pf = banco_mundial._linha({**n, "procurement_method_code": "INDV"}, ["ai"])
checar("INDV é pessoa física", ln_pf["pessoa_fisica"] == "sim")

print("\n── PNUD e UNGM")
checar("PNUD data com hora", pnud._data("02-Dec-26 05:00 AM (New York time)") == "2026-12-02T05:00:00")
checar("PNUD data sem hora", pnud._data("17-Oct-26") == "2026-10-17")
html = ('<div class="tableRow dataRow" data-noticeid="317517">'
        + "".join(f'<div class="tableCell">{v}</div>' for v in [
            "", "Call for Data analysis Open in a new window", "20-Oct-2026 12:00 (GMT 2.00)",
            "09-Oct-2026", "ILO", "Call for individual consultants", "rfx_1", "Multiple"])
        + "</div>")
av = ungm._avisos(html)
checar("UNGM lê a linha", len(av) == 1 and av[0]["agencia"] == "ILO", str(av))
checar("UNGM tira 'Open in a new window'", av and av[0]["titulo"] == "Call for Data analysis")
checar("UNGM data", ungm._data(av[0]["prazo"]) == "2026-10-20T12:00:00")

print("\n── bronze: país vai para local_abertura")
df = normalizar_para_bronze(pd.DataFrame([banco_mundial._linha(n, ["ai"])]),
                            banco_mundial.FONTE)
checar("local_abertura = país", df.iloc[0]["local_abertura"] == "Kenya")
checar("data = publicação", str(df.iloc[0]["data"]).startswith("2026-10-13"))

print("\n── detalhes")
checar("_ts com hora", detalhes._ts("2026-11-03T09:00:00") == "2026-11-03T09:00:00")
checar("_ts BR sem hora", detalhes._ts("05/11/2026") == "2026-11-05T00:00:00")
checar("_juntar tira 'Não se aplica' se há outro",
       detalhes._juntar(["Não se aplica", "Menor preço", "Menor preço"]) == "Menor preço")
checar("_juntar mantém 'Não se aplica' sozinho", detalhes._juntar(["Não se aplica"]) == "Não se aplica")
r = {"id_unificado": "x", "tipo": "Pregão Eletrônico", "valor": "", "edital_url": "",
     "documentos": json.dumps([{"titulo": "Termo de Referência", "url": "https://a/tr.pdf"},
                               {"titulo": "Edital 1", "url": "https://a/ed.pdf"}])}
b = detalhes._base(r)
checar("anexos do bronze", len(b["anexos"]) == 2)
checar("link do edital prefere 'edital'", b["link_edital"] == "https://a/ed.pdf", b["link_edital"])
checar("modalidade normalizada", b["modalidade"] == "Pregão Eletrônico")
txt = "O valor total estimado da contratação é de R$ 1.234.567,89 (um milhão...)"
m = detalhes._RX_VALOR.search(txt)
checar("valor no edital", m and m.group(1) == "1.234.567,89")
checar("exclusiva ME/EPP no edital",
       bool(detalhes._RX_ME_EPP.search("licitação exclusiva para microempresas e EPP")))

print("\n" + "=" * 72)
if FALHAS:
    print(f"{len(FALHAS)} falha(s): {FALHAS}")
    sys.exit(1)
print("todos os testes passaram")
