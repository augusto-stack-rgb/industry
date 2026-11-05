"""Modalidade das compras ligadas a contratos, quando a compra saiu antes da janela coletada.

    python -m industry.mercado.modalidades              # todas, até acabar (resumível)
    python -m industry.mercado.modalidades --limite 500

Contrato do PNCP não diz a modalidade, só o número da compra. Sem isso não dá para separar
inexigibilidade (sem disputa) de pregão: em 02/11/2026, 62% dos contratos de serviços e TI
tinham compra fora de ``mercado.contratacao``, e entre os conhecidos 46% eram inexigibilidade.

Uma consulta por compra, no limite do PNCP (~13 por minuto): 14,9 mil compras levam ~19 h.
Por isso vai primeiro nas compras dos fornecedores com mais licitações, que são os candidatos
a prospect. Grava em ``mercado.compra_modalidade`` a cada lote.
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import requests
from sqlalchemy import text

from industry.mercado.fontes import H, PNCP_CONSULTA, RITMO_PNCP
from industry.shared import ddl
from industry.shared.connections import MERCADO_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

LOTE = 25
PENDENTES = """
    with sem as (
        select ct.numero_controle_compra as compra, left(ct.fornecedor_ni, 8) as raiz
          from mercado.contrato ct
          left join mercado.contratacao cp on cp.numero_controle_pncp = ct.numero_controle_compra
          left join mercado.compra_modalidade cm on cm.numero_controle_compra = ct.numero_controle_compra
         where ct.categoria in ('Serviços', 'Informática (TIC)') and ct.fornecedor_tipo_pessoa = 'PJ'
           and ct.numero_controle_compra is not null
           and cp.numero_controle_pncp is null and cm.numero_controle_compra is null
    ), peso as (
        select raiz, count(distinct compra) as n from sem group by 1
    )
    select s.compra from sem s join peso p using (raiz)
     group by s.compra order by max(p.n) desc, s.compra"""


def consultar(s: requests.Session, compra: str) -> dict | None:
    """Dados da compra; ``None`` se o PNCP diz que ela não existe. Em 429 ou 5xx espera."""
    cnpj, _, resto = compra.split("-")
    seq, ano = resto.split("/")
    url = f"{PNCP_CONSULTA}/orgaos/{cnpj}/compras/{ano}/{int(seq)}"
    espera = 20
    for _ in range(7):
        RITMO_PNCP.esperar()
        try:
            r = s.get(url, headers=H, timeout=90)
            if r.status_code == 200 and r.text.lstrip()[:1] == "{":
                RITMO_PNCP.sucesso()
                return r.json()
            if r.status_code in (204, 404):
                return None
            if r.status_code == 429:
                RITMO_PNCP.bloqueio()
        except requests.RequestException:
            pass
        time.sleep(espera)
        espera = min(espera * 2, 300)
    raise RuntimeError(f"PNCP sem resposta para {compra}")


def resolver(limite: int | None = None) -> int:
    ddl.aplicar(Path(__file__).with_name("schema.sql"), MERCADO_PG)
    eng = get_sqlalchemy_engine(MERCADO_PG)
    with eng.connect() as c:
        pendentes = [r[0] for r in c.execute(text(PENDENTES)).fetchall()]
    log.info(f"[modalidades] {len(pendentes)} compras pendentes")
    s = requests.Session()
    feitas, lote = 0, []

    def gravar():
        with eng.begin() as c:
            c.execute(text("""insert into mercado.compra_modalidade
                                (numero_controle_compra, modalidade_id, modalidade_nome, modo_disputa_nome, achada)
                              values (:c, :mid, :mnome, :disp, :achada)
                              on conflict (numero_controle_compra) do update set modalidade_id = excluded.modalidade_id,
                                modalidade_nome = excluded.modalidade_nome, modo_disputa_nome = excluded.modo_disputa_nome,
                                achada = excluded.achada, consultado_em = now()"""), lote)
        lote.clear()

    for compra in pendentes[:limite]:
        try:
            d = consultar(s, compra)
        except ValueError:   # número fora do formato cnpj-1-seq/ano
            d = None
        except RuntimeError as e:
            log.warning(f"[modalidades] {e}; paro aqui, a próxima rodada continua")
            break
        lote.append({"c": compra, "mid": (d or {}).get("modalidadeId"), "mnome": (d or {}).get("modalidadeNome"),
                     "disp": (d or {}).get("modoDisputaNome"), "achada": d is not None})
        feitas += 1
        if len(lote) >= LOTE:
            gravar()
            log.info(f"[modalidades] {feitas}/{len(pendentes)}")
    if lote:
        gravar()
    log.info(f"[modalidades] fim: {feitas} consultadas")
    return feitas


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limite", type=int)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    resolver(p.parse_args().limite)
