#!/usr/bin/env python3
"""Testes do gate de prazo por número de controle do PNCP — sem banco, sem rede.

O repo não tem pytest; isto roda com `python` puro e sai com código 1 se algo
falhar, para poder entrar em CI sem nova dependência.

    python tests/licitacoes/test_prazo_controle.py

Cobre o defeito encontrado testando a branch unificada em 16/09/2026:
  o prazo da proposta só era consultado quando `fonte == 'PNCP'`, então os
  editais vindos do ComprasNet — que SÃO editais do PNCP, coletados por outra
  porta, com `numeroControlePNCP` e link pncp.gov.br/app/editais — nunca
  ganhavam prazo e apareciam para sempre como "prazo não consultado".
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

from industry.licitacoes import bronze, pncp  # noqa: E402

falhas: list[str] = []


def checa(rotulo: str, obtido, esperado) -> None:
    ok = obtido == esperado
    print(f"  {'ok  ' if ok else 'FALHA'} {rotulo}: {obtido!r}")
    if not ok:
        falhas.append(f"{rotulo}: obtido {obtido!r}, esperado {esperado!r}")


print("\n── identificadores_de_controle · formato canônico")
# Número real de um edital do Banco do Brasil colhido pelo ComprasNet.
checa("edital ComprasNet", pncp.identificadores_de_controle(
    "33657248000189-2-000128/2026"), ("33657248000189", "2026", "128"))
checa("zeros à esquerda somem do sequencial", pncp.identificadores_de_controle(
    "00360305000104-1-000186/2026"), ("00360305000104", "2026", "186"))
checa("sequencial de 1 dígito", pncp.identificadores_de_controle(
    "33657248000189-1-7/2025"), ("33657248000189", "2025", "7"))
checa("espaço em volta não atrapalha", pncp.identificadores_de_controle(
    "  33657248000189-2-000199/2026  "), ("33657248000189", "2026", "199"))

print("\n── identificadores_de_controle · o que NÃO é controle PNCP")
for ruim, rotulo in (
    ("", "vazio"),
    (None, "None"),
    ("sem-controle", "texto solto"),
    ("3365724800018-2-000128/2026", "CNPJ com 13 dígitos"),
    ("33657248000189-2-000128/26", "ano com 2 dígitos"),
    ("33657248000189-000128/2026", "sem o campo de tipo"),
    ("PREGAO 12/2026", "número de processo comum"),
):
    checa(f"recusa {rotulo}", pncp.identificadores_de_controle(ruim), None)

print("\n── identificadores_para_prazo · ordem de resolução")
# ComprasNet: traz o controle, NÃO traz cnpj/ano em coluna própria. É o caso que
# estava quebrado — antes do fix, nada aqui produzia uma consulta.
checa("ComprasNet (só controle)", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "33657248000189-2-000128/2026",
     "orgao_cnpj": None, "ano": None}), ("33657248000189", "2026", "128"))

# PNCP: traz as três coisas. O controle tem precedência, e as duas rotas têm
# que concordar — se divergissem, a consulta mudaria de alvo conforme a fonte.
checa("PNCP (controle + campos nativos concordam)", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "00360305000104-1-000186/2026",
     "orgao_cnpj": "00360305000104", "ano": "2026"}),
    ("00360305000104", "2026", "186"))

# Reserva: controle fora do formato canônico, mas com sequencial legível e
# cnpj/ano em coluna própria (como o coletor do PNCP preenche).
checa("reserva pelos campos nativos", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "algo-estranho-000042/2026",
     "orgao_cnpj": "33657248000189", "ano": "2026"}),
    ("33657248000189", "2026", "42"))

print("\n── identificadores_para_prazo · quem fica sem prazo, de propósito")
# Dispensa do ComprasNet (rota 5_consultarComprasSemLicitacao): sem controle
# PNCP e sem cnpj do órgão. Continua sem prazo — é limitação da fonte.
checa("dispensa sem controle", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "", "orgao_cnpj": "", "ano": "2026"}), None)
checa("meta vazia", pncp.identificadores_para_prazo({}), None)
checa("meta None", pncp.identificadores_para_prazo(None), None)
checa("sequencial sem cnpj", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "x-000042/2026", "orgao_cnpj": "", "ano": "2026"}), None)
checa("cnpj sem ano", pncp.identificadores_para_prazo(
    {"numero_controle_pncp": "x-000042/2026", "orgao_cnpj": "33657248000189",
     "ano": ""}), None)

print("\n── não-regressão: buscar_prazo_proposta recusa argumento vazio sem ir à rede")
checa("cnpj vazio", pncp.buscar_prazo_proposta("", "2026", "128"), (False, None))
checa("ano vazio", pncp.buscar_prazo_proposta("33657248000189", "", "128"), (False, None))
checa("seq None", pncp.buscar_prazo_proposta("33657248000189", "2026", None), (False, None))

print("\n── não-regressão: _seq_de_controle continua como estava")
checa("sequencial", pncp._seq_de_controle("cnpj-1-000108/2026"), "108")
checa("sem casar", pncp._seq_de_controle("nada"), None)

print("\n── base_id · o número de controle é DADO, não identidade")
# Regressão de 17/09/2026. O coletor do ComprasNet passou a emitir
# `numero_controle_pncp` (para dar link e prazo), e o `base_id` prefere esse
# campo — o que reescreveria o id_unificado de uma fonte que já tem 1.670 linhas
# no bronze. Medido contra a base real: 716 linhas voltariam como "novas", mesmo
# id_nativo e id diferente (`comprasnet::2026-90001` viraria
# `comprasnet::2026-33657248000189-1-000001/2026`).
checa("ComprasNet mantém o `numero` como base", bronze.base_id({
    "fonte": "ComprasNet", "numero_controle_pncp": "33657248000189-1-000001/2026",
    "numero": "90001", "id_nativo": "comprasnet-lic-33657248000189-1-000001/2026",
    "row_hash": "abcdef1234"}), "90001")

checa("ComprasNet sem numero cai no id_nativo", bronze.base_id({
    "fonte": "ComprasNet", "numero_controle_pncp": "33657248000189-1-000001/2026",
    "numero": "", "id_nativo": "comprasnet-lic-x", "row_hash": "abcdef1234"}),
    "comprasnet-lic-x")

# O PNCP sempre usou o controle como base: não pode mudar, sob pena do mesmo
# efeito ao contrário.
checa("PNCP continua usando o controle", bronze.base_id({
    "fonte": "PNCP", "numero_controle_pncp": "00360305000104-1-000186/2026",
    "numero": "186", "id_nativo": "x", "row_hash": "abcdef1234"}),
    "00360305000104-1-000186/2026")

checa("fonte sem controle usa o numero", bronze.base_id({
    "fonte": "BNDES", "numero_controle_pncp": "", "numero": "028",
    "id_nativo": "", "row_hash": "abcdef1234"}), "028")

checa("sem nada, cai no prefixo do row_hash", bronze.base_id({
    "fonte": "BNDES", "numero_controle_pncp": "", "numero": "",
    "id_nativo": "", "row_hash": "abcdef1234567890"}), "abcdef1234")

checa("Banestes (id_nativo único) ignora numero", bronze.base_id({
    "fonte": "Banestes - Editais e Avisos", "numero_controle_pncp": "",
    "numero": "025/2026", "id_nativo": "banestes-abc123", "row_hash": "abcdef1234"}),
    "banestes-abc123")


print("\n" + "=" * 72)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("todos os testes passaram")
