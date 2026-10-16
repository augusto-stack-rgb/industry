"""Coletor PNCP — Portal Nacional de Contratações Públicas (agregador federal).

Duas buscas na API pública (``/api/search``):
  1) órgãos-alvo por CNPJ (q=<CNPJ>): editais, contratos e atas (herdado da origem);
  2) por TERMO (``config.BUSCA_TERMOS_PNCP``): só editais recebendo proposta, de
     qualquer órgão — é onde aparecem as contratações de ML/IA/ciência de dados. Recorte "só novos": ordena por
-data e para quando a página inteira é anterior a min(ANOS_ALVO); a dedup por
row_hash no bronze garante que só entram registros inéditos.

FIX de link: o `item_url` da API vem como /compras|/contratos|/atas, mas a rota
do portal é /app/editais|/app/contratos|/app/atas — sem isso os links dão 404.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import (
    DEST_DIR, ANOS_ALVO, BUSCA_TERMOS_PNCP, MAX_PAGINAS_TERMO, RETRO_DIAS,
)

API = "https://pncp.gov.br/api/search/"
HEADERS = {"Accept": "application/json, text/plain, */*",
           "Referer": "https://pncp.gov.br/app/editais", "Origin": "https://pncp.gov.br"}
TIPOS = ["edital", "contrato", "ata"]
TAM = 50
MAX_PAGINAS = 60   # recorte "novos": páginas recentes por banco/tipo
_ANO_MIN = min(int(a) for a in ANOS_ALVO)
_LIMITE_RETRO = (date.today() - timedelta(days=RETRO_DIAS)).isoformat()

BANCOS = {
    "Banco do Brasil": "00000000000191",
    "Caixa Econômica Federal": "00360305000104",
    "BNDES": "33657248000189",
    "Banco do Nordeste": "07237373000120",
    "Banco da Amazônia": "04902979000144",
    "Banco Central do Brasil": "00038166000105",
    "BRB - Banco de Brasília": "00000208000100",
    "BDMG": "38486817000194",
    "Banestes": "28127603000178",
    "Banrisul": "92702067000196",
}
# item_url (/compras|/contratos|/atas) -> rota real do portal (/app/...)
_ROTA = {"compras": "editais", "contratos": "contratos", "atas": "atas"}
_DATA_CAMPOS = ["data_publicacao_pncp", "data_assinatura", "data_inicio_vigencia",
                "data_atualizacao_pncp", "createdAt"]


def _ano(it: dict) -> str:
    for f in _DATA_CAMPOS:
        m = re.match(r"(20\d{2})", str(it.get(f, "")))
        if m:
            return m.group(1)
    return ""


def _data(it: dict) -> str:
    for f in _DATA_CAMPOS:
        v = str(it.get(f, ""))
        if re.match(r"20\d{2}", v):
            return v[:19]
    return ""


def _link_app(item_url: str) -> str:
    """Normaliza p/ a rota real do portal (/app/editais|contratos|atas).

    Aceita relativo (`/compras/…`, como a API devolve hoje) E absoluto
    (`https://pncp.gov.br/compras/…`, formato antigo do backfill) — sem isso o
    link antigo, sem `/app/`, abre página vazia no SPA. Já-corretos passam ilesos.
    """
    if not item_url:
        return item_url
    seg = re.sub(r"^https?://pncp\.gov\.br", "", item_url)  # tira host se absoluto
    if not seg.startswith("/"):
        return item_url
    if seg.startswith("/app/"):
        return "https://pncp.gov.br" + seg  # já está no formato certo
    seg = re.sub(r"^/(compras|contratos|atas)/",
                 lambda m: f"/{_ROTA[m.group(1)]}/", seg)
    return "https://pncp.gov.br/app" + seg


def _busca(s, q, tipo, pagina, status="todos"):
    params = {"tipos_documento": tipo, "ordenacao": "-data", "pagina": pagina,
              "tam_pagina": TAM, "status": status, "q": q}
    for _ in range(4):
        try:
            r = s.get(API, params=params, timeout=50)
            if r.status_code == 200 and r.text.strip()[:1] == "{":
                return r.json()
        except Exception:
            pass
        time.sleep(2)
    return None


CONSULTA = "https://pncp.gov.br/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}"


def buscar_prazo_proposta(cnpj: str, ano, seq, s: requests.Session | None = None
                          ) -> tuple[bool, dict | None]:
    """Consulta o detalhe do edital no PNCP e retorna (ok, info).

    - (True, {abertura, encerramento, situacao})  → detalhe lido (encerramento pode
      ser None em inexigibilidade/dispensa, que não têm janela de proposta).
    - (True, None)  → 404 (sem detalhe de proposta).
    - (False, None) → a API falhou (rate-limit/reset) → o chamador cai no fallback de
      recência, nunca descarta por erro.

    A API de detalhe do PNCP é instável (reseta conexão), por isso retry + pausa.
    """
    if not (cnpj and ano and seq):
        return (False, None)
    s = s or requests.Session()
    url = CONSULTA.format(cnpj=cnpj, ano=ano, seq=seq)
    for tent in range(4):
        try:
            r = s.get(url, headers={"Accept": "application/json"}, timeout=30)
            if r.status_code == 200 and r.text.strip()[:1] == "{":
                d = r.json()
                return (True, {
                    "data_abertura_proposta": d.get("dataAberturaProposta"),
                    "data_encerramento_proposta": d.get("dataEncerramentoProposta"),
                    "situacao_pncp": d.get("situacaoCompraNome"),
                })
            if r.status_code == 404:
                return (True, None)
        except Exception:
            pass
        time.sleep(1.5 * (tent + 1))
    return (False, None)


def _seq_de_controle(numero_controle_pncp: str) -> str | None:
    """Extrai o sequencial do numero_controle_pncp (ex.: 'cnpj-1-000108/2026' → '108')."""
    import re as _re
    m = _re.search(r"-(\d+)\s*/\s*\d{4}\s*$", str(numero_controle_pncp or ""))
    return str(int(m.group(1))) if m else None


def _linha(it: dict, banco: str, tipo: str, termo: str = "") -> dict:
    return {
        "fonte": "PNCP",
        "banco": banco,
        "tipo_documento": tipo,
        "numero_controle_pncp": it.get("numero_controle_pncp", ""),
        "numero": it.get("numero", ""),
        "tipo": it.get("modalidade_licitacao_nome") or it.get("tipo_nome", ""),
        "titulo": it.get("title", ""),
        "objeto": it.get("description") or it.get("title", ""),
        "link": _link_app(it.get("item_url", "")),
        "data": _data(it),
        "ano": _ano(it),
        "valor": it.get("valor_global", ""),
        "orgao": it.get("orgao_nome", ""),
        "orgao_cnpj": it.get("orgao_cnpj", ""),
        "unidade": it.get("unidade_nome", ""),
        "uf": it.get("uf", ""),
        "situacao": it.get("situacao_nome", ""),
        "filtros_usados": termo,
    }


def _coletar_por_cnpj(s) -> list[dict]:
    rows = []
    for banco, cnpj in BANCOS.items():
        for tipo in TIPOS:
            pagina = 1
            while pagina <= MAX_PAGINAS:
                d = _busca(s, cnpj, tipo, pagina)
                if not d:
                    break
                itens = [it for it in d.get("items", []) if str(it.get("orgao_cnpj", "")) == cnpj]
                rows += [_linha(it, banco, tipo) for it in itens if _ano(it) in ANOS_ALVO]
                anos_pag = [_ano(it) for it in itens if _ano(it)]
                if anos_pag and all(int(a) < _ANO_MIN for a in anos_pag):
                    break
                # Ordenado por data desc: página inteira mais velha que RETRO_DIAS
                # encerra a busca deste banco/tipo (não há carga retroativa).
                datas = [_data(it)[:10] for it in d.get("items", []) if _data(it)]
                if datas and max(datas) < _LIMITE_RETRO:
                    break
                if len(d.get("items", [])) < TAM:
                    break
                pagina += 1
                time.sleep(0.25)
    return rows


def _coletar_por_termo(s) -> list[dict]:
    """Editais RECEBENDO PROPOSTA que casam com cada termo, de qualquer órgão."""
    rows = []
    for termo in BUSCA_TERMOS_PNCP:
        n = 0
        for pagina in range(1, MAX_PAGINAS_TERMO + 1):
            d = _busca(s, termo, "edital", pagina, status="recebendo_proposta")
            if not d:
                break
            itens = d.get("items", [])
            for it in itens:
                rows.append(_linha(it, "", "edital", termo))
                n += 1
            if len(itens) < TAM:
                break
            time.sleep(0.25)
        print(f"PNCP termo '{termo}': {n} edital(is) recebendo proposta")
    return rows


def collect_pncp(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(HEADERS)

    rows = _coletar_por_cnpj(s) + _coletar_por_termo(s)
    df = pd.DataFrame(rows)
    if not df.empty:
        # O mesmo edital volta por CNPJ e por vários termos: uma linha só, juntando
        # os termos que o trouxeram em `filtros_usados`.
        termos = (df.groupby("numero_controle_pncp")["filtros_usados"]
                    .apply(lambda x: "; ".join(sorted({t for t in x if t}))))
        df = df.drop_duplicates("numero_controle_pncp").copy()
        df["filtros_usados"] = df["numero_controle_pncp"].map(termos)
    out = dest_dir / "pncp_contratacoes.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"PNCP: {len(df)} documentos (recorte {sorted(ANOS_ALVO)} + termos) -> {out}")
    return out


if __name__ == "__main__":
    print(collect_pncp())


# ── Identificadores para a consulta de prazo ─────────────────────────────────
# O número de controle do PNCP carrega, ele mesmo, os três campos que a API de
# detalhe precisa: <CNPJ de 14 dígitos>-<tipo>-<sequencial>/<ano>. Isso importa
# porque nem toda fonte que traz o número traz CNPJ e ano em colunas próprias —
# o ComprasNet, por exemplo, devolve `numeroControlePNCP` e a razão social do
# órgão, sem o CNPJ. Antes, o prazo só era consultado quando `fonte == 'PNCP'`,
# então os editais do ComprasNet (que SÃO editais do PNCP, coletados por outra
# porta) ficavam para sempre em "prazo não consultado".
_CONTROLE_COMPLETO = re.compile(r"^\s*(\d{14})-\d+-(\d+)\s*/\s*(\d{4})\s*$")


def identificadores_de_controle(numero_controle_pncp) -> tuple[str, str, str] | None:
    """(cnpj, ano, sequencial) extraídos do número de controle, ou None.

    >>> identificadores_de_controle("33657248000189-2-000128/2026")
    ('33657248000189', '2026', '128')
    """
    m = _CONTROLE_COMPLETO.match(str(numero_controle_pncp or ""))
    if not m:
        return None
    return (m.group(1), m.group(3), str(int(m.group(2))))


def identificadores_para_prazo(meta: dict) -> tuple[str, str, str] | None:
    """Resolve (cnpj, ano, seq) de uma linha do bronze para consultar o prazo.

    Ordem: o número de controle primeiro (auto-suficiente e presente em toda
    fonte que passa pelo PNCP), e as colunas nativas como reserva — o coletor do
    PNCP preenche `orgao_cnpj`/`ano` separadamente e pode ter número de controle
    fora do formato canônico. Devolve None quando não dá para montar a consulta:
    é o caso de dispensa/contrato sem controle PNCP, que segue sem prazo.
    """
    ident = identificadores_de_controle((meta or {}).get("numero_controle_pncp"))
    if ident:
        return ident
    cnpj = str((meta or {}).get("orgao_cnpj") or "").strip()
    ano = str((meta or {}).get("ano") or "").strip()
    seq = _seq_de_controle((meta or {}).get("numero_controle_pncp"))
    if cnpj and ano and seq:
        return (cnpj, ano, seq)
    return None
