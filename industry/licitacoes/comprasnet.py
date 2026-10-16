"""Coletor ComprasNet / Compras.gov.br (SIASG) — Dados Abertos (requests).

Fluxo por órgão: consulta órgão/UASGs -> contratos + licitações legado + compras
sem licitação (dispensa/inexigibilidade). Os bancos-alvo são empresas públicas e
em geral NÃO constam no SISG (publicam via PNCP) — retorno pequeno/vazio é a
constatação factual, não falha. Recorte 2024-2026.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.licitacoes.net import ColetorBloqueado

B = "https://dadosabertos.compras.gov.br"
ANOS = sorted(ANOS_ALVO)

# /modulo-legado/1_consultarLicitacao (Lei 8.666) foi descontinuado pelo governo —
# retorna HTTP 400 "Erro ao efetuar a consulta" p/ QUALQUER parâmetro (confirmado
# ao vivo em 2026-07-31, inclusive com UASG e período que antes funcionavam).
# Substituído por /modulo-contratacoes/1_consultarContratacoes_PNCP_14133 (Lei
# 14.133, dados publicados no PNCP). codigoModalidade é obrigatório e sem opção
# "todas" — a API não documenta um enum, então varremos o domínio de modalidades
# do PNCP (1-14; códigos sem uso hoje só retornam totalRegistros=0, sem custo).
MODALIDADES_PNCP = list(range(1, 15))
BANCOS = {
    "Banco do Brasil": "00000000000191", "Caixa Econômica Federal": "00360305000104",
    "BNDES": "33657248000189", "Banco do Nordeste": "07237373000120",
    "Banco da Amazônia": "04902979000144", "Banco Central do Brasil": "00038166000105",
    "BRB - Banco de Brasília": "00000208000100", "BDMG": "38486817000194",
    "Banestes": "28127603000178", "Banrisul": "92702067000196",
}


# As licitações do ComprasNet são as MESMAS publicadas no PNCP e vêm com o
# `numeroControlePNCP` ("<cnpj>-1-<seq>/<ano>"), mas o coletor só o usava para
# montar o `id` e descartava o resto — as 717 linhas de edital ficavam sem link
# nenhum no banco, e sem link a linha é inútil na tela (não dá para abrir o
# edital). O portal do PNCP tem página por item, então dá para reconstruir.
# Verificado ao vivo: /app/editais/07237373000120/2026/82 devolve 200 e a API de
# consulta confirma o mesmo numeroControlePNCP.
_CONTROLE_PNCP = re.compile(r"^(\d{14})-\d+-(\d+)/(\d{4})$")


def _link_pncp(numero_controle: str) -> str:
    """URL do item no portal do PNCP a partir do número de controle.

    Vazio quando o número não bate o formato — contratos, por exemplo, não têm
    controle PNCP e continuam sem link (limitação da fonte, não do parser).
    """
    m = _CONTROLE_PNCP.match(str(numero_controle or "").strip())
    if not m:
        return ""
    cnpj, seq, ano = m.group(1), m.group(2).lstrip("0") or "0", m.group(3)
    return f"https://pncp.gov.br/app/editais/{cnpj}/{ano}/{seq}"


def _gj(s, path, params):
    """GET -> JSON. Distingue 'sem dados' (OK) de 'bloqueio/erro' (levanta).

    - HTTP 200 + JSON válido -> retorna (resultado vazio é legítimo p/ os bancos,
      que publicam via PNCP e quase não constam no SISG).
    - HTTP 404 -> None (endpoint sem dados p/ aquele órgão/uasg — não é erro).
    - HTTP 200 não-JSON (WAF), 4xx/5xx persistente, ou falha de rede persistente
      -> ColetorBloqueado, para a task falhar em vez de gravar CSV vazio "ok".
    """
    tentativas, ultimo = 4, None
    for i in range(tentativas):
        # Pacing fixo entre TODAS as chamadas (não só entre páginas de um mesmo
        # _paginar): a varredura por modalidade/ano/uasg dispara centenas de GETs
        # em sequência e sem isso a API rate-limita (HTTP 429) mesmo com poucos
        # resultados por chamada.
        time.sleep(0.4)
        try:
            r = s.get(B + path, params=params, timeout=50)
        except requests.RequestException as e:
            ultimo = e
            time.sleep(3 * (i + 1))
            continue
        if r.status_code == 200:
            txt = (r.text or "").lstrip()
            if txt[:1] in "{[":
                try:
                    return r.json()
                except ValueError:
                    ultimo = f"JSON inválido: {txt[:80]!r}"
                    time.sleep(3 * (i + 1))
                    continue
            # HTTP 200 com corpo não-JSON = WAF/bloqueio — re-tenta (transitório) e
            # só falha ao esgotar; não é 'vazio legítimo'.
            ultimo = f"WAF/200-não-JSON: {txt[:80]!r}"
            time.sleep(3 * (i + 1))
            continue
        if r.status_code == 404:
            return None  # sem dados p/ esse órgão/uasg — não é falha
        if r.status_code == 429:
            # Rate-limit: respeita Retry-After se vier, senão backoff bem mais
            # longo que o dos outros erros (429 é esperado se pacing acima não
            # bastar, não é falha real que deva estourar rápido).
            espera = int(r.headers.get("Retry-After", 0)) or 15 * (i + 1)
            ultimo = f"HTTP 429 (aguardando {espera}s)"
            time.sleep(espera)
            continue
        ultimo = f"HTTP {r.status_code}"
        time.sleep(3 * (i + 1))
    raise ColetorBloqueado(f"ComprasNet {path}: falhou após {tentativas} tentativas ({ultimo})")


def _orgao_uasgs(s, cnpj):
    org = _gj(s, "/modulo-uasg/2_consultarOrgao",
             {"cnpjCpfOrgao": cnpj, "statusOrgao": "true", "pagina": 1, "tamanhoPagina": 50})
    cods = [r.get("codigoOrgao") for r in (org.get("resultado") if org else []) or []]
    ua = _gj(s, "/modulo-uasg/1_consultarUasg",
             {"cnpjCpfOrgao": cnpj, "statusUasg": "true", "pagina": 1, "tamanhoPagina": 200})
    uasgs = [r.get("codigoUasg") for r in (ua.get("resultado") if ua else []) or []]
    return [c for c in cods if c], [u for u in uasgs if u]


def _paginar(s, path, params, max_pag=200):
    out, pag = [], 1
    while pag <= max_pag:
        d = _gj(s, path, {**params, "pagina": pag, "tamanhoPagina": 500})
        res = (d.get("resultado") if d else None) or []
        if not res:
            break
        out.extend(res)
        if len(res) < 500:
            break
        pag += 1
        time.sleep(0.2)
    return out


def collect_comprasnet(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    rows = []
    for banco, cnpj in BANCOS.items():
        cods, uasgs = _orgao_uasgs(s, cnpj)
        if not cods and not uasgs:
            continue
        for co in cods:
            for ano in ANOS:
                for c in _paginar(s, "/modulo-contratos/1_consultarContratos",
                                  {"codigoOrgao": co, "dataVigenciaInicialMin": f"{ano}-01-01",
                                   "dataVigenciaInicialMax": f"{ano}-12-31"}):
                    rows.append({
                        "id": f"comprasnet-contrato-{co}-{c.get('numeroContrato','')}",
                        "fonte": "ComprasNet", "banco": banco, "tipo_documento": "contrato",
                        "tipo": c.get("nomeTipo", ""), "modalidade": c.get("nomeModalidadeCompra", ""),
                        "titulo": (c.get("objeto") or "")[:140], "objeto": c.get("objeto", ""),
                        # contrato tem data no retorno (o filtro já usa dataVigenciaInicial);
                        # antes gravava "" fixo. Cadeia dos nomes prováveis (dados abertos
                        # Compras.gov.br). NÃO verificado com resposta ao vivo — conferir no log.
                        "data": (c.get("dataVigenciaInicio") or c.get("dataVigenciaInicial")
                                 or c.get("dataAssinatura") or c.get("dataPublicacaoDOU") or ""),
                        "data_inicio_vigencia": (c.get("dataVigenciaInicio")
                                                 or c.get("dataVigenciaInicial") or ""),
                        "ano": ano, "numero": c.get("numeroContrato", ""),
                        "orgao": c.get("nomeOrgao", str(co)), "unidade": c.get("nomeUnidadeGestora", ""),
                        "contratado_cnpj": c.get("niFornecedor", ""),
                        "contratado": c.get("nomeRazaoSocialFornecedor", ""),
                        "processo": c.get("processo", ""),
                    })
        # Licitações via PNCP (Lei 14.133) — filtra por CNPJ do órgão, não por UASG
        # (o endpoint novo não expõe filtro por UASG).
        for ano in ANOS:
            for modalidade in MODALIDADES_PNCP:
                for lic in _paginar(s, "/modulo-contratacoes/1_consultarContratacoes_PNCP_14133",
                                    {"orgaoEntidadeCnpj": cnpj,
                                     "dataPublicacaoPncpInicial": f"{ano}-01-01",
                                     "dataPublicacaoPncpFinal": f"{ano}-12-31",
                                     "codigoModalidade": modalidade}):
                    rows.append({
                        "id": f"comprasnet-lic-{lic.get('numeroControlePNCP','')}",
                        "numero_controle_pncp": lic.get("numeroControlePNCP", ""),
                        "link": _link_pncp(lic.get("numeroControlePNCP", "")),
                        "fonte": "ComprasNet", "banco": banco, "tipo_documento": "edital",
                        "tipo": lic.get("modalidadeNome", ""), "modalidade": lic.get("modalidadeNome", ""),
                        "titulo": (lic.get("objetoCompra") or "")[:120],
                        "objeto": lic.get("objetoCompra", ""),
                        "data": lic.get("dataPublicacaoPncp", ""), "numero": lic.get("numeroCompra", ""),
                        "valor": lic.get("valorTotalEstimado", ""),
                        "orgao": lic.get("orgaoEntidadeRazaoSocial", banco),
                    })
        for ua in uasgs:
            for ano in ANOS:
                for cs in _paginar(s, "/modulo-legado/5_consultarComprasSemLicitacao",
                                   {"co_uasg": ua, "dt_ano_aviso": ano}):
                    rows.append({
                        "id": f"comprasnet-dispensa-{ua}-{cs.get('id_compra','')}",
                        "fonte": "ComprasNet", "banco": banco,
                        "tipo_documento": "dispensa_inexigibilidade",
                        "tipo": cs.get("co_modalidade_licitacao", ""),
                        "modalidade": cs.get("co_modalidade_licitacao", ""),
                        "titulo": (cs.get("ds_objeto_licitacao") or "")[:120],
                        "objeto": cs.get("ds_objeto_licitacao", ""),
                        "data": cs.get("dt_publicacao") or cs.get("dt_ratificacao") or cs.get("dt_alteracao", ""),
                        "valor": cs.get("vr_estimado", ""),
                        "numero": cs.get("nu_aviso_licitacao", ""), "orgao": f"uasg={ua}",
                    })
    df = pd.DataFrame(rows)
    out = dest_dir / "comprasnet_contratacoes.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"ComprasNet: {len(df)} registros -> {out}")
    return out


if __name__ == "__main__":
    print(collect_comprasnet())
