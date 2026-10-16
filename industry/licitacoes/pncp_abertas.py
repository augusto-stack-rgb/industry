"""Coletor PNCP — todas as contratações de qualquer órgão, filtradas por tema.

A busca do coletor ``pncp.py`` parte dos CNPJs dos bancos (herdado da origem) e
de alguns termos. Esta lê as contratações de QUALQUER órgão federal, estadual ou
municipal e guarda só o que casa com os temas de ML/IA/dados ou de TI
(``termos.py``). É a fonte de maior cobertura do pipeline. Dois modos:

- diário: ``/contratacoes/publicacao`` dos últimos ``PNCP_PUBLICACAO_DIAS`` dias,
  por modalidade (~14 min, limitado pelo rate limit da API — ver config);
- completo: ``/contratacoes/proposta``, TODAS com proposta aberta (~80 min, a API
  é lenta nesse endpoint). Só manual (``completo=True``).

Na primeira execução (sem manifesto anterior) o modo diário cobre
``PNCP_PRIMEIRA_COLETA_DIAS`` dias em vez de ``PNCP_PUBLICACAO_DIAS``.

Mesma ``fonte`` ("PNCP") e mesmo id (número de controle) do ``pncp.py``: o item
que vier pelas duas portas vira uma linha só no bronze.

A data de encerramento da proposta já vem na listagem e vai para o bronze
(``data_encerramento_proposta``); o filtro "acionável" do gold a usa sem
consultar a API de detalhe item a item.
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import (
    DEST_DIR, PNCP_ABERTAS_DIAS_A_FRENTE, PNCP_ABERTAS_MAX_PAGINAS, PNCP_ABERTAS_PAUSA_S,
    PNCP_ESPERA_429_S, PNCP_MODALIDADES, PNCP_PRIMEIRA_COLETA_DIAS, PNCP_PUBLICACAO_DIAS,
)
from industry.licitacoes.net import ColetorBloqueado
from industry.licitacoes.termos import termos_encontrados

API = "https://pncp.gov.br/api/consulta/v1/contratacoes/proposta"
API_PUBLICACAO = "https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao"
TAM = 50
HEADERS = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
TENTATIVAS = 6


def _get(s: requests.Session, url: str, params: dict) -> dict | None:
    """Uma página; ``None`` = fim (204). Repete com espera crescente em 429/5xx/rede
    (a API devolve 429 em HTML depois de rajadas, sem dizer o limite)."""
    espera = PNCP_ESPERA_429_S
    for _ in range(TENTATIVAS):
        try:
            r = s.get(url, params=params, timeout=60)
            if r.status_code == 204:
                return None
            if r.status_code == 200 and r.text.strip()[:1] == "{":
                return r.json()
            motivo = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            motivo = type(e).__name__
        time.sleep(espera)
        espera = min(espera * 2, 240)
    raise ColetorBloqueado(f"PNCP {url.rsplit('/', 1)[-1]} {params}: falhou ({motivo})")


def _linha(it: dict, termos: list[str]) -> dict:
    org = it.get("orgaoEntidade") or {}
    uni = it.get("unidadeOrgao") or {}
    cnpj, ano, seq = org.get("cnpj", ""), it.get("anoCompra", ""), it.get("sequencialCompra", "")
    link = (f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"
            if cnpj and ano and seq else it.get("linkSistemaOrigem") or "")
    objeto = it.get("objetoCompra") or ""
    compl = (it.get("informacaoComplementar") or "").strip()
    return {
        "fonte": "PNCP",
        "tipo_documento": "edital",
        "numero_controle_pncp": it.get("numeroControlePNCP", ""),
        "numero": it.get("numeroCompra", ""),
        "processo": it.get("processo", ""),
        "tipo": it.get("modalidadeNome", ""),
        "objeto": objeto if not compl else f"{objeto} — {compl[:400]}",
        "link": link,
        "data": (it.get("dataPublicacaoPncp") or "")[:19],
        "ano": str(ano),
        "valor": it.get("valorTotalEstimado", ""),
        "orgao": org.get("razaoSocial", ""),
        "orgao_cnpj": cnpj,
        "unidade": uni.get("nomeUnidade", ""),
        "uf": uni.get("ufSigla", ""),
        "municipio": uni.get("municipioNome", ""),
        "situacao": it.get("situacaoCompraNome", ""),
        "data_abertura": (it.get("dataAberturaProposta") or "")[:19],
        "data_encerramento_proposta": (it.get("dataEncerramentoProposta") or "")[:19],
        "modo_disputa": it.get("modoDisputaNome", ""),
        "filtros_usados": "; ".join(termos),
    }


def _filtrar(itens: list[dict], rows: dict) -> int:
    """Acrescenta em ``rows`` (por número de controle) os itens que casam com os temas."""
    for it in itens:
        termos = termos_encontrados(f"{it.get('objetoCompra', '')} "
                                    f"{it.get('informacaoComplementar', '')}")
        if termos:
            ln = _linha(it, termos)
            rows[ln["numero_controle_pncp"] or ln["link"]] = ln
    return len(itens)


def _paginar(s, url: str, params: dict, rows: dict) -> int:
    vistos, pagina, total = 0, 1, None
    while pagina <= PNCP_ABERTAS_MAX_PAGINAS:
        d = _get(s, url, {**params, "pagina": pagina, "tamanhoPagina": TAM})
        if not d or not d.get("data"):
            break
        vistos += _filtrar(d["data"], rows)
        total = d.get("totalPaginas", total)
        if total and pagina >= total:
            break
        pagina += 1
        time.sleep(PNCP_ABERTAS_PAUSA_S)
    return vistos


def _primeira_coleta() -> bool:
    """Sem manifesto anterior deste coletor = nunca rodou (ver assets.py)."""
    try:
        from industry.shared import storage
        return not storage.path_of("licitacoes/coletas/pncp_abertas.json").exists()
    except Exception:  # noqa: BLE001
        return False


def collect_pncp_abertas(dest_dir: Path = DEST_DIR, completo: bool = False) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(HEADERS)
    hoje = date.today()
    dias = PNCP_PRIMEIRA_COLETA_DIAS if _primeira_coleta() else PNCP_PUBLICACAO_DIAS

    rows: dict[str, dict] = {}
    if completo is True:
        data_final = (hoje + timedelta(days=PNCP_ABERTAS_DIAS_A_FRENTE)).strftime("%Y%m%d")
        vistos = _paginar(s, API, {"dataFinal": data_final}, rows)
        modo = "completo (todas com proposta aberta)"
    else:
        ini = (hoje - timedelta(days=dias - 1)).strftime("%Y%m%d")
        fim = hoje.strftime("%Y%m%d")
        vistos = sum(_paginar(s, API_PUBLICACAO, {"dataInicial": ini, "dataFinal": fim,
                                                  "codigoModalidadeContratacao": m}, rows)
                     for m in PNCP_MODALIDADES)
        modo = f"diário (publicadas de {ini} a {fim})"

    df = pd.DataFrame(list(rows.values()))
    out = dest_dir / "pncp_contratacoes_tema.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"PNCP {modo}: {vistos} contratações lidas; {len(df)} casam com os temas -> {out}")
    return out


if __name__ == "__main__":
    print(collect_pncp_abertas())
