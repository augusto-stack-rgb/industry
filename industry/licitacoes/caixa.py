"""Coletor CAIXA SICVE — Sistema de Compras Eletrônicas (Selenium remoto).

App JSF/RichFaces com anti-bot Radware (sem API consumível). CHECK SEMANAL rápido:
faz UMA busca no ano mais recente (CAIXA_ANO) e lê só as primeiras MAX_PAGES
páginas — os certames novos ficam no topo da tabela; a dedup por row_hash no
bronze garante que só entram os inéditos. O filtro de valor (>=R$100k) é aplicado
pelo próprio site. NÃO baixa anexos (o SICVE não expõe PDF na tabela/detalhe).

O enriquecimento (valor/data via página de detalhe) é caro — refaz uma busca por
certame — e fica DESLIGADO por padrão (CAIXA_MAX_ENRICH=0).
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.shared.connections import get_selenium_command_executor

FONTE = "CAIXA SICVE - Sistema de Compras Eletronicas"
URL = "https://licitacoes.caixa.gov.br/sicve-web/public/view/portal/resultado.jsf"
DETALHE_URL = "https://licitacoes.caixa.gov.br/sicve-web/public/view/portal/detalhamento.jsf"
# Link POR ITEM do certame. Abre por GET em sessão nova (redireciona p/ detalhamento.jsf
# e renderiza o certame certo) — é o link permanente que faltava. O id do lote já vem
# na LISTAGEM, no div que embrulha o nº do certame (id="cm_<comprador>_cr_<certame>_lt_<lote>"),
# então não custa nenhuma requisição extra. Ver DECISOES.md §21.
VISUALIZAR_URL = "https://licitacoes.caixa.gov.br/sicve-web/public/visualizar.jsf?lote={}"
VALOR_MIN_FMT = "100000,00"
# Check SEMANAL: só o ano mais recente e poucas páginas (os certames novos ficam
# no topo da tabela). A dedup por row_hash no bronze garante que só entram os inéditos.
ANO_RECENTE = os.environ.get("CAIXA_ANO", max(ANOS_ALVO))
MAX_PAGES = int(os.environ.get("CAIXA_MAX_PAGES", "3"))
# Enriquecimento (valor/data via detalhe): o SICVE NÃO expõe a data na listagem,
# só no detalhe ("Data de Publicação no DOU"). Custa uma re-busca Selenium por
# certame, então enriquecemos SÓ os certames ainda não coletados (ver collect_caixa).
# MAX_ENRICH é um teto de segurança para o backlog inicial.
MAX_ENRICH = int(os.environ.get("CAIXA_MAX_ENRICH", "80"))
TB_SEL = '[id="tableVisitante:tb"] tr'


def _driver():
    o = Options()
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-blink-features=AutomationControlled")
    return webdriver.Remote(command_executor=get_selenium_command_executor("/wd/hub"), options=o)


def _wait_rows(d, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        n = d.execute_script(f"return document.querySelectorAll('{TB_SEL}').length;")
        if n and n > 0:
            return n
        time.sleep(0.5)
    return d.execute_script(f"return document.querySelectorAll('{TB_SEL}').length;")


def _open_and_search(d, numero=None, ano=None):
    d.get(URL)
    time.sleep(5)  # anti-bot Radware precisa assentar
    d.execute_script("arguments[0].click();", d.find_element(By.ID, "comprador:0"))
    time.sleep(1)

    def setf(name, val):
        d.execute_script(
            "var e=document.getElementsByName(arguments[0])[0];if(!e)return;e.value=arguments[1];"
            "e.dispatchEvent(new Event('input',{bubbles:true}));"
            "e.dispatchEvent(new Event('change',{bubbles:true}));"
            "e.dispatchEvent(new Event('blur',{bubbles:true}));", name, val)

    setf("deValorEstimadoDe", VALOR_MIN_FMT)
    if numero:
        setf("deNumeroCertame", numero)
    if ano:
        setf("deAnoCertame", ano)
    time.sleep(0.5)
    d.execute_script("arguments[0].click();", d.find_element(By.NAME, "pesquisaCertame"))
    time.sleep(4)
    return _wait_rows(d)


def _scrape_page(d):
    return d.execute_script(r"""
      var out=[];
      document.querySelectorAll('#tableVisitante\\:tb tr').forEach(function(tr){
        var tds=tr.querySelectorAll('td'); if(tds.length<7) return;
        var a=tr.querySelector('a[id$="numeroCertameFormatado"]');
        var descSpan=tds[4].querySelector('span[title]');
        // id do lote (link permanente do item) — div id="cm_<comprador>_cr_<certame>_lt_<lote>"
        var div=tr.querySelector('div[id^="cm_"]');
        var mLote=div?/_lt_(\d+)$/.exec(div.id):null;
        out.push({comprador:tds[0].textContent.trim(), modalidade:tds[1].textContent.trim(),
          numero_certame:(a?a.textContent.trim():tds[2].textContent.trim()),
          numero_item:tds[3].textContent.trim(),
          lote:(mLote?mLote[1]:''),
          descricao_item:(descSpan?(descSpan.getAttribute('title')||descSpan.textContent):tds[4].textContent).trim(),
          situacao:tds[6].textContent.trim()});
      });
      return out;""")


def _next_page(d):
    try:
        nxt = d.find_element(By.ID, "ds_tableVisitante_ds_next")
    except Exception:
        return False
    if "dis" in (nxt.get_attribute("class") or ""):
        return False
    before = d.execute_script(
        "var a=document.querySelector('#tableVisitante\\\\:tb a[id$=numeroCertameFormatado]');"
        "return a?a.textContent.trim():'';")
    d.execute_script("arguments[0].click();", nxt)
    end = time.time() + 15
    while time.time() < end:
        time.sleep(0.6)
        after = d.execute_script(
            "var a=document.querySelector('#tableVisitante\\\\:tb a[id$=numeroCertameFormatado]');"
            "return a?a.textContent.trim():'';")
        if after and after != before:
            return True
    return False


def _read_detail(d):
    return d.execute_script(r"""
      var r={};
      document.querySelectorAll('label').forEach(function(l){
        var s=l.nextElementSibling;
        if(s){var k=l.textContent.trim().replace(/:$/,'');if(!(k in r)) r[k]=s.textContent.trim();}});
      return r;""")


def _parse_valor(txt):
    m = re.search(r"[\d\.]+,\d{2}", txt or "")
    if not m:
        return ""
    try:
        return float(m.group(0).replace(".", "").replace(",", "."))
    except ValueError:
        return ""


def _certames_no_bronze() -> set[str]:
    """Números de certame CAIXA já presentes no bronze (p/ pular no enriquecimento).

    Falha graciosa: se o banco não responder, retorna vazio (enriquece tudo).
    """
    try:
        from sqlalchemy import text
        from industry.licitacoes import db
        with db.get_engine().connect() as c:
            rows = c.execute(text(
                "SELECT DISTINCT numero FROM licitacoes.bronze WHERE fonte ILIKE 'CAIXA%'"
            )).fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception as e:
        print(f"[caixa] não consegui ler certames do bronze ({str(e)[:50]}) — enriquecendo tudo")
        return set()


def collect_caixa(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    d = _driver()
    linhas = []
    try:
        # 1 busca, só o ano mais recente, poucas páginas (os certames novos no topo)
        _open_and_search(d, ano=ANO_RECENTE)
        page = 1
        while True:
            batch = [b for b in _scrape_page(d) if b.get("comprador")]
            linhas.extend(batch)
            if page >= MAX_PAGES or not _next_page(d):
                break
            page += 1

        # enriquecimento (valor/data via detalhe): só certames AINDA NÃO no bronze,
        # para manter barato — em regime, poucos por semana.
        detalhe = {}
        if MAX_ENRICH > 0:
            ja = _certames_no_bronze()
            certames = [nc for nc in dict.fromkeys(
                r["numero_certame"] for r in linhas if r.get("numero_certame")) if nc not in ja]
            print(f"[caixa] enriquecendo {min(len(certames), MAX_ENRICH)} certames novos "
                  f"(de {len(certames)} não vistos; {len(ja)} já no bronze)")
            for nc in certames[:MAX_ENRICH]:
                m = re.match(r"(\d+)\s*/\s*(\d+)", nc)
                if not m:
                    continue
                try:
                    if not _open_and_search(d, numero=m.group(1), ano=m.group(2)):
                        continue
                    d.execute_script("arguments[0].click();",
                                     d.find_element(By.ID, "tableVisitante:0:numeroCertameFormatado"))
                    time.sleep(5)
                    det = _read_detail(d)
                    detalhe[nc] = {
                        "valor": _parse_valor(det.get("Valor Estimado", "")),
                        "data": det.get("Data de Publicação no DOU", ""),
                        "objeto": det.get("Descrição Resumida do Objeto", ""),
                    }
                except Exception as e:
                    print(f"[caixa] enrich {nc} falhou: {str(e)[:50]}")
    finally:
        d.quit()

    rows = []
    for r in linhas:
        nc = r.get("numero_certame", "")
        det = detalhe.get(nc, {})
        lote = str(r.get("lote") or "")
        rows.append({
            "id": f"caixa-{nc}-item{r.get('numero_item','')}", "fonte": FONTE,
            "tipo": r.get("modalidade", ""),
            "titulo": r.get("descricao_item", ""),
            # Prefixo [nº do certame] no objeto: mantido de §14 (ajuda a achar por
            # Ctrl+F e é o que já está gravado no histórico).
            "objeto": (f"[{nc}] " + (det.get("objeto", "") or r.get("descricao_item", ""))
                       if nc else (det.get("objeto", "") or r.get("descricao_item", ""))),
            # Link permanente por ITEM quando temos o lote; senão cai na página de
            # resultados (pesquisável), como era antes. Ver DECISOES.md §21.
            "link": VISUALIZAR_URL.format(lote) if lote else URL,
            "data": det.get("data", ""), "valor": det.get("valor", ""),
            "numero": nc, "numero_certame": nc, "numero_item": r.get("numero_item", ""),
            "lote": lote,
            "situacao": r.get("situacao", ""), "ano": ANO_RECENTE,
        })
    df = pd.DataFrame(rows)
    out = dest_dir / "caixa_sicve.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"CAIXA SICVE: {len(df)} itens -> {out}")
    return out


if __name__ == "__main__":
    print(collect_caixa())
