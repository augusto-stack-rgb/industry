"""Coletores de portais de CPSI e desafios de inovação fora do PNCP.

A LC 182/2021 (art. 13, §2º) manda publicar o edital de CPSI no diário oficial e
num sítio oficial que pode ser o PNCP OU um mantido pelo próprio ente. Muitos
desafios só aparecem nestes portais, e as consultas públicas (etapa antes do
edital) quase nunca chegam ao PNCP. Um coletor por portal:

- GovTech Place: marketplace de desafios de órgãos públicos (API JSON pública
  que a própria página usa); traz consultas públicas e editais.
- Enap Desafios (gov.br/desafios): desafios de inovação aberta de órgãos federais.
- CNJ: página própria de CPSI do Conselho Nacional de Justiça.
- Goiás GovTech: notícias de editais GovTech/CPSI dos sites das secretarias
  (WordPress). Os desafios ficam na plataforma do Porto Digital, que não tem
  listagem pública.
- Copasa HUB: editais de CPSI da Copasa (MG).
- Desafio Rio: consultas e editais da Prefeitura do Rio.
- CatalisaGov (Sebrae): consultas públicas municipais anunciadas na Agência
  Sebrae, porque o programa não tem lista própria.

Fica de fora o Petrobras Conexões para Inovação: o portal é montado por
JavaScript (Salesforce) sem listagem acessível sem navegador.

A data de publicação vai em ``publicado_em`` (fica nos dados originais), não
em ``data``: o bronze corta o que foi publicado há mais de 7 dias, e um desafio
publicado há um mês com prazo aberto sumiria. Sem ``data``, vale a primeira
coleta. Cada coletor só traz o que está aberto ou não tem prazo conhecido.

Estas fontes não passam pelo filtro de tema (gold.filtrar_tema): desafio de
inovação raramente usa os termos ("Cenas 3D: visualização forense") e o LLM é
quem decide a relevância. Ver FONTES_INOVACAO em config.py.
"""
from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

from industry.licitacoes.config import CPSI_PORTAIS_DIAS, DEST_DIR, REQUEST_HEADERS
from industry.licitacoes.net import ColetorBloqueado, get_json

FONTE_GOVTECH = "GovTech Place - Desafios de órgãos públicos"
FONTE_ENAP = "Enap - Desafios (gov.br)"
FONTE_CNJ = "CNJ - Contratação Pública de Solução Inovadora"
FONTE_GOIAS = "Goiás GovTech - Editais CPSI"
FONTE_COPASA = "Copasa HUB - Editais CPSI"
FONTE_RIO = "Desafio Rio - Prefeitura do Rio"
FONTE_CATALISA = "CatalisaGov (Sebrae) - Consultas públicas"

MESES = {m: i for i, m in enumerate(["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho",
                                     "agosto", "setembro", "outubro", "novembro", "dezembro"], 1)}
_RX_DATA_NUM = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_RX_DATA_EXT = re.compile(r"\b(\d{1,2})[ºo°]?\s+de\s+(janeiro|fevereiro|mar[çc]o|abril|maio|junho|julho|agosto|"
                          r"setembro|outubro|novembro|dezembro)(?:\s+de\s+(\d{4}))?", re.I)
_RX_PRAZO = re.compile(r"(?i)(?:at[ée]|encerra\w*\s+em|prazo\w*\s+(?:at[ée]|final)?|inscri[çc][õo]es\s+at[ée])\s*[:\-]?\s*")


def _sem_html(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def _id(*partes) -> str:
    return hashlib.sha1("|".join(str(p) for p in partes).encode()).hexdigest()[:16]


def _data_de(texto: str, ano_padrao: int) -> str:
    """Primeira data (dd/mm[/aaaa] ou "15 de junho [de 2026]") do texto -> ISO."""
    t = texto or ""
    m = _RX_DATA_NUM.search(t)
    if m:
        d, mo, a = int(m.group(1)), int(m.group(2)), m.group(3)
        a = int(a) + (2000 if a and len(a) == 2 else 0) if a else ano_padrao
        try:
            return date(a, mo, d).isoformat()
        except ValueError:
            pass
    m = _RX_DATA_EXT.search(t)
    if m:
        mes = MESES[m.group(2).lower().replace("ç", "c")]
        try:
            return date(int(m.group(3) or ano_padrao), mes, int(m.group(1))).isoformat()
        except ValueError:
            pass
    return ""


def _prazo_no_texto(texto: str, ano_padrao: int) -> str:
    """Data que vem logo depois de "até", "inscrições até", "prazo"..."""
    for m in _RX_PRAZO.finditer(texto or ""):
        d = _data_de(texto[m.end():m.end() + 40], ano_padrao)
        if d:
            return d
    return ""


def _salvar(rows: list[dict], dest_dir: Path, nome: str, rotulo: str) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["id", "fonte", "objeto", "link"])
    out = dest_dir / f"{nome}.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"{rotulo}: {len(df)} -> {out}")
    return out


def _abertos(rows: list[dict]) -> list[dict]:
    """Fica o que tem prazo de hoje em diante ou prazo desconhecido."""
    hoje = date.today().isoformat()
    return [r for r in rows if not r.get("data_encerramento_proposta")
            or r["data_encerramento_proposta"] >= hoje]


def _sessao() -> requests.Session:
    s = requests.Session()
    s.headers.update(REQUEST_HEADERS)
    return s


def _html(s: requests.Session, url: str, fonte: str, tentativas: int = 4) -> BeautifulSoup:
    erro = None
    for i in range(tentativas):
        try:
            r = s.get(url, timeout=60)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            erro = e
            time.sleep(5 * (i + 1))
    raise ColetorBloqueado(f"{fonte}: {type(erro).__name__}: {erro}")


def _ano_mais_recente(texto: str) -> int:
    anos = [int(a) for a in re.findall(r"(20\d{2})", texto or "")]
    return max(anos) if anos else 0


def _tipo(titulo: str) -> str:
    return ("Consulta pública (antes do edital)" if re.search(r"(?i)consulta\s+p[úu]blica", titulo or "")
            else "CPSI / Desafio de inovação")


# ── GovTech Place ────────────────────────────────────────────────────────────
GOVTECH_API = "https://api.govtechplace.com.br/api/challenges-site"


def collect_govtech_place(dest_dir: Path = DEST_DIR) -> Path:
    d = get_json(GOVTECH_API, fonte="GovTech Place", session=_sessao(), timeout=60)
    hoje = date.today().isoformat()
    rows = []
    for c in d.get("challenges") or []:
        prazo = (c.get("date_limit") or "")[:10]
        criado = (c.get("created_at") or "")[:10]
        if prazo and prazo < hoje:
            continue
        org = (c.get("organization") or {})
        orgao = c.get("entity") or org.get("name") or ""
        descricao = " ".join(x for x in (_sem_html(c.get("description")), _sem_html(c.get("contextualization")),
                                          _sem_html(c.get("objectives"))) if x and x != "--" and x != "---")
        rows.append({
            "id": str(c.get("id", "")),
            "fonte": FONTE_GOVTECH,
            "tipo_documento": "edital",
            "tipo": _tipo(c.get("name", "")),
            "titulo": c.get("name", ""),
            "objeto": f"{c.get('name', '')} — {descricao}"[:3000],
            "publicado_em": criado,
            "data_encerramento_proposta": prazo,
            "orgao": orgao,
            "responsavel": c.get("contact_email") or "",
            "link": c.get("challenge_url") or c.get("external_registration_url") or "https://govtechplace.com.br/#challenges",
            "observacao": f"faturamento mínimo: {c.get('faturament') or '-'}; TRL: {c.get('trl_id') or '-'}",
        })
    return _salvar(rows, dest_dir, "govtech_place", "GovTech Place")


# ── Enap Desafios ────────────────────────────────────────────────────────────
ENAP_URL = "https://desafios.enap.gov.br/desafios/"


def collect_enap_desafios(dest_dir: Path = DEST_DIR) -> Path:
    s = _sessao()
    soup = _html(s, ENAP_URL, "Enap Desafios")
    hoje = date.today()
    rows = []
    for a in soup.select("a.card-item[href]"):
        texto = a.get_text(" ", strip=True)
        titulo = " ".join(p.get_text(" ", strip=True) for p in a.select(".card-descricao p")[:1]) or texto[:150]
        link = urljoin(ENAP_URL, a["href"])
        rows.append({
            "id": _id(link),
            "fonte": FONTE_ENAP,
            "tipo_documento": "edital",
            "tipo": "Desafio de inovação aberta",
            "titulo": titulo,
            "objeto": texto,
            "data_encerramento_proposta": _prazo_no_texto(texto, hoje.year),
            "orgao": "Enap",
            "link": link,
        })
    return _salvar(_abertos(rows), dest_dir, "enap_desafios", "Enap Desafios")


# ── CNJ ──────────────────────────────────────────────────────────────────────
CNJ_URL = "https://www.cnj.jus.br/transparencia-cnj/gestao-administrativa/licitacoes-e-contratos/cpsi/"


def collect_cnj_cpsi(dest_dir: Path = DEST_DIR) -> Path:
    soup = _html(_sessao(), CNJ_URL, "CNJ CPSI")
    rows = []
    for a in soup.find_all("a", href=True):
        m = re.search(r"edital[-_]cnj[-_]cpsi[-_](\d+)[-_](\d{4})\.pdf", a["href"], re.I)
        if not m:
            continue
        if int(m.group(2)) < date.today().year:
            continue      # edital de ano anterior: já encerrado
        numero = f"{int(m.group(1)):02d}/{m.group(2)}"
        bloco = a.find_parent(["div", "section", "article"]) or a
        texto = bloco.get_text(" ", strip=True)[:2000]
        rows.append({
            "id": f"cnj-cpsi-{numero}",
            "fonte": FONTE_CNJ,
            "tipo_documento": "edital",
            "tipo": "CPSI / Desafio de inovação",
            "numero": numero,
            "titulo": f"Edital CNJ CPSI {numero}",
            "objeto": texto,
            "ano": m.group(2),
            "orgao": "Conselho Nacional de Justiça",
            "uf": "DF",
            "link": CNJ_URL,
            "edital_url": urljoin(CNJ_URL, a["href"]),
        })
    rows = list({r["id"]: r for r in rows}.values())
    return _salvar(rows, dest_dir, "cnj_cpsi", "CNJ CPSI")


# ── Goiás GovTech (notícias das secretarias) ─────────────────────────────────
GOIAS_SITES = ["economia", "administracao", "inovacao", "saude", "educacao", "seds", "agricultura"]
_RX_GOIAS_EDITAL = re.compile(r"(?i)(edital|inscri[çc][õo]es|consulta p[úu]blica)")
_RX_GOIAS_FORA = re.compile(r"(?i)(forma[çc][ãa]o|curso|capacita|live|e-book|pr[êe]mio|resultado)")


def collect_goias_govtech(dest_dir: Path = DEST_DIR) -> Path:
    s = _sessao()
    hoje = date.today()
    corte = (hoje - timedelta(days=CPSI_PORTAIS_DIAS)).isoformat()
    rows = {}
    for site in GOIAS_SITES:
        for termo in ("CPSI", "GovTech"):
            url = f"https://goias.gov.br/{site}/wp-json/wp/v2/posts"
            try:
                r = s.get(url, params={"search": termo, "per_page": 20, "after": f"{corte}T00:00:00",
                                       "_fields": "id,date,link,title,excerpt"}, timeout=60)
            except requests.RequestException:
                continue
            if r.status_code != 200 or not r.text.strip().startswith("["):
                continue
            for p in r.json():
                titulo = _sem_html(p["title"]["rendered"])
                resumo = _sem_html(p.get("excerpt", {}).get("rendered", ""))
                if not _RX_GOIAS_EDITAL.search(titulo) or _RX_GOIAS_FORA.search(titulo):
                    continue
                ano = int(p["date"][:4])
                # a mesma notícia sai em vários sites das secretarias: dedup pelo título
                rows.setdefault(titulo.lower(), {
                    "id": _id(titulo.lower()),
                    "fonte": FONTE_GOIAS,
                    "tipo_documento": "edital",
                    "tipo": _tipo(titulo),
                    "titulo": titulo,
                    "objeto": f"{titulo} — {resumo}"[:2000],
                    "publicado_em": p["date"][:10],
                    "data_encerramento_proposta": _prazo_no_texto(f"{titulo} {resumo}", ano),
                    "orgao": f"Governo de Goiás ({site})",
                    "uf": "GO",
                    "link": p["link"],
                })
    return _salvar(_abertos(list(rows.values())), dest_dir, "goias_govtech", "Goiás GovTech")


# ── Copasa HUB ───────────────────────────────────────────────────────────────
COPASA_URL = "https://hub.copasa.com.br"


def collect_copasa_hub(dest_dir: Path = DEST_DIR) -> Path:
    """Bloco "Oportunidades em aberto" da página: "CHAMAMENTO PD&I | <título> |
    ... | Confira o edital" (link para o portal de compras da Copasa)."""
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0"   # o site derruba a conexão com o UA completo de Chrome
    soup = _html(s, COPASA_URL, "Copasa HUB")
    texto = soup.get_text(" | ", strip=True)
    edital = next((urljoin(COPASA_URL, a["href"]) for a in soup.find_all("a", href=True)
                   if re.search(r"(?i)edital|PortalCompras", a.get_text(" ", strip=True) + a["href"])), "")
    rows = []
    for m in re.finditer(r"(CHAMAMENTO[^|]{0,60}|EDITAL[^|]{0,60}|CPSI[^|]{0,60})\|\s*([^|]{30,400})", texto):
        tipo, titulo = m.group(1).strip(), m.group(2).strip()
        rows.append({
            "id": _id(titulo.lower()),
            "fonte": FONTE_COPASA,
            "tipo_documento": "edital",
            "tipo": tipo.title(),
            "titulo": titulo,
            "objeto": f"{tipo} — {titulo}",
            "data_encerramento_proposta": _prazo_no_texto(texto[m.end():m.end() + 500], date.today().year),
            "orgao": "Copasa",
            "uf": "MG",
            "link": edital or COPASA_URL,
        })
    rows = list({r["id"]: r for r in rows}.values())
    return _salvar(_abertos(rows), dest_dir, "copasa_hub", "Copasa HUB")


# ── Desafio Rio ──────────────────────────────────────────────────────────────
RIO_URL = "https://desafio.rio/"


def collect_desafio_rio(dest_dir: Path = DEST_DIR) -> Path:
    soup = _html(_sessao(), RIO_URL, "Desafio Rio")
    hoje = date.today()
    texto = soup.get_text(" ", strip=True)
    rows = []
    if _ano_mais_recente(texto) < hoje.year:
        return _salvar(rows, dest_dir, "desafio_rio", "Desafio Rio (só edições anteriores)")
    for a in soup.find_all("a", href=True):
        rotulo = a.get_text(" ", strip=True)
        if not re.search(r"(?i)edital|consulta", rotulo) or re.search(r"(?i)relat[óo]rio|resultado", rotulo):
            continue
        rows.append({
            "id": _id(a["href"]),
            "fonte": FONTE_RIO,
            "tipo_documento": "edital",
            "tipo": _tipo(rotulo),
            "titulo": f"Desafio Rio — {rotulo}",
            "objeto": f"Desafio Rio — {rotulo} — {texto[:1500]}",
            "data_encerramento_proposta": _prazo_no_texto(texto, hoje.year),
            "orgao": "Prefeitura do Rio de Janeiro",
            "uf": "RJ",
            "municipio": "Rio de Janeiro",
            "link": a["href"],
        })
    rows = list({r["id"]: r for r in rows}.values())
    return _salvar(_abertos(rows), dest_dir, "desafio_rio", "Desafio Rio")


# ── CatalisaGov (Agência Sebrae) ─────────────────────────────────────────────
CATALISA_API = "https://agenciasebrae.com.br/wp-json/wp/v2/posts"
_RX_CATALISA = re.compile(r"(?i)catalisa")


def collect_catalisa_gov(dest_dir: Path = DEST_DIR) -> Path:
    s = _sessao()
    corte = (date.today() - timedelta(days=CPSI_PORTAIS_DIAS)).isoformat()
    try:
        r = s.get(CATALISA_API, params={"search": "Catalisa", "per_page": 30, "after": f"{corte}T00:00:00",
                                        "_fields": "id,date,link,title,excerpt,content"}, timeout=60)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ColetorBloqueado(f"CatalisaGov: {type(e).__name__}: {e}") from e
    rows = []
    for p in r.json():
        titulo = _sem_html(p["title"]["rendered"])
        corpo = _sem_html(p.get("content", {}).get("rendered", ""))
        if not re.search(r"(?i)catalisa|consulta p[úu]blica|cpsi|solu[çc][õo]es inovadoras|startups e prefeituras",
                         titulo):
            continue
        # só notícia que fala de consulta pública ou edital aberto
        if not re.search(r"(?i)consulta[s]? p[úu]blica|edital|inscri[çc]", corpo):
            continue
        ano = int(p["date"][:4])
        trecho = corpo[:2500]
        rows.append({
            "id": str(p["id"]),
            "fonte": FONTE_CATALISA,
            "tipo_documento": "edital",
            "tipo": "Consulta pública (antes do edital)",
            "titulo": titulo,
            "objeto": f"{titulo} — {trecho}",
            "publicado_em": p["date"][:10],
            "data_encerramento_proposta": _prazo_no_texto(corpo, ano),
            "orgao": "Sebrae / municípios do CatalisaGov",
            "link": p["link"],
        })
    return _salvar(_abertos(rows), dest_dir, "catalisa_gov", "CatalisaGov")
