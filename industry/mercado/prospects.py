"""Universo de prospects: uma linha por empresa, ganhando ou não, com colunas para ordenar.

    python -m industry.mercado.prospects      # depois de fornecedores.py e nichos.py

Decidido com o Augusto em 06/11/2026: prospect é qualquer empresa que trabalhou ou se
cadastrou para licitar, em qualquer nicho (os técnicos e de TI primeiro). Nada é cortado:
share, nicho e o resto viram colunas e entram num score que só serve para ordenar.

- **Universo**: fornecedores ativos do SICAF (cadastro para licitar) agrupados pela raiz do
  CNPJ, mais todo vencedor do PNCP que não aparece no SICAF.
- **Vitórias** (contratos do PNCP e resultados do Compras.gov.br, qualquer categoria):
  licitações distintas por modalidade. Quem perdeu não aparece: não há API aberta com os
  participantes.
- **Nicho e share**: de ``mercado.fornecedor_nicho`` (nichos.py), só nos nichos de serviço
  já agrupados. Quem nunca ganhou recebe o nicho provável pelo CNAE (o nicho mais comum entre
  os vencedores do mesmo CNAE).

Saídas: ``mercado.prospect_universo`` e, em ``.data/mercado/``, o parquet completo e um CSV
com quem ganhou alguma coisa ou está em nicho técnico.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from industry.licitacoes.termos import normalizar
from industry.mercado.nichos import CORTES, DADOS, _PUBLICO
from industry.shared.connections import MERCADO_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

# licitações distintas ganhas por raiz e por modalidade, em qualquer categoria
VITORIAS = """
with c as (
    select left(ct.fornecedor_ni, 8) as raiz, ct.numero_controle_compra as compra, ct.valor_global as valor,
           coalesce(cp.modalidade_id, cm.modalidade_id) as m
      from mercado.contrato ct
      left join mercado.contratacao cp on cp.numero_controle_pncp = ct.numero_controle_compra
      left join mercado.compra_modalidade cm on cm.numero_controle_compra = ct.numero_controle_compra
     where ct.fornecedor_tipo_pessoa = 'PJ'
), r as (
    select left(fornecedor_ni, 8) as raiz, numero_controle_compra as compra, valor_total as valor,
           case substr(id_compra_item, 7, 2) when '05' then 6 when '03' then 4 when '06' then 8
                                             when '07' then 9 when '00' then 12 end as m
      from mercado.resultado where tipo_pessoa = 'PJ' and situacao like 'Informado%'
), t as (select * from c union all select * from r)
select raiz,
       count(distinct compra) filter (where m in (2, 3, 4, 5, 6, 7, 10, 11)) as ganhou_pregao_concorrencia,
       count(distinct compra) filter (where m = 8) as ganhou_dispensa,
       count(distinct compra) filter (where m in (9, 12)) as ganhou_sem_disputa,
       count(distinct compra) filter (where m is null) as ganhou_modalidade_desconhecida,
       count(distinct compra) as ganhou_total,
       sum(valor) as valor_ganho
  from t where raiz is not null group by raiz"""


def calcular() -> dict:
    eng = get_sqlalchemy_engine(MERCADO_PG)
    with eng.connect() as c:
        dias = dict(c.execute(text("""select fonte, count(distinct dia) from mercado.coleta_log
                                       where status = 'ok' and linhas > 0 group by 1""")).fetchall())
        sicaf = pd.read_sql(text("""
            select distinct on (raiz) raiz, ni, nome, habilitado_licitar, cnae, cnae_nome, porte, uf, municipio
              from mercado.fornecedor where ativo and raiz is not null
             order by raiz, (substr(ni, 9, 4) = '0001') desc, ni"""), c)
        vit = pd.read_sql(text(VITORIAS), c)
        fn = pd.read_sql(text("""select f.raiz, f.nicho, f.valor_ano, f.share, f.nome as nome_venc, f.ni as ni_venc,
                                        n.nome as nicho_nome, n.valor_ano as nicho_ano, n.hhi, n.tecnico
                                   from mercado.fornecedor_nicho f join mercado.nicho n using (nicho)"""), c)
    log.info(f"[prospects] SICAF {len(sicaf)} raízes, vencedores {len(vit)}, em nichos {fn['raiz'].nunique()}")

    # nicho principal de cada vencedor: onde ele mais ganhou
    princ = fn.sort_values("valor_ano", ascending=False).drop_duplicates("raiz").set_index("raiz")
    u = pd.DataFrame({"raiz": pd.unique(pd.concat([sicaf["raiz"], vit["raiz"]]))})
    u = u.merge(sicaf, on="raiz", how="left").merge(vit, on="raiz", how="left")
    u["nome"] = u["nome"].fillna(u["raiz"].map(princ["nome_venc"]))
    u["ni"] = u["ni"].fillna(u["raiz"].map(princ["ni_venc"]))
    u["no_sicaf"] = u["cnae"].notna()
    for col, src in (("nicho_nome", "nicho_nome"), ("share_nicho", "share"), ("ganho_ano_nicho", "valor_ano"),
                     ("nicho_ano", "nicho_ano"), ("hhi_nicho", "hhi"), ("nicho_tecnico", "tecnico")):
        u[col] = u["raiz"].map(princ[src])
    u["nicho_origem"] = np.where(u["nicho_nome"].notna(), "vitórias", None)

    # nicho provável pelo CNAE, para quem não ganhou nada nos nichos agrupados
    cn = (u.dropna(subset=["nicho_nome", "cnae"]).groupby(["cnae", "nicho_nome"]).size().rename("n").reset_index()
           .sort_values("n", ascending=False).drop_duplicates("cnae").set_index("cnae"))
    tec_cnae = u.dropna(subset=["nicho_nome", "cnae"]).groupby("cnae")["nicho_tecnico"].mean()
    sem = u["nicho_nome"].isna() & u["cnae"].notna()
    u.loc[sem, "nicho_nome"] = u.loc[sem, "cnae"].map(cn["nicho_nome"])
    u.loc[sem, "nicho_tecnico"] = u.loc[sem, "cnae"].map(tec_cnae) >= 0.5
    u.loc[sem & u["nicho_nome"].notna(), "nicho_origem"] = "CNAE"
    # CNAE de TI (61 telecom, 62 software e TI, 63 dados) conta como técnico mesmo sem nicho
    cnae_ti = u["cnae"].fillna(0).astype(int).astype(str).str[:2].isin(["61", "62", "63"])
    u["nicho_tecnico"] = u["nicho_tecnico"].fillna(False).astype(bool) | cnae_ti

    for col in ("ganhou_pregao_concorrencia", "ganhou_dispensa", "ganhou_sem_disputa",
                "ganhou_modalidade_desconhecida", "ganhou_total"):
        u[col] = u[col].fillna(0).astype(int)
    cobertura = 365 / max(dias.get("contratos", 30), 1)
    u["ganho_ano_aprox"] = u["valor_ganho"].fillna(0) * cobertura
    u["porte"] = u["porte"].fillna("não informado")
    u["entidade_publica"] = u["nome"].fillna("").map(lambda t: bool(_PUBLICO.search(normalizar(t))))
    u["gigante"] = (u["ganho_ano_aprox"] >= CORTES["demais_ganho_max"]) & ~u["porte"].str.upper().isin(["ME", "EPP"])

    # score só para ordenar (0 a 100): atividade, espaço no nicho, técnico, porte
    atividade = np.minimum(np.log1p(u["ganhou_pregao_concorrencia"] * 2 + u["ganhou_dispensa"]) / np.log1p(40), 1)
    espaco = np.where(u["share_nicho"].notna(), 1 - np.minimum(u["share_nicho"].fillna(0) / 0.10, 1), 0.5)
    tamanho = np.minimum(np.log10(u["nicho_ano"].fillna(1e6).clip(lower=1e6)) - 6, 3) / 3
    u["score"] = (40 * atividade + 20 * espaco + 15 * tamanho + 15 * u["nicho_tecnico"]
                  + 10 * u["porte"].str.upper().isin(["ME", "EPP"]) + 5 * u["habilitado_licitar"].fillna(False).astype(bool)
                  - 30 * u["gigante"] - 100 * u["entidade_publica"]).clip(lower=0).round(1)
    u = u.sort_values("score", ascending=False)

    cols = ["ni", "nome", "score", "porte", "uf", "municipio", "cnae", "cnae_nome", "habilitado_licitar", "no_sicaf",
            "nicho_nome", "nicho_origem", "nicho_tecnico", "share_nicho", "ganho_ano_nicho", "nicho_ano", "hhi_nicho",
            "ganhou_total", "ganhou_pregao_concorrencia", "ganhou_dispensa", "ganhou_sem_disputa",
            "ganhou_modalidade_desconhecida", "ganho_ano_aprox", "gigante", "entidade_publica"]
    u = u[cols]
    u.to_sql("prospect_universo", eng, schema="mercado", if_exists="replace", index=False, chunksize=20000)
    DADOS.mkdir(parents=True, exist_ok=True)
    hoje = date.today().isoformat()
    u.to_parquet(DADOS / f"prospect_universo_{hoje}.parquet", index=False)
    vista = u[(u["ganhou_total"] > 0) | u["nicho_tecnico"]].copy()
    vista["share_nicho"] = (vista["share_nicho"] * 100).round(3)   # em %
    vista.rename(columns={"share_nicho": "share_nicho_pct"}).to_csv(
        DADOS / f"prospects_{hoje}.csv", index=False, sep=";")
    r = {"universo": len(u), "no_sicaf": int(u["no_sicaf"].sum()), "ganharam": int((u["ganhou_total"] > 0).sum()),
         "tecnicos": int(u["nicho_tecnico"].sum()), "no_csv": len(vista)}
    log.info(f"[prospects] {r}")
    return r


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print(calcular())
