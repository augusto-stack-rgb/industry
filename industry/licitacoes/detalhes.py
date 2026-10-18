"""Campos de detalhe das licitações pontuadas (``licitacoes.detalhes``).

O bronze guarda o que cada listagem traz, e as listagens trazem pouco: o PNCP
não diz na busca o critério de julgamento, se é exclusiva para ME/EPP, se é
registro de preços nem quais são os anexos. Esta etapa completa os itens do
``gold_filtros`` (os que aparecem no painel), do mais relevante para o menos:

1. Base do bronze, para qualquer fonte: modalidade normalizada, situação, datas,
   critério, modo de disputa, valor, links de anexo que o coletor já trouxe.
2. PNCP (quem tem número de controle): detalhe da contratação, itens e
   arquivos. Dão amparo legal, instrumento, SRP, modo de disputa, critério de
   julgamento e benefício ME/EPP (por item), orçamento sigiloso, valor
   estimado, link do sistema de origem e a lista de anexos com URL.
3. Valor no edital: sem valor depois de 1 e 2, baixa o PDF do edital ou do
   termo de referência e procura "valor estimado/global/máximo ... R$". Também
   marca exclusividade ME/EPP quando o PNCP não disse.

Idempotente e com teto: só processa quem ainda não tem linha, até
``DETALHES_LIMITE`` itens ou ``DETALHES_MAX_SEGUNDOS`` por execução.
"""
from __future__ import annotations

import json
import logging
import re
import time

import pandas as pd
import requests

from industry.licitacoes import db
from industry.licitacoes import pncp as pncp_mod
from industry.licitacoes.config import (
    DETALHES_LIMITE, DETALHES_MAX_PDF_MB, DETALHES_MAX_SEGUNDOS, DETALHES_PAGINAS_PDF,
    DETALHES_VALOR_LIMITE, PNCP_ABERTAS_PAUSA_S, REQUEST_HEADERS, SCHEMA, TB_BRONZE,
    TB_DETALHES, TB_GOLD_FILTROS,
)

logger = logging.getLogger(__name__)

API = "https://pncp.gov.br/api"
COLUNAS = [
    "id_unificado", "modalidade", "amparo_legal", "instrumento", "srp", "modo_disputa",
    "criterio_julgamento", "beneficio_me_epp", "orcamento_sigiloso", "valor_estimado",
    "valor_origem", "situacao", "data_abertura_proposta", "data_encerramento_proposta",
    "link_sistema_origem", "link_edital", "anexos", "qtd_itens", "origem",
]
_NAO_SE_APLICA = {"", "não se aplica", "nao se aplica", "nenhum", "none"}


# ── helpers ──────────────────────────────────────────────────────────────────
def _txt(v) -> str:
    s = db._safe(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def _ts(v) -> str | None:
    """Data/hora em qualquer formato das fontes -> 'YYYY-MM-DDTHH:MM:SS' ou None."""
    s = _txt(v)
    d = db.to_date(s)
    if not d:
        return None
    h = re.search(r"[T\s](\d{2}):(\d{2})(?::(\d{2}))?", s)
    return f"{d}T{h.group(1)}:{h.group(2)}:{h.group(3) or '00'}" if h else f"{d}T00:00:00"


def _juntar(valores) -> str:
    """Valores distintos, sem 'Não se aplica' quando há outro valor."""
    vs = list(dict.fromkeys(_txt(v) for v in valores if _txt(v)))
    uteis = [v for v in vs if v.lower() not in _NAO_SE_APLICA]
    return "; ".join(uteis or vs[:1])


def _anexos_do_bronze(r: dict) -> list[dict]:
    out = []
    u = _txt(r.get("edital_url"))
    if u.lower().startswith("http"):
        out.append({"titulo": _txt(r.get("edital_nome")) or "Edital", "tipo": "Edital", "url": u})
    for campo in ("documentos", "arquivos_baixados"):
        raw = _txt(r.get(campo))
        if not raw:
            continue
        itens = []
        if raw.startswith("["):
            try:
                itens = json.loads(raw)
            except ValueError:
                itens = []
        else:
            itens = [p.strip() for p in re.split(r"[;\n]+", raw)]
        for d in itens:
            url = d.get("url") if isinstance(d, dict) else d
            if str(url or "").lower().startswith("http"):
                titulo = (d.get("titulo") or d.get("nome") or "") if isinstance(d, dict) else ""
                out.append({"titulo": titulo or str(url).rsplit("/", 1)[-1][:80],
                            "tipo": "", "url": str(url)})
    vistos, unicos = set(), []
    for a in out:
        if a["url"] not in vistos:
            vistos.add(a["url"])
            unicos.append(a)
    return unicos


def _link_edital(anexos: list[dict]) -> str:
    for chave in ("edital", "termo de refer", "aviso"):
        for a in anexos:
            if chave in f"{a.get('tipo', '')} {a.get('titulo', '')}".lower():
                return a["url"]
    return anexos[0]["url"] if anexos else ""


# ── 1. base do bronze ─────────────────────────────────────────────────────────
def _base(r: dict) -> dict:
    anexos = _anexos_do_bronze(r)
    valor = db.to_numeric(r.get("valor")) or db.to_numeric(r.get("valor_estimado"))
    return {
        "id_unificado": r["id_unificado"],
        "modalidade": db.normalizar_modalidade(r.get("tipo")),
        "modo_disputa": _txt(r.get("modo_disputa")) or None,
        "criterio_julgamento": (_txt(r.get("criterio_julgamento"))
                                or _txt(r.get("tipo_julgamento")) or None),
        "valor_estimado": valor,
        "valor_origem": "listagem" if valor else None,
        "situacao": _txt(r.get("situacao")) or None,
        "data_abertura_proposta": _ts(r.get("data_abertura")),
        "data_encerramento_proposta": _ts(r.get("data_encerramento_proposta")),
        "link_edital": _link_edital(anexos) or None,
        "anexos": anexos,
        "origem": "bronze",
    }


# ── 2. PNCP ───────────────────────────────────────────────────────────────────
def _get(s: requests.Session, url: str, params: dict | None = None):
    """JSON ou None (404/204). Repete em 429/5xx/rede, como o pncp_abertas."""
    espera = 10.0
    for _ in range(4):
        try:
            r = s.get(url, params=params, timeout=40)
            if r.status_code in (204, 404):
                return None
            if r.status_code == 200 and r.text.strip()[:1] in "{[":
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(espera)
        espera *= 2
    raise RuntimeError(f"PNCP falhou: {url}")


def _pncp(s: requests.Session, cnpj: str, ano: str, seq: str) -> dict:
    base = f"{API}/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"
    d = _get(s, base) or {}
    time.sleep(PNCP_ABERTAS_PAUSA_S)
    itens = _get(s, f"{API}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens",
                 {"pagina": 1, "tamanhoPagina": 100}) or []
    time.sleep(PNCP_ABERTAS_PAUSA_S)
    arqs = _get(s, f"{API}/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/arquivos") or []
    time.sleep(PNCP_ABERTAS_PAUSA_S)

    anexos = [{"titulo": a.get("titulo", ""), "tipo": a.get("tipoDocumentoNome", ""),
               "url": a.get("url") or a.get("uri", "")}
              for a in arqs if a.get("statusAtivo", True) and (a.get("url") or a.get("uri"))]
    # 0 no PNCP = "não informado" (dispensa e inexigibilidade costumam vir assim)
    valor = float(d.get("valorTotalEstimado") or 0) or sum(
        float(i.get("valorTotal") or 0) for i in itens)
    valor = valor if valor >= 1 else None
    sigiloso = d.get("orcamentoSigilosoCodigo")
    out = {
        "modalidade": db.normalizar_modalidade(d.get("modalidadeNome")) if d else None,
        "amparo_legal": (d.get("amparoLegal") or {}).get("nome"),
        "instrumento": d.get("tipoInstrumentoConvocatorioNome"),
        "srp": d.get("srp"),
        "modo_disputa": d.get("modoDisputaNome"),
        "criterio_julgamento": _juntar(i.get("criterioJulgamentoNome") for i in itens) or None,
        "beneficio_me_epp": _juntar(i.get("tipoBeneficioNome") for i in itens) or None,
        "orcamento_sigiloso": (sigiloso not in (None, 1)) if sigiloso is not None else None,
        "valor_estimado": float(valor) if valor else None,
        "valor_origem": "pncp" if valor else None,
        "situacao": d.get("situacaoCompraNome"),
        "data_abertura_proposta": _ts(d.get("dataAberturaProposta")),
        "data_encerramento_proposta": _ts(d.get("dataEncerramentoProposta")),
        "link_sistema_origem": d.get("linkSistemaOrigem") or d.get("linkProcessoEletronico"),
        "link_edital": _link_edital(anexos) or None,
        "anexos": anexos,
        "qtd_itens": len(itens) or None,
        "origem": "pncp",
    }
    return {k: v for k, v in out.items() if v not in (None, "", [])}


# ── 3. valor e ME/EPP no texto do edital ──────────────────────────────────────
_RX_VALOR = re.compile(
    r"valor\s+(?:total\s+|global\s+|m[aá]ximo\s+|anual\s+)?(?:estimado|global|m[aá]ximo|"
    r"de\s+refer[eê]ncia|total\s+estimado|aceit[aá]vel)[\s\S]{0,120}?R\$\s*"
    r"((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})", re.I)
_RX_ME_EPP = re.compile(
    r"exclusiv\w*\s+(?:para|[àa]s?)\s+(?:a\s+participa[çc][ãa]o\s+de\s+)?"
    r"(?:microempresas?|ME\b|ME/EPP|MEs?\s+e\s+EPPs?)", re.I)


def _texto_pdf(s: requests.Session, url: str) -> str:
    try:
        r = s.get(url, timeout=60, stream=True)
        r.raise_for_status()
        limite = DETALHES_MAX_PDF_MB * 1024 * 1024
        dados = b""
        for parte in r.iter_content(256 * 1024):
            dados += parte
            if len(dados) > limite:
                return ""
        if dados[:5] != b"%PDF-":
            return ""
        import pymupdf
        with pymupdf.open(stream=dados, filetype="pdf") as doc:
            return "\n".join(doc[i].get_text() for i in range(min(len(doc),
                                                                  DETALHES_PAGINAS_PDF)))
    except Exception as e:  # noqa: BLE001 — PDF quebrado ou fora do ar: segue sem valor
        logger.info(f"[detalhes] PDF ilegível {url[:90]}: {type(e).__name__}")
        return ""


def _do_edital(s: requests.Session, det: dict) -> dict:
    url = det.get("link_edital")
    if not url:
        return {}
    texto = _texto_pdf(s, url)
    if not texto:
        return {}
    out = {}
    for m in _RX_VALOR.finditer(texto):
        v = db.to_numeric(m.group(1))
        if v and 1_000 <= v <= 5_000_000_000:
            out.update(valor_estimado=v, valor_origem="edital")
            break
    if not det.get("beneficio_me_epp") and _RX_ME_EPP.search(texto):
        out["beneficio_me_epp"] = "Exclusiva ME/EPP (lido no edital)"
    return out


# ── banco ────────────────────────────────────────────────────────────────────
def _pendentes(limite: int) -> pd.DataFrame:
    with db.get_engine().connect() as conn:
        return pd.read_sql(
            f"SELECT b.* FROM {SCHEMA}.{TB_GOLD_FILTROS} gf "
            f"JOIN {SCHEMA}.{TB_BRONZE} b ON b.id_unificado = gf.id_unificado "
            f"LEFT JOIN {SCHEMA}.{TB_DETALHES} d ON d.id_unificado = gf.id_unificado "
            f"WHERE d.id_unificado IS NULL "
            f"ORDER BY gf.score_combinado DESC NULLS LAST LIMIT {int(limite)}", conn)


def _gravar(linhas: list[dict]) -> int:
    if not linhas:
        return 0
    from sqlalchemy import text
    cols = COLUNAS
    sql = text(
        f"INSERT INTO {SCHEMA}.{TB_DETALHES} ({', '.join(cols)}, atualizado_em) "
        f"VALUES ({', '.join(':' + c for c in cols)}, now()) "
        f"ON CONFLICT (id_unificado) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "id_unificado")
        + ", atualizado_em = now()")
    params = []
    for ln in linhas:
        p = {c: ln.get(c) for c in cols}
        p["anexos"] = json.dumps(ln.get("anexos") or [], ensure_ascii=False)
        params.append(p)
    with db.get_engine().begin() as conn:
        conn.execute(sql, params)
    return len(params)


def coletar_detalhes(limite: int = DETALHES_LIMITE,
                     limite_valor: int = DETALHES_VALOR_LIMITE) -> dict:
    pend = _pendentes(limite).fillna("")
    if pend.empty:
        return {"pendentes": 0, "gravados": 0}
    s = requests.Session()
    s.headers.update({**REQUEST_HEADERS, "Accept": "application/json"})
    inicio = time.monotonic()
    linhas, n_pncp, n_edital, falhas_pncp, gravados = [], 0, 0, 0, 0
    parou = False
    for _, row in pend.iterrows():
        if time.monotonic() - inicio > DETALHES_MAX_SEGUNDOS:
            parou = True
            break
        r = row.to_dict()
        det = _base(r)
        ident = (pncp_mod.identificadores_para_prazo(r)
                 if _txt(r.get("numero_controle_pncp")) else None)
        if ident:
            try:
                extra = _pncp(s, *ident)
                det.update(extra)
                n_pncp += 1
            except RuntimeError as e:
                falhas_pncp += 1
                logger.warning(f"[detalhes] {r['id_unificado']}: {e}")
                continue      # sem linha: tenta de novo na próxima execução
        if not det.get("valor_estimado") and n_edital < limite_valor:
            extra = _do_edital(s, det)
            if extra:
                det.update(extra)
            n_edital += 1
        linhas.append(det)
        if len(linhas) >= 20:
            gravados += _gravar(linhas)
            linhas = []
    gravados += _gravar(linhas)
    res = {"pendentes": len(pend), "gravados": gravados, "pncp": n_pncp,
           "falhas_pncp": falhas_pncp, "editais_lidos": n_edital, "parou_por_tempo": parou}
    logger.info(f"[detalhes] {res}")
    return res
