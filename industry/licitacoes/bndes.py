"""Coletor BNDES — Consulta de Licitações (requests, sem Selenium).

Fonte (página de consulta, IBM WebSphere Portal):
  https://www.bndes.gov.br/wps/portal/site/home/transparencia/licitacoes-contratos/licitacoes/consulta-licitacoes

A página é renderizada por um web component (`<bndes-data-search-geral>`) que
carrega TODAS as licitações de um único JSON estático do portal:

  https://www.bndes.gov.br/arquivos/tabelas-graficos/licitacoes/licitacoes.json

Formato: ``{"dados": [{"$config": {...}, "linhas": [...]}], "origemDados": "..."}``.
- ``$config.colunas`` é uma lista de ``{"campo": "2".."18", "titulo": "..."}``;
- ``linhas`` são as licitações, cada uma um dict com essas chaves numéricas;
- o campo ``"1"`` não está em ``colunas``: é o título do registro
  (``$config.tituloRegistro == "[1]"``), ex. "Pregão Eletrônico 028/2026".

Portanto não é preciso Selenium nem uma chamada por licitação: baixa o JSON,
decodifica o mapa de colunas e materializa as linhas com os TÍTULOS NATIVOS —
que o `config.GRUPOS` já sabe mapear para o superset do bronze ("Valor estimado
da licitação", "Data de publicação do edital", "Situação da licitação - PTP",
"Razão social do fornecedor", "Identificação da licitação"...). Esse mapeamento
foi herdado da carga histórica de 2026-07 e nunca teve um coletor que o
alimentasse: por isso a fonte tinha 344 linhas no bronze, todas com a sentinela
`run_date = 2020-01-01`, e nenhuma coleta desde então.

Anexos
------
Cada licitação tem uma pasta no portal, listável por
``/WCMUtil/api/diretorio/licitacoes@<título com "/" trocado por "-">`` (é o que
o JS do portal monta). A API devolve ``{"fileName": "/arquivos/licitacoes/<pasta>/<arquivo>", ...}``
e o arquivo é baixável em ``https://www.bndes.gov.br`` + fileName. Preenchemos a
coluna ``documentos`` com essas URLs; o download/extração fica com o `anexos.py`,
igual às outras fontes. A listagem é best-effort: falha de uma pasta não derruba
a coleta.

Divergências propositais em relação ao scraper standalone
---------------------------------------------------------
- **Sem filtro de valor mínimo.** O standalone descartava licitações abaixo de
  R$ 100.000. Aqui não: quem decide relevância é o score (keyword/zero-shot/LLM),
  e consultoria abaixo de 100k é exatamente o tipo de coisa que interessa.
- **CPF/CNPJ do fornecedor não é coletado.** O campo 17 do JSON é "CNPJ ou CPF do
  fornecedor" e pode trazer CPF de fornecedor pessoa física. `GRUPOS` mapearia
  para `contratado_cnpj`, mas preferimos não ingerir o campo: a razão social
  (campo 16) já identifica o contratado e nada no pipeline usa o documento.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

from industry.licitacoes.config import (
    ANOS_ALVO, DEST_DIR, REQUEST_HEADERS, REQUEST_TIMEOUT,
)

logger = logging.getLogger(__name__)

FONTE = "BNDES - Banco Nacional de Desenvolvimento Econômico e Social"
BASE = "https://www.bndes.gov.br"
DADOS_JSON = BASE + "/arquivos/tabelas-graficos/licitacoes/licitacoes.json"
DIRETORIO_API = BASE + "/WCMUtil/api/diretorio/"
PAGINA_CONSULTA = (
    BASE + "/wps/portal/site/home/transparencia/licitacoes-contratos/"
    "licitacoes/consulta-licitacoes"
)

# Campo do JSON -> ignorado na materialização.
#   "15" = "Arquivos": vem como template do portal, não como lista de arquivos;
#          o conteúdo real sai da API de diretório (ver `_listar_arquivos`).
#   "17" = "CNPJ ou CPF do fornecedor": ver docstring do módulo.
CAMPOS_IGNORADOS = {"15", "17"}

# Título do registro, que o $config declara como "[1]" e não lista em `colunas`.
# "Identificação da licitação" já está no GRUPOS["numero"].
TITULO_CAMPO = "1"
TITULO_COLUNA = "Identificação da licitação"
ANO_CAMPO = "3"


def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    s.headers["Accept"] = "application/json"
    return s


def _baixar_dados(session: requests.Session) -> tuple[dict, list, str]:
    """Baixa o JSON único. Devolve (mapa campo->título, linhas, origem)."""
    r = session.get(DADOS_JSON, timeout=90)
    r.raise_for_status()
    payload = r.json()
    bloco = payload["dados"][0]
    colmap = {c["campo"]: c.get("titulo", c["campo"])
              for c in bloco["$config"]["colunas"]}
    colmap.setdefault(TITULO_CAMPO, TITULO_COLUNA)
    return colmap, bloco["linhas"], payload.get("origemDados", "")


def _listar_arquivos(session: requests.Session, titulo: str) -> list[dict]:
    """URLs dos anexos da licitação, no formato que o anexos.py consome.

    Best-effort: pasta inexistente ou API fora devolve lista vazia, nunca levanta
    — perder o anexo de um item não pode derrubar a coleta inteira.
    """
    pasta = "licitacoes@" + titulo.replace("/", "-")
    url = quote(DIRETORIO_API + pasta, safe=":/@")
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        itens = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[bndes] anexos de {titulo!r} falharam: {str(e)[:60]}")
        return []
    if not isinstance(itens, list):
        return []
    docs = []
    for it in itens:
        nome = str(it.get("fileName") or "").strip()
        if not nome:
            continue
        docs.append({
            "label": nome.rsplit("/", 1)[-1][:80],
            "url": BASE + quote(nome, safe="/"),
        })
    return docs


def _num(v) -> float | None:
    """Número do JSON ('65535087.71') em float positivo, senão None."""
    s = str(v if v is not None else "").strip().replace(" ", "")
    if not s:
        return None
    for cand in (s, s.replace(".", "").replace(",", ".")):
        try:
            f = float(cand)
        except ValueError:
            continue
        return f if f > 0 else None
    return None


def _valor_efetivo(raw: dict) -> str:
    """Maior valor positivo entre estimado (campo 5) e contratado (campo 6).

    Licitação aberta só tem o estimado; encerrada tem os dois e o contratado é o
    que valeu. Vazio quando a fonte não informou — nunca zero, que o `to_numeric`
    do silver descartaria de qualquer forma e confundiria "sem informação" com
    "custo nulo".
    """
    positivos = [v for v in (_num(raw.get("5")), _num(raw.get("6"))) if v]
    return str(max(positivos)) if positivos else ""


def _data_publicacao(raw: dict) -> str:
    """Data de publicação do edital (campo 9). Vazio se a fonte não informou.

    Só a publicação — NÃO cair para a data de abertura (campo 7). Abertura é
    sempre posterior à publicação, então usá-la como `data` faria um edital
    antigo parecer recente e o seguraria dentro da janela do mês. Sem publicação,
    o pipeline já tem a regra certa: cai no `run_date`, a primeira vez que vimos
    o item (DECISOES.md §9).
    """
    return str(raw.get("9") or "").strip()


def collect_bndes(dest_dir: Path = DEST_DIR, anos: set[str] | None = None) -> Path:
    """Coleta as licitações do BNDES dos `anos` (default: ANOS_ALVO)."""
    anos = anos or ANOS_ALVO
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = _sessao()

    colmap, linhas, origem = _baixar_dados(s)
    logger.info(f"[bndes] {len(linhas)} linhas no JSON | origem: {origem}")

    rows = []
    for raw in linhas:
        if str(raw.get(ANO_CAMPO) or "").strip() not in anos:
            continue
        titulo = str(raw.get(TITULO_CAMPO) or "").strip()
        if not titulo:
            continue
        row = {colmap.get(campo, campo): valor
               for campo, valor in raw.items()
               if campo not in CAMPOS_IGNORADOS}
        docs = _listar_arquivos(s, titulo)
        row.update({
            # `data` e `valor` são os nomes que o resto do medallion consome
            # (silver.data, silver.valor, e daí a janela e o e-mail). Os títulos
            # nativos do BNDES caem em `data_publicacao`/`valor_estimado`, que
            # ficam no bronze mas não chegam ao silver — sem isto a fonte
            # entraria sem data e sem valor, que é a lacuna que as outras
            # fontes têm hoje.
            "data": _data_publicacao(raw),
            "valor": _valor_efetivo(raw),
            # `id` é a chave estável do dedup do bronze (config.SOURCE_STABLE_ID).
            # O título embute modalidade+número+ano e é único no portal.
            "id": titulo,
            "titulo": titulo,
            # O portal não tem URL por licitação — o componente é uma busca só.
            # Mandar a página de consulta é melhor que link vazio: abre a fonte,
            # e o número no objeto/título localiza o item (mesma saída adotada
            # para Banestes e CAIXA, DECISOES.md §14).
            "link": PAGINA_CONSULTA,
            "documentos": json.dumps(docs, ensure_ascii=False) if docs else "",
        })
        rows.append(row)

    df = pd.DataFrame(rows)
    out = dest_dir / "bndes_licitacoes.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    n_docs = sum(1 for r in rows if r.get("documentos"))
    logger.info(f"[bndes] {len(df)} licitações dos anos {sorted(anos)} "
                f"({n_docs} com anexos) -> {out}")
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(collect_bndes())
