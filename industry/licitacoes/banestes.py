"""Coletor Banestes — TODAS as categorias (requests + bs4).

Cada categoria é uma página própria (Inexigibilidade sozinha tem ~900 itens);
tudo vem no HTML (jplist client-side), sem paginação. Extrai número do edital
COM ano ("Nº 068/2021" -> "068/2021", com IGNORECASE) e um id ESTÁVEL por
conteúdo (não-posicional). URLs de PDF vão em `documentos` (JSON) p/ o anexos.py.
Substitui a versão Selenium (que pegava só 1 página / 32 itens).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import DEST_DIR, REQUEST_HEADERS, REQUEST_TIMEOUT, ANOS_ALVO
from industry.licitacoes.net import ColetorBloqueado

DOMAIN = "https://www.banestes.com.br"
BASE = "https://www.banestes.com.br/publicacoes_legais/"
FONTE = "Banestes - Editais e Avisos"


def _abs_url(u: str) -> str:
    """Resolve URL de anexo evitando duplicar '/publicacoes_legais/'.
    - http(s)://...        -> como está
    - //host/...           -> https:...
    - /publicacoes_legais/ -> root-relative: DOMÍNIO + u  (NÃO BASE, senão dobra)
    - arquivos_colic/...   -> relativo a /publicacoes_legais/: BASE + u
    """
    u = u.strip()
    if u.startswith("http"):
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        return DOMAIN + u
    return BASE + u
PAGINAS = {
    "Audiência Pública": "audiencia-publica.html",
    "Concorrência": "editais_concorrencia.html",
    "Credenciamento": "editais-credenciamento.html",
    "Licitação": "editais-licitacao.html",
    "Pregão Eletrônico e Presencial": "editais_pregao.html",
    "Registro de preços": "registro_precos.htm",
    "Tomada de preços": "tomada-preco.html",
    "Cadastramento de empresas": "cadastro_empresas.html",
    "Dispensa de licitação": "aviso_dispensa_licitacao.html",
    "Inexigibilidade": "aviso_inexigibilidade.html",
    "Contrato": "contratos.html",
}
DISP_RE = re.compile(r"displayWindow\('([^']+)','([^']*)'")
PDF_HREF_RE = re.compile(r"(arquivos_[a-z]+/[^\"')\s]+\.pdf|/publicacoes_legais/[^\"')\s]+\.pdf)", re.I)
DATA_RE = re.compile(r"(\d{2})[./](\d{2})[./](\d{2,4})")
# Data só com fronteira dos dois lados: sem isso o "DD.MM.AAAA" casava DENTRO do
# ID CidadES do TCE ("2026.500E1600004.09.0062" virava 04/09/0062), que é o que
# deixava ~345 contratos da carga de julho sem data. Ver DECISOES.md §27.
_DATA_ISOLADA = r"(?<!\d)(?<!\d\.)(\d{2})[./](\d{2})[./](\d{4}|\d{2})(?!\d)(?!\.\d)"
# Contrato não tem `data-publicacao`; a data que ele expõe é o início da vigência
# ("Prazo de Vigência: 25/09/2026 a 24/09/2028", "180 dias, contados a partir de
# 13/05/2026") ou a assinatura ("Firmado em").
_DATA_CONTRATO_RE = re.compile(
    r"(?:Vig[êe]ncia|Firmado\s+em).{0,80}?" + _DATA_ISOLADA, re.I)
_DATA_PUB_RE = re.compile(_DATA_ISOLADA)
# Identificador do item como o site o mostra no título ("Contrato 4600001333 –",
# "Nº 047/2024", "nº 251-26"). É o que a pessoa procura com Ctrl+F na página da
# categoria — o `numero` normalizado ("251/2026") não aparece escrito lá.
_COD_TITULO_RE = re.compile(r"\d[\d./-]*\d")
_PROCESSO_RE = re.compile(r"Processo\s*(?:n\.?\s*º?|nº|n°)?\s*:?\s*(\d{4}-[A-Z0-9]{5})", re.I)
VAL_RE = re.compile(
    r"Valor\s*(?:Global|Total|Estimad[oa]|M[áa]xim[oa]|Anual|da\s+Contrata\S*|"
    r"do\s+Contrato|Contratual|Mensal|Unit[áa]rio)?[^\n]{0,35}?R\$\s*([\d.]+,\d{2})", re.I)

# número do edital COM ano — com IGNORECASE (o bug era o "Nº" maiúsculo não casar)
_NUM_MARC = re.compile(r"n[ºo°]\s*(\d{1,5})\s*[-/.]\s*(\d{2,4})", re.IGNORECASE)
_NUM_BARE = re.compile(r"(?<![\d/.])(\d{1,5})\s*/\s*(20\d{2})(?![\d/])")


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:24]


def _numero_edital(texto: str) -> str:
    m = _NUM_MARC.search(texto) or _NUM_BARE.search(texto)
    if not m:
        return ""
    ano = m.group(2)
    ano = ("20" + ano) if len(ano) == 2 else ano
    return f"{m.group(1)}/{ano}"


def _data_valida(dd: str, mm: str, aa: str) -> str:
    """'DD/MM/AAAA' se for uma data plausível de publicação, senão ''."""
    from datetime import date
    ano = int(("20" + aa) if len(aa) == 2 else aa)
    try:
        d = date(ano, int(mm), int(dd))
    except ValueError:
        return ""
    if not (1990 <= d.year <= date.today().year + 1):
        return ""
    return d.strftime("%d/%m/%Y")


def _data_item(acc, texto: str) -> str:
    """Data do item: publicação (`span.data-publicacao`, "18.09.24") quando o
    site a expõe; para contrato, início da vigência/assinatura; senão vazio.

    Vazio é melhor do que chutar: sem data o pipeline cai na data em que vimos o
    item pela primeira vez (bronze.run_date, DECISOES.md §9). Antes pegava a
    PRIMEIRA coisa com cara de data no texto — sessão, período do evento ou um
    pedaço do ID do TCE.
    """
    span = acc.select_one(".data-publicacao")
    if span is not None:
        m = _DATA_PUB_RE.search(span.get_text(" ", strip=True))
        if m:
            return _data_valida(*m.groups())
    m = _DATA_CONTRATO_RE.search(texto)
    if m:
        return _data_valida(*m.groups())
    return ""


def _codigo_titulo(acc) -> str:
    """Identificador do item como aparece no título (sem a data de publicação)."""
    h = acc.select_one("h3")
    if h is None:
        return ""
    partes = [t for t in h.find_all(string=True)
              if "data-publicacao" not in (t.parent.get("class") or [])]
    m = _COD_TITULO_RE.search(" ".join(partes))
    return m.group(0) if m else ""


def _link_item(links: list[dict], pagina_categoria: str) -> str:
    """PDF do próprio item quando houver (o site não tem página por processo);
    senão a página da categoria."""
    for d in links:
        u = d.get("url", "")
        if u.lower().endswith(".pdf") and "javascript" not in u.lower():
            return u
    return pagina_categoria


def _id_banestes(categoria, numero, texto) -> str:
    disc = hashlib.md5(f"{categoria}||{numero}||{texto}".encode()).hexdigest()[:8]
    if numero:
        return f"banestes-{_slug(categoria)}-{numero.replace('/', '-')}-{disc}"
    return f"banestes-{_slug(categoria)}-{disc}"


def _valor(texto):
    achados = VAL_RE.findall(texto)
    if not achados:
        return ""
    try:
        return max((float(v.replace(".", "").replace(",", ".")), v) for v in achados)[1]
    except ValueError:
        return achados[0]


def _links(acc, html_item):
    out = []
    for path, _ in DISP_RE.findall(html_item):
        out.append({"url": _abs_url(path)})
    for a in acc.find_all("a", href=True):
        if PDF_HREF_RE.search(a["href"]):
            full = _abs_url(a["href"])
            if full not in [x["url"] for x in out]:
                out.append({"url": full, "label": a.get_text(" ", strip=True)[:60]})
    return out


def soup_da_pagina(conteudo: bytes) -> BeautifulSoup:
    """Parse de uma página de categoria.

    Decodifica como UTF-8 (o que as páginas declaram) ANTES do parser. Com bytes
    crus o BeautifulSoup adivinha Windows-1252 em contratos/pregão/inexigibilidade,
    porque o próprio site tem meia dúzia de bytes UTF-8 truncados ("ESP\\xc3?RITO")
    — e aí o texto inteiro saía "Presta√ß√£o". `replace` troca só esses bytes
    quebrados por "�". Ver DECISOES.md §27.
    """
    return BeautifulSoup(conteudo.decode("utf-8", errors="replace"), "lxml")


def itens_da_soup(soup: BeautifulSoup):
    """Os blocos (accordion) de cada item da página, na ordem do HTML."""
    return soup.select("[data-jplist-item]") or soup.select("div.elemento-accordion")


def linha_do_item(acc, categoria: str, pagina: str) -> dict:
    """Linha nativa de um item. Sem recorte de ano — quem filtra é o chamador
    (`collect_banestes`); a migração do §27 precisa de todos os anos."""
    html_item = str(acc)
    texto = re.sub(r"\s+", " ", acc.get_text(" ", strip=True))
    numero = _numero_edital(texto)
    data = _data_item(acc, texto)
    ano = numero.split("/")[-1] if numero else (data.split("/")[-1] if data else "")
    mo = re.search(r"(?:Patroc[íi]nio|Contrata[çc][ãa]o|Aquisi[çc][ãa]o|Presta[çc][ãa]o|"
                   r"Objeto|Registro de Pre|Credenciamento|Concess)[^.]{0,400}", texto, re.I)
    objeto = (mo.group(0) if mo else texto)[:1500]
    links = _links(acc, html_item)
    codigo = _codigo_titulo(acc) or numero
    mp = _PROCESSO_RE.search(texto)
    return {
        "id": _id_banestes(categoria, numero, texto), "fonte": FONTE,
        "tipo": categoria, "categoria": categoria,
        "titulo": f"{categoria} nº {numero} {data}".strip(),
        # Banestes não tem página por processo: o link vai direto no PDF do item
        # e o identificador do TÍTULO (como está escrito no site) vai prefixado no
        # objeto, p/ achar o item na página da categoria. Ver §14/§27.
        "objeto": (f"[{codigo}] {objeto}" if codigo else objeto),
        "link": _link_item(links, BASE + pagina), "data": data,
        "processo": mp.group(1) if mp else "",
        "valor": _valor(texto), "numero": numero, "ano": ano,
        "contratado": "",  # extraível do texto; fica p/ etapa de contratados
        "texto_completo": texto,
        "documentos": json.dumps(links, ensure_ascii=False) if links else "",
    }


def collect_banestes(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    rows = []
    falhas = []
    for categoria, pagina in PAGINAS.items():
        try:
            r = s.get(BASE + pagina, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
        except Exception as e:
            print(f"[banestes] falha {pagina}: {str(e)[:50]}")
            falhas.append(f"{pagina}: {str(e)[:80]}")
            continue
        for acc in itens_da_soup(soup_da_pagina(r.content)):
            linha = linha_do_item(acc, categoria, pagina)
            if linha["ano"] and linha["ano"] not in ANOS_ALVO:   # recorte recente
                continue
            rows.append(linha)
    if falhas:
        # Falha visível (task vermelha + alerta), não CSV parcial em silêncio. A
        # listagem é completa a cada coleta, então a próxima run recupera tudo.
        raise ColetorBloqueado(f"{FONTE}: {len(falhas)} página(s) falharam — " + "; ".join(falhas))
    df = pd.DataFrame(rows)
    out = dest_dir / "banestes.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Banestes: {len(df)} itens (todas as categorias) -> {out}")
    return out


if __name__ == "__main__":
    print(collect_banestes())
