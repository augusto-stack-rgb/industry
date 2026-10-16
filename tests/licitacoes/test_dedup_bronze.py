#!/usr/bin/env python3
"""Testes do dedup do bronze — sem banco, sem rede (o acesso a banco é stubado).

O repo não tem pytest; isto roda com `python` puro e sai com código 1 se algo
falhar, para poder entrar em CI sem nova dependência.

    python tests/licitacoes/test_dedup_bronze.py

Cobre a reingestão em massa medida em 17/09/2026, rodando a DAG inteira contra
um clone de produção: o fallback de `data` do Banrisul mudou o conteúdo das
linhas, nenhuma das 5.283 casou por row_hash, e 5.316 voltaram como novas — numa
fonte que insere de 8 a 63 por dia. Uma correção de parser não pode reingerir a
fonte inteira.
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

import datetime as _dt  # noqa: E402

import pandas as pd  # noqa: E402

from industry.licitacoes import bronze, db  # noqa: E402

falhas: list[str] = []
BANRISUL = "Banrisul"


def checa(rotulo: str, obtido, esperado) -> None:
    ok = obtido == esperado
    print(f"  {'ok  ' if ok else 'FALHA'} {rotulo}: {obtido!r}")
    if not ok:
        falhas.append(f"{rotulo}: obtido {obtido!r}, esperado {esperado!r}")


def inserir(linhas, *, hashes=(), ids=(), nativos=()):
    """Roda `inserir_bronze` com o banco stubado; devolve os ids que entrariam."""
    gravadas = {}
    orig = (db.existing_bronze_hashes, db.existing_ids,
            db.existing_native_ids, db.insert_df, db.get_engine)
    db.existing_bronze_hashes = lambda *_a, **_k: set(hashes)
    db.existing_ids = lambda *_a, **_k: set(ids)
    db.existing_native_ids = lambda *_a, **_k: set(nativos)
    db.insert_df = lambda df, *a, **k: gravadas.update(df=df)
    db.get_engine = lambda: None
    try:
        df = bronze.normalizar_para_bronze(pd.DataFrame(linhas), linhas[0]["fonte"])
        out = bronze.inserir_bronze([df], _dt.date(2026, 9, 17))
    finally:
        (db.existing_bronze_hashes, db.existing_ids,
         db.existing_native_ids, db.insert_df, db.get_engine) = orig
    return list(out["id_unificado"]) if len(out) else []


print("\n── Banrisul · linha antiga guardada sob o esquema de id ANTIGO")
# É o caso real: a base do id já foi `numero` ("0132/2026") e virou `id_nativo`
# ("354076"), então a linha antiga do mesmo item não colide com o id de hoje. Com
# o conteúdo mudado (o fallback de `data`), o row_hash também não casa. Sobra o
# id_nativo como única identidade estável — e ele tem que bastar.
linha = {"fonte": BANRISUL, "id": "354076", "numero": "0132/2026",
         "objeto": "Contratação de serviços", "publishdate": "2026-07-16"}
checa("descarta pelo id_nativo, mesmo com id e hash diferentes",
      inserir([linha], nativos={(BANRISUL, "354076")}), [])

checa("sem o id_nativo no banco, entra (item de fato novo)",
      inserir([linha], nativos=set()), ["banrisul::2026-354076"])

checa("id_unificado já no banco também descarta",
      inserir([linha], ids={"banrisul::2026-354076"}), [])

checa("row_hash já no banco descarta (caminho de sempre)",
      inserir([linha], hashes={db.compute_row_hash(
          bronze.normalizar_para_bronze(pd.DataFrame([linha]), BANRISUL).iloc[0])}), [])

print("\n── ComprasNet · o número de controle não pode reescrever a identidade")
# 17/09/2026: o coletor passou a emitir `numero_controle_pncp`, e o base_id
# prefere esse campo. Sem a exceção, a fonte inteira voltaria sob ids novos.
cn = {"fonte": "ComprasNet", "id": "comprasnet-lic-33657248000189-1-000001/2026",
      "numero_controle_pncp": "33657248000189-1-000001/2026", "numero": "90001",
      "objeto": "Contratação", "data": "2026-01-05"}
checa("id continua vindo do numero", inserir([cn]), ["comprasnet::2026-90001"])
checa("e o id antigo descarta", inserir([cn], ids={"comprasnet::2026-90001"}), [])

print("\n── fontes fora da regra: nada muda")
pncp = {"fonte": "PNCP", "id": "x", "numero_controle_pncp": "00360305000104-1-000186/2026",
        "numero": "186", "objeto": "Contratação", "data": "2026-03-02"}
checa("PNCP segue com o controle como id",
      inserir([pncp]), ["pncp::2026-00360305000104-1-000186/2026"])
checa("PNCP não usa id_nativo para descartar",
      inserir([pncp], nativos={("PNCP", "x")}),
      ["pncp::2026-00360305000104-1-000186/2026"])

print("\n── colisão DENTRO do lote continua virando sufixo (bronze_pkey)")
# Dois itens distintos do Banrisul (id_nativo diferente) que caem no mesmo id.
a = {"fonte": BANRISUL, "id": "900", "numero": "1/2026", "objeto": "A",
     "publishdate": "2026-05-05"}
b = dict(a, id="900", objeto="B")   # mesmo id_nativo, conteúdo diferente
ids_lote = inserir([a, b])
checa("2 linhas do lote não violam a PK", len(set(ids_lote)), len(ids_lote))

print("\n" + "=" * 72)
if falhas:
    print(f"{len(falhas)} FALHA(S):")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("todos os testes passaram")
