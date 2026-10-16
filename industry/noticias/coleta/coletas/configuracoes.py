from dataclasses import dataclass
from typing import List



@dataclass
class Jornal:
    fonte: str
    setor: str
    url: str


@dataclass
class JornalXML(Jornal):
    article_selector: str
    # Padrões preservam o comportamento dos sitemaps gov.br (corpo em
    # #content-core [property="rnews:articleBody"], só as 30 primeiras URLs).
    content_selector: str | None = None
    max_urls: int = 30
    # Só URLs publicadas nas últimas N horas (news sitemap cobre dias).
    janela_horas: int | None = None
    excluded_path_parts: List[str] | None = None
    # Se informado, só URLs com algum destes segmentos no caminho (news
    # sitemap do site inteiro, ex.: CNN, filtrado para a editoria).
    incluir_path_parts: List[str] | None = None
    # Não baixa de novo link já gravado em noticias_raw para esta fonte.
    pular_existentes: bool = False
    headers: dict | None = None
    # Página da matéria bloqueada (anti-robô): grava o título como texto, sem
    # baixar a página. A classificação usa só o título.
    texto_do_titulo: bool = False
    # Quando url é um índice de sitemaps (<sitemapindex>), quantos ler.
    max_sitemaps: int = 20


@dataclass
class JornalSublinks(Jornal):
    article_selector: str
    headers: dict
    title_link_selector: str
    fonte: str
    setor: str
    content_selector: str
    data_selector: str | None = None
    content_iframe_selector: str | None = None
    content_iframe_srcdoc: bool = False
    use_urljoin: bool = False
    timeout: int = 300
    sleep_seconds: float = 0.5
    article_is_link: bool = False
    data_regex: str | None = None
    # Quando o <a> do card não tem texto (ex.: link "stretched" sobre o card),
    # o título vem deste seletor, relativo ao article_selector.
    title_selector: str | None = None


@dataclass
class JornalSelenium(Jornal):
    url: str
    link_selector: str
    title_selector: str
    date_selector: str
    text_selector: str
    excluded_path_parts: List[str]
    min_section_length: int = 26
    page_sleep: float = 1.0
    article_sleep: float = 1.5


@dataclass
class JornalRSS(Jornal):
    headers: dict
    content_selector: str | None = None
    data_selector: str | None = None
    timeout: int = 30
    sleep_seconds: float = 1.0
    # True quando o summary do feed é só o lead (ex.: CartaCapital, ~150
    # chars): busca o texto na página do artigo mesmo com summary > 100 chars.
    texto_da_pagina: bool = False
    # Sem acesso ao texto (ex.: Google News, que só traz título e fonte).
    texto_do_titulo: bool = False


@dataclass
class JornalPloneAPI(Jornal):
    """
    Sites gov.br migrados para Plone 6/Volto: o HTML vem sem corpo nem lista
    (renderizados no navegador), mas a API REST (++api++) devolve tudo em JSON.
    ``url`` é o endpoint @search da pasta de notícias, ex.:
    https://www.gov.br/sudene/++api++/pt-br/assuntos/imprensa/@search
    """

    headers: dict | None = None
    max_itens: int = 30
    timeout: int = 30
    sleep_seconds: float = 0.5

