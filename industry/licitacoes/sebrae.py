"""Coletor Sebrae — Canal do Fornecedor (SCF).

O Sistema Sebrae (nacional e os 27 estaduais) licita pelo regulamento próprio
(Resolução CDN 439/2023), não pela Lei 14.133, e não publica no PNCP. Todos os
processos ficam no SCF; o formulário de filtro da página pública devolve, num
POST só, tudo o que está "Em Andamento" (~325 processos em 17/10/2026), com
modalidade, datas, critério de julgamento e onde a disputa acontece.

Ficam só os que casam com os temas (``termos.py``). Há modalidades fora da Lei
14.133 que interessam: "Desafio da Inovação" (contratação de startups) e
"Credenciamento" (cadastro de consultores e empresas).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import DEST_DIR, REQUEST_HEADERS, SEBRAE_SCF_URL
from industry.licitacoes.net import ColetorBloqueado, json_or_raise
from industry.licitacoes.termos import termos_encontrados

FONTE = "Sebrae - Canal do Fornecedor"
FILTRO_URL = SEBRAE_SCF_URL + "/Filter"
DETALHE_URL = SEBRAE_SCF_URL + "/Detalhe?id={id}"

UF_POR_NOME = {
    "ACRE": "AC", "ALAGOAS": "AL", "AMAPA": "AP", "AMAZONAS": "AM", "BAHIA": "BA",
    "CEARA": "CE", "DISTRITO FEDERAL": "DF", "ESPIRITO SANTO": "ES", "GOIAS": "GO",
    "MARANHAO": "MA", "MATO GROSSO DO SUL": "MS", "MATO GROSSO": "MT",
    "MINAS GERAIS": "MG", "PARAIBA": "PB", "PARANA": "PR", "PARA": "PA",
    "PERNAMBUCO": "PE", "PIAUI": "PI", "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN", "RIO GRANDE DO SUL": "RS", "RONDONIA": "RO",
    "RORAIMA": "RR", "SANTA CATARINA": "SC", "SAO PAULO": "SP", "SERGIPE": "SE",
    "TOCANTINS": "TO",
}


BRASILIA = timezone(timedelta(hours=-3))     # sem horário de verão desde 2019


def _data(v) -> str:
    """'/Date(1794193200000)/' (epoch em ms, UTC) -> '2026-11-09T00:00:00' (Brasília)."""
    m = re.search(r"\((-?\d+)", str(v or ""))
    if not m:
        return ""
    return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=BRASILIA).strftime(
        "%Y-%m-%dT%H:%M:%S")


def _uf(unidade: str) -> str:
    from industry.licitacoes.termos import normalizar
    nome = normalizar(unidade).upper().replace("SEBRAE", "").strip()
    return UF_POR_NOME.get(nome, "")


def _nome(d) -> str:
    v = (d or {}).get("Descricao") if isinstance(d, dict) else ""
    return "" if not v or v == "Nenhum" else str(v).strip()


def _linha(it: dict, termos: list[str]) -> dict:
    unidade = _nome(it.get("ZSebrae"))
    criterio = " / ".join(x for x in (it.get("TipoJulgamentoDescricao"),
                                      it.get("TipoMenorPrecoDescricao"))
                          if x and x != "Nenhum")
    # No pregão a proposta vai até a abertura da sessão; o portal só preenche
    # DataEncerramento em alguns casos (e o fim do período no credenciamento).
    prazo = (_data(it.get("DataEncerramento")) or _data(it.get("DataCredenciamentoFim"))
             or _data(it.get("DataAbertura")))
    return {
        "id": str(it.get("Id", "")),
        "fonte": FONTE,
        "tipo_documento": "edital",
        "tipo": _nome(it.get("LicitacaoModalidade")),
        "numero": it.get("Numero", ""),
        "processo": it.get("NumeroProcesso", ""),
        "objeto": re.sub(r"\s+", " ", it.get("Objeto") or "").strip(),
        "situacao": it.get("StatusDescricao", ""),
        "data_publicacao": _data(it.get("DataPublicacao")),
        "data_abertura": _data(it.get("DataAbertura")),
        "data_encerramento_proposta": prazo,
        "criterio_julgamento": criterio,
        "observacao": (it.get("Local") or "").strip(),
        "orgao": unidade or "SEBRAE",
        "uf": _uf(unidade),
        "link": DETALHE_URL.format(id=it.get("Id", "")),
        "filtros_usados": "; ".join(termos),
    }


def collect_sebrae(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    try:
        pagina = s.get(SEBRAE_SCF_URL, timeout=60)
        pagina.raise_for_status()
        form = BeautifulSoup(pagina.text, "html.parser").find("form")
        if form is None:
            raise ColetorBloqueado("Sebrae SCF: formulário de filtro não encontrado")
        # o formulário já vem com StatusId=5 ("Em Andamento") e o token antiforgery
        dados = {i.get("name"): i.get("value") or ""
                 for i in form.find_all(["input", "select"]) if i.get("name")}
        r = s.post(FILTRO_URL, data=dados, timeout=180,
                   headers={"X-Requested-With": "XMLHttpRequest"})
    except requests.RequestException as e:
        raise ColetorBloqueado(f"Sebrae SCF: {type(e).__name__}: {e}") from e
    resp = json_or_raise(r, "Sebrae SCF")
    if not resp.get("success"):
        raise ColetorBloqueado(f"Sebrae SCF: resposta sem sucesso: {str(resp)[:200]}")
    itens = resp.get("aData") or []

    rows = []
    for it in itens:
        termos = termos_encontrados(it.get("Objeto") or "")
        if termos:
            rows.append(_linha(it, termos))
    df = pd.DataFrame(rows)
    out = dest_dir / "sebrae.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Sebrae SCF: {len(itens)} em andamento; {len(df)} casam com os temas -> {out}")
    return out


if __name__ == "__main__":
    print(collect_sebrae())
