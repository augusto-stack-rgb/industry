"""Cadastro de fornecedores do governo (SICAF) no Postgres local (``mercado.fornecedor``).

    python -m industry.mercado.fornecedores            # todos os ativos (~826 mil, 1 a 2 h)
    python -m industry.mercado.fornecedores --desde 400 # retoma a partir da página 400

É o universo de prospects: quem se cadastrou para licitar, mesmo sem ter ganho nada. O PNCP e
os resultados do Compras.gov.br só mostram o vencedor, e nenhuma API aberta lista os
participantes que perderam (conferido na especificação dos Dados Abertos em 06/11/2026).
"""
from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import requests

from industry.mercado.coleta import gravar
from industry.mercado.fontes import H, _get
from industry.shared import ddl
from industry.shared.connections import MERCADO_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

URL = "https://dadosabertos.compras.gov.br/modulo-fornecedor/1_consultarFornecedor"


def _linha(x: dict) -> dict:
    ni = x.get("cnpj") or x.get("cpf")
    return {"ni": ni, "raiz": (x.get("cnpj") or "")[:8] or None, "nome": x.get("nomeRazaoSocialFornecedor"),
            "habilitado_licitar": x.get("habilitadoLicitar"), "ativo": x.get("ativo"),
            "cnae": x.get("codigoCnae"), "cnae_nome": x.get("nomeCnae"), "porte": x.get("porteEmpresaNome"),
            "natureza_juridica": x.get("naturezaJuridicaNome"), "municipio": x.get("nomeMunicipio"),
            "uf": x.get("ufSigla"), "dados": x}


def coletar(desde: int = 1) -> int:
    ddl.aplicar(Path(__file__).with_name("schema.sql"), MERCADO_PG)
    eng = get_sqlalchemy_engine(MERCADO_PG)
    raw = eng.raw_connection()
    s = requests.Session()
    pagina, total, n = desde, None, 0
    try:
        while total is None or pagina <= total:
            d = _get(s, URL, {"pagina": pagina, "tamanhoPagina": 500, "ativo": "true"})
            regs = (d or {}).get("resultado") or []
            total = (d or {}).get("totalPaginas") or 0
            n += gravar(raw, "fornecedor", [_linha(x) for x in regs if x.get("cnpj") or x.get("cpf")])
            raw.commit()
            if pagina % 20 == 0:
                log.info(f"[fornecedores] página {pagina}/{total}, {n} gravados")
            pagina += 1
            time.sleep(0.2)
    finally:
        raw.close()
        eng.dispose()
    log.info(f"[fornecedores] fim: {n} gravados")
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", type=int, default=1)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    coletar(p.parse_args().desde)
