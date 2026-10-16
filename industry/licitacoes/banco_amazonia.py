"""Coletor Banco da Amazônia — licitações (Selenium remoto).

A página (Next.js) tem accordions por ano com markups diferentes; usamos um
extrator PAGE-WIDE (segmenta o conteúdo por título) que funciona nos dois. URLs
de documento vão em `documentos` (JSON) para o anexos.py.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.shared.connections import get_selenium_command_executor

FONTE = "Banco da Amazônia - Licitações"
# Site trocou a seção de /o-banco/licitacoes/ para /acesso-informacao/licitacoes-e-contratos/
# (achado: URL antiga cai em página 404 própria do Next.js, que responde HTTP 200 mas
# não tem nenhum item — por isso o coletor vinha voltando 0 resultados). Markup
# (div.accordion > button, h3.text-variant-title) é o mesmo na URL nova.
BASE_LIC = "https://www.bancoamazonia.com.br/acesso-informacao/licitacoes-e-contratos/"
TIPOS = {
    "Pregão Eletrônico": "pregao-eletronico",
    "Concorrência Eletrônica": "concorrencia-eletronica",
    "Credenciamento": "credenciamento",
    "Pré-Qualificação": "pre-qualificacao",
    "Procedimento Regular de Licitação": "procedimento-regular-licitacao",
    "Chamamento Público": "chamamento-publico",
    "Leilão": "leilao",
}
JS_EXTRACT = r"""
var anos = arguments[0];
var NUMANO = /(\d{1,5}\s*\/\s*20\d{2}|20\d{2}\s*\/\s*\d{1,5})/;
var out = [];
var root = document.querySelector('main') || document.body;
var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT, null, false);
var cur = null;
while (walker.nextNode()) {
  var el = walker.currentNode;
  var txt = (el.innerText || '').trim();
  var isH3 = el.matches && el.matches('h3.text-variant-title') && NUMANO.test(txt);
  var isStrong = el.tagName === 'STRONG' && txt.length < 90 && NUMANO.test(txt);
  var firstLine = txt.split('\n')[0].trim();
  var isBtn = el.tagName === 'BUTTON' && firstLine.length < 90 && NUMANO.test(firstLine);
  if (isH3 || isStrong || isBtn) {
    var ttl = isBtn ? firstLine : txt;
    var m = ttl.match(/(20\d{2})/);
    cur = {ano: m ? m[1] : '', titulo: ttl, texto: '', links: []};
    out.push(cur);
  } else if (cur) {
    if (el.tagName === 'A' && el.href) cur.links.push({label: txt.slice(0,80), url: el.href});
    if (el.children.length === 0 && txt) cur.texto += txt + ' \n';
  }
}
return out.filter(function(o){ return anos.indexOf(o.ano) >= 0; });
"""


def _driver():
    o = Options()
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    return webdriver.Remote(command_executor=get_selenium_command_executor("/wd/hub"), options=o)


def collect_banco_amazonia(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    anos = sorted(ANOS_ALVO)
    d = _driver()
    itens = []
    try:
        for tipo, slug in TIPOS.items():
            try:
                d.get(BASE_LIC + slug)
                time.sleep(7)
            except Exception as e:
                print(f"[amazonia] {tipo} falhou: {str(e)[:50]}")
                continue
            for b in d.find_elements(By.CSS_SELECTOR, "div.accordion > button"):
                m = re.search(r"20\d{2}", b.text)
                if not m or m.group(0) not in ANOS_ALVO:
                    continue
                try:
                    d.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.4)
                    b.click()
                    time.sleep(1.2)
                except Exception:
                    pass
            for it in d.execute_script(JS_EXTRACT, anos):
                itens.append((it, tipo))
    finally:
        d.quit()

    rows, vistos = [], set()
    for it, tipo in itens:
        mnum = re.search(r"(\d{2,5})\s*/\s*(20\d{2})|(20\d{2})\s*/\s*(\d{2,5})", it["titulo"])
        if not mnum:
            continue
        numero = mnum.group(0).replace(" ", "")
        chave = (tipo, it["ano"], numero)
        if chave in vistos:
            continue
        vistos.add(chave)
        texto = it.get("texto", "")
        mo = re.search(r"Objeto[:\s]*(.+?)(?:Data e hora|Data:|$)", texto, re.IGNORECASE | re.DOTALL)
        objeto = re.sub(r"\s+", " ", (mo.group(1) if mo else texto)).strip()[:1500]
        md = re.search(r"sess[aã]o[:\s]*([0-3]?\d/[0-1]?\d/20\d{2})", texto, re.IGNORECASE)
        docs = [{"label": l["label"], "url": l["url"]} for l in it["links"]
                if l["url"].lower().split("?")[0].endswith((".pdf", ".zip", ".doc", ".docx"))]
        rows.append({
            "id": f"amazonia-{tipo[:4]}-{numero}", "fonte": FONTE, "tipo": tipo,
            "titulo": it["titulo"], "objeto": objeto, "link": BASE_LIC + TIPOS[tipo],
            "data": md.group(1) if md else "", "numero": numero, "ano": it["ano"],
            "documentos": json.dumps(docs, ensure_ascii=False) if docs else "",
        })
    df = pd.DataFrame(rows)
    out = dest_dir / "banco_amazonia.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Banco da Amazônia: {len(df)} itens -> {out}")
    return out


if __name__ == "__main__":
    print(collect_banco_amazonia())
