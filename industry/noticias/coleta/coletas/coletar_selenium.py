import pandas as pd
import time

from urllib.parse import urlparse
from datetime import date

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options

from .configuracoes import JornalSelenium
from . import saude
from industry.shared.connections import (
    NOTICIAS_PG,
    get_sqlalchemy_engine,
    get_selenium_command_executor,
)


PADRAO_BLOQUEIO = (
    r"you have been blocked|just a moment|access denied|attention required"
    r"|are you a robot|verify you are human"
)


def _txt(el) -> str:
    """Texto visível do elemento, com fallback para textContent.

    Em modo headless, alguns sites (ex.: Bloomberg Línea) renderizam os
    parágrafos do corpo de forma que o Selenium os considera 'não visíveis',
    fazendo `.text` retornar vazio embora o conteúdo exista no DOM. Nesses
    casos lemos o textContent. Para as fontes em que `.text` já funciona o
    comportamento não muda (só caímos no fallback quando `.text` vem vazio).
    """
    t = (el.text or "").strip()
    if not t:
        t = (el.get_attribute("textContent") or "").strip()
    return t


def coletar_selenium(config: JornalSelenium, engine=None):
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Remote(
        command_executor=get_selenium_command_executor(),
        options=options,
    )
    driver.set_page_load_timeout(60)
    driver.get(config.url)
    driver.implicitly_wait(2)
    time.sleep(config.page_sleep)

    elements = driver.find_elements(By.CSS_SELECTOR, config.link_selector)

    def is_news_article(url: str) -> bool:
        path = urlparse(url).path.strip("/")
        parts = path.split("/")
        if any(excl in parts for excl in config.excluded_path_parts):
            return False
        return any(len(part) > config.min_section_length for part in parts)

    seen = set()
    news_links = []

    for el in elements:
        url = el.get_attribute("href")
        if url and is_news_article(url) and url not in seen:
            seen.add(url)
            news_links.append(url)

    saude.links(len(news_links))
    rows = []

    for url in news_links:
        try:
            driver.get(url)
            time.sleep(config.article_sleep)

            try:
                title_el = driver.find_element(By.CSS_SELECTOR, config.title_selector)
                # .text vem vazio quando o h1 é "invisível" no headless (SaMais).
                title = _txt(title_el)
            except NoSuchElementException:
                title = None

            try:
                date_el = driver.find_element(By.CSS_SELECTOR, config.date_selector)
                article_date = date_el.get_attribute("datetime") or date_el.text
            except NoSuchElementException:
                article_date = None

            paragraphs = driver.find_elements(By.CSS_SELECTOR, config.text_selector)
            text = "\n".join(t for p in paragraphs if (t := _txt(p)))

            # Fallback: vários sites renderizam o corpo em containers sem classe
            # semântica (divs com classes ofuscadas/dinâmicas), então o
            # text_selector configurado casa zero parágrafos e o artigo era
            # descartado silenciosamente. Tenta seletores genéricos antes de
            # desistir, exigindo um mínimo de conteúdo para evitar boilerplate.
            if not text:
                for fb in ("article p", "main p", "p"):
                    paragraphs = driver.find_elements(By.CSS_SELECTOR, fb)
                    candidato = "\n".join(
                        t for p in paragraphs if len(t := _txt(p)) > 40
                    )
                    if len(candidato) > 200:
                        text = candidato
                        break

            rows.append(
                {
                    "link": url,
                    "titulo": title,
                    "data": article_date,
                    "texto": text,
                    "setor": config.setor,
                    "fonte": config.fonte,
                }
            )
        except Exception as e:
            print(f"  [AVISO] Erro ao coletar {url}: {e}")
            continue

    driver.quit()

    saude.artigos(rows)
    df = pd.DataFrame(
        rows, columns=["titulo", "link", "data", "texto", "fonte", "setor"]
    )

    if df.empty:
        return None

    # Exige apenas texto, como os demais coletores (sublinks/rss). Data ausente
    # é resolvida adiante por normalizar_data(), que assume a data de hoje quando
    # vazia. Exigir data aqui descartava artigos válidos de sites que não expõem
    # data estruturada (causa de várias fontes retornarem None silenciosamente).
    df = df.dropna(subset=["texto"])
    df = df[df["texto"].astype(str).str.strip() != ""]

    # Descarta linhas sem título: em vários sites o link_selector deixa passar
    # páginas que não são notícia (landings/especiais/seção) sem <h1>, em que
    # title_selector retorna None mas ainda há <p>. Antes essas linhas eram
    # gravadas com titulo "None" (lixo) e quebravam a deduplicação por título.
    df = df.dropna(subset=["titulo"])
    df = df[df["titulo"].astype(str).str.strip() != ""]

    # Página de bloqueio anti-robô (Cloudflare/DataDome) tem título e <p>, e
    # entraria como notícia: Brasil Energia devolveu 60 "Sorry, you have been
    # blocked" num teste de 07/10/2026.
    bloqueio = df["titulo"].astype(str).str.contains(saude.PADRAO_BLOQUEIO, case=False, regex=True)
    if bloqueio.any():
        print(f"  [AVISO] {config.fonte}: {int(bloqueio.sum())} páginas de bloqueio descartadas")
    df = df[~bloqueio]

    if df.empty:
        return None

    df["data_coleta"] = date.today()

    with engine.begin() as conn:
        df.to_sql(
            name="noticias_raw",
            schema="noticias",
            con=conn,
            if_exists="append",
            index=False,
        )

    return df
