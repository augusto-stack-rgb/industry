import os
import random

from industry.noticias.coleta.coletas.configuracoes import (
    JornalXML,
    JornalSublinks,
    JornalSelenium,
    JornalRSS,
    JornalPloneAPI,
)


FRASES_REMOVER_TEXTO = [
    r"Este resumo foi útil\? Resumo gerado por ferramenta de IA treinada pela redação da Editora Abril\.",
]

JORNAIS = [
    JornalSublinks(
        url="https://braziljournal.com/tag/setor-eletrico/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="article.boxarticle",
        title_link_selector="h2.boxarticle-infos-title a",
        data_selector="time.post-time",
        content_selector="div.post-content-text",
        fonte="Brazil_Journal_Eletrico",
        use_urljoin=True,
        setor="Óleo e Gás",
    ),
    JornalSublinks(
        url="https://techcrunch.com/latest/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="li.wp-block-post",
        title_link_selector="a.loop-card__title-link",
        data_selector=".wp-block-post-date time",
        content_selector=".entry-content",
        fonte="TechCrunch",
        setor="Tecnologia",
        use_urljoin=False,
        sleep_seconds=random.uniform(3.5, 6.5),
    ),
    JornalSublinks(
        url="https://globorural.globo.com/ultimas-noticias/index/feed/pagina-1",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
        article_selector="article.feed-post-body, div.feed-post-body",
        title_link_selector="a",
        data_selector="p.content-publication-data__updated time[itemprop='datePublished']",
        content_selector="div#mc-article-body article",
        fonte="Globo_Rural",
        setor="Agro",
        use_urljoin=True,
    ),
    JornalSublinks(
        url="https://www.infomoney.com.br/tudo-sobre/mercado-imobiliario/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector='div[data-ds-component="card-sm"]',
        title_link_selector="h2 a",
        data_selector="p.im-mob-core-description time:first-of-type",
        content_selector="article.im-article",
        fonte="InfoMoney_Imobiliario",
        setor="Real Estate",
        use_urljoin=False,
        sleep_seconds=1,
    ),
    JornalSublinks(
        url="https://www.noticiasagricolas.com.br/conteudo/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="li.horizontal.com-hora",
        title_link_selector="a",
        data_selector="div.datas",
        content_selector="div.materia",
        fonte="Noticias_Agricolas",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=0.5,
    ),
    JornalSublinks(
        url="https://quatrorodas.abril.com.br/tudo-sobre/negocios/",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
        article_selector="div.card.list-item",
        title_link_selector="a:has(h2.title)",
        data_selector="span.date-post",
        content_selector="section.content.readme-audima",
        fonte="Quatro_Rodas_Negocios",
        setor="Automotivo",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=0.5,
    ),
    JornalSublinks(
        url="https://quatrorodas.abril.com.br/ultimas-noticias/",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        },
        article_selector="div.card.list-item",
        title_link_selector="a:has(h2.title)",
        data_selector="span.date-post",
        content_selector="section.content.readme-audima",
        fonte="Quatro_Rodas_Noticias",
        setor="Automotivo",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=0.5,
    ),
    JornalSublinks(
        url="https://www.agrolink.com.br/culturas/soja/noticia/lista",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="ul.news-list-main-thumbs li.news-list-main-thumbs-item",
        title_link_selector="a.news-list-main-link-title",
        data_selector="time[itemprop*='datePublished']",
        content_selector="div.section-description",
        fonte="agrolink_soja",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=5,
    ),
    JornalSublinks(
        url="https://www.agrolink.com.br/culturas/milho/noticia/lista",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="ul.news-list-main-thumbs li.news-list-main-thumbs-item",
        title_link_selector="a.news-list-main-link-title",
        data_selector="time[itemprop*='datePublished']",
        content_selector="div.section-description",
        fonte="agrolink_milho",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=5,
    ),
    JornalSublinks(
        url="https://www.agrolink.com.br/culturas/trigo/noticia/lista",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="ul.news-list-main-thumbs li.news-list-main-thumbs-item",
        title_link_selector="a.news-list-main-link-title",
        data_selector="time[itemprop*='datePublished']",
        content_selector="div.section-description",
        fonte="agrolink_trigo",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=5,
    ),
    JornalSublinks(
        url="https://www.agrolink.com.br/culturas/arroz/noticia/lista",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="ul.news-list-main-thumbs li.news-list-main-thumbs-item",
        title_link_selector="a.news-list-main-link-title",
        data_selector="time[itemprop*='datePublished']",
        content_selector="div.section-description",
        fonte="agrolink_arroz",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=5,
    ),
    JornalSublinks(
        url="https://www.gov.br/saude/pt-br/assuntos/noticias",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="article.tileItem",
        title_link_selector="h2.tileHeadline a",
        data_selector="span.documentPublished span.value",
        content_selector="div[property='rnews:articleBody']",
        fonte="ministerio_saude",
        setor="Saúde",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=0.5,
    ),
    JornalSublinks(
        url="https://wmo.int/news",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.views-row",
        title_link_selector="a",
        data_selector="div.text-sm div.font-normal",
        content_selector="div.field--name-body",
        fonte="WMO",
        setor="Agro",
        use_urljoin=True,
        timeout=20,
        sleep_seconds=0.5,
    ),
    # --- Agro via RSS ---
    JornalRSS(
        url="https://www.canalrural.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=None,  # articles behind Cloudflare; RSS summaries (500-800 chars) are sufficient
        fonte="Canal_Rural",
        setor="Agro",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://agrorevenda.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.elementor-widget-theme-post-content",
        fonte="Agrorevenda",
        setor="Agro",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.portaldoagronegocio.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.contentFull",
        fonte="Portal_do_Agronegocio",
        setor="Agro",
        sleep_seconds=1.0,
    ),
    JornalSublinks(
        # Site foi refeito em Elementor/WordPress; URL de listagem antiga
        # (/ultimas-noticias/) e seletores antigos (iframe de conteúdo
        # incluso) não existem mais. Novo layout expõe o texto direto,
        # sem iframe.
        url="https://www.autodata.com.br/noticias/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="article.elementor-post",
        title_link_selector="h3.elementor-post__title a",
        data_selector="span.elementor-post-info__item--type-date",
        content_selector="div.elementor-widget-theme-post-content",
        fonte="Autodata",
        setor="Automotivo",
        use_urljoin=False,
        sleep_seconds=0.5,
        timeout=30,
    ),
    JornalSublinks(
        url="https://braziljournal.com/tecnologia/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="article",
        title_link_selector="h2 a",
        data_selector="time.post-time",
        content_selector="div.post-content-text",
        fonte="Brazil_Journal_Tecnologia",
        use_urljoin=True,
        sleep_seconds=1,
        timeout=20,
        setor="Tecnologia",
    ),
    JornalSublinks(
        url="https://braziljournal.com/hot-topic/bancos-fintechs/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="ul#allnews > li article.boxarticle",
        title_link_selector="h2.boxarticle-infos-title a",
        data_selector="time.post-time",
        content_selector="div.post-content-text",
        fonte="Brazil_Journal_Fintechs",
        use_urljoin=True,
        sleep_seconds=1,
        timeout=20,
        setor="Financial Services",
    ),
    JornalXML(
        url="https://www.gov.br/mme/pt-br/assuntos/noticias/sitemap.xml",
        fonte="MME",
        setor="Óleo e Gás",
        article_selector={"name": "div", "attrs": {"property": "rnews:articleBody"}},
    ),
    JornalXML(
        url="https://www.gov.br/aneel/pt-br/assuntos/noticias/sitemap.xml",
        fonte="aneel",
        setor="Óleo e Gás",
        article_selector={"name": "div", "attrs": {"property": "rnews:articleBody"}},
    ),
    JornalXML(
        url="https://www.gov.br/sudam/pt-br/noticias-1/sitemap.xml",
        fonte="sudam",
        setor="Indústria",
        article_selector={"name": "div", "attrs": {"property": "rnews:articleBody"}},
    ),
    # SUDENE migrou para Plone 6/Volto (out/2026): /assuntos/noticias passou a
    # dar 401 e o sitemap 404 — a fonte nunca tinha gravado nada. As notícias
    # estão em /assuntos/imprensa, lidas pela API REST.
    JornalPloneAPI(
        url="https://www.gov.br/sudene/++api++/pt-br/assuntos/imprensa/@search",
        fonte="sudene",
        setor="Indústria",
    ),
    JornalXML(
        url="https://www.gov.br/mdic/pt-br/assuntos/noticias/sitemap.xml",
        fonte="mdic",
        setor="Indústria",
        article_selector={"name": "div", "attrs": {"property": "rnews:articleBody"}},
    ),
    JornalSelenium(
        url="https://www.cnnbrasil.com.br/tudo-sobre/petroleo/",
        link_selector="a[href^='https://www.cnnbrasil.com.br/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p",
        excluded_path_parts=["pagina", "entretenimento", "nacional"],
        min_section_length=40,
        fonte="cnn_petroleo",
        setor="Óleo e Gás",
    ),
    JornalSelenium(
        url="https://www.cnnbrasil.com.br/saude/",
        link_selector="a[href^='https://www.cnnbrasil.com.br/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p",
        excluded_path_parts=["pagina", "entretenimento", "nacional"],
        min_section_length=40,
        fonte="cnn_saude",
        setor="Saúde",
    ),
    # InfoMoney: era Selenium na home 2x/dia — pegava só o que estava na capa
    # naquela hora (~1/3 das ~150 matérias/dia; perdeu "ANP multa distribuidoras
    # em mais de R$ 700 mi", 28/09) e ainda links de rodapé. O news sitemap traz
    # todas as matérias dos últimos 2 dias com título e data.
    JornalXML(
        url="https://www.infomoney.com.br/news-sitemap.xml",
        article_selector="",
        content_selector="article.im-article",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        # live = posts do blog ao vivo (centenas/dia); política já era excluída.
        excluded_path_parts=["live", "web-stories", "esportes", "politica", "perfil"],
        janela_horas=36,
        pular_existentes=True,
        max_urls=300,
        fonte="infomoney",
        setor="Economia",
    ),
    JornalSelenium(
        url="https://forbes.com.br/forbesagro/",
        # Selector amplo (qualquer link forbes.com.br) pegava todo o menu/
        # rodapé do site — ~120 páginas por run, estourando os 15 min de
        # execution_timeout. Escopado como o Forbes_Money abaixo.
        link_selector="a[href^='https://forbes.com.br/forbesagro/']",
        title_selector="h1",
        date_selector="time[datetime], span.time time",
        text_selector="article p",
        excluded_path_parts=["tag", "autor", "videos", "newsletter"],
        min_section_length=26,
        setor="Agro",
        fonte="forbes",
    ),
    JornalSelenium(
        url="https://exame.com/mercado-imobiliario/",
        link_selector="a[href^='https://exame.com/mercado-imobiliario/']",
        title_selector="#news-component h1",
        date_selector="p.body-small",
        text_selector="#news-body p",
        excluded_path_parts=["tag", "autor", "assine", "newsletter"],
        setor="Real Estate",
        fonte="exame_mi",
    ),
    # --- M&A via RSS ---
    JornalRSS(
        url="http://www.infomoney.com.br/tudo-sobre/ma/rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="article.im-article",
        fonte="InfoMoney_MnA",
        setor="M&A",
    ),
    # Tag de combustíveis do InfoMoney (~10 matérias/semana). O news sitemap da
    # fonte "infomoney" já cobre o site todo; esta entra como fonte de Óleo e Gás
    # dedicada. Summary do feed é só o lead: texto vem da página.
    JornalRSS(
        url="https://www.infomoney.com.br/tudo-sobre/combustiveis/rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="article.im-article",
        fonte="InfoMoney_Combustiveis",
        setor="Óleo e Gás",
        texto_da_pagina=True,
    ),
    JornalRSS(
        url=(
            "https://www.nytimes.com/svc/collections/v1/publish/"
            "http://www.nytimes.com/topic/subject/"
            "mergers-acquisitions-and-divestitures/rss.xml"
        ),
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="section[name='articleBody'], div.StoryBodyCompanionColumn",
        fonte="NYT_MnA",
        setor="M&A",
    ),
    JornalRSS(
        url="https://www.theguardian.com/business/mergers-and-acquisitions/rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div[data-gu-name='body'], div.article-body-commercial-selector",
        fonte="TheGuardian_MnA",
        setor="M&A",
    ),
    JornalRSS(
        url="https://braziljournal.com/negocios/rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.post-content-text",
        data_selector="time.post-time",
        fonte="BrazilJournal_MnA",
        setor="M&A",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://fusoesaquisicoes.com/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content, div.post-content",
        fonte="FusoesAquisicoes",
        setor="M&A",
    ),
    JornalRSS(
        url="https://feeds.bloomberg.com/markets/news.rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=None,  # artigos paywalled; texto extraído do resumo do feed
        fonte="Bloomberg",
        setor="M&A",
        sleep_seconds=0.5,
    ),
    # Reuters: as páginas têm bloqueio anti-robô (DataDome) — a fonte nunca
    # gravou nada via Selenium. O índice do news sitemap não é bloqueado e traz
    # ~2 dias de matérias com título e data; M&A hoje fica em legal/transactional.
    # Sem acesso ao texto: o título vai como texto.
    JornalXML(
        url="https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
        article_selector="",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        incluir_path_parts=["transactional"],
        texto_do_titulo=True,
        janela_horas=36,
        pular_existentes=True,
        max_urls=300,
        fonte="Reuters",
        setor="M&A",
    ),
    # --- Finanças ---
    JornalSublinks(
        url="https://estado.rs.gov.br/agencia-de-noticias",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.lista-publicacoes-modular__item",
        title_link_selector="h3.lista-publicacoes-modular__titulo a",
        data_selector="time[datetime]",
        content_selector="div.artigo__texto",
        fonte="Diario_Oficial_RS",
        setor="Economia",
        use_urljoin=True,
        sleep_seconds=0.5,
        timeout=30,
    ),
    JornalSelenium(
        url="https://exame.com/negocios/",
        link_selector="a[href^='https://exame.com/negocios/']",
        title_selector="#news-component h1",
        date_selector="p.body-small",
        text_selector="#news-body p",
        excluded_path_parts=["tag", "autor", "assine", "newsletter"],
        min_section_length=8,
        page_sleep=2.0,
        article_sleep=1.5,
        fonte="Exame_Negocios",
        setor="Economia",
    ),
    JornalRSS(
        url="https://www.moneytimes.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".entry-content, .td-post-content, article",
        fonte="Money_Times",
        setor="Economia",
        sleep_seconds=1.0,
    ),
    JornalSelenium(
        url="https://forbes.com.br/forbes-money/",
        link_selector="a[href^='https://forbes.com.br/forbes-money/']",
        title_selector="h1",
        date_selector="time[datetime], span.time time",
        text_selector="article p",
        excluded_path_parts=["tag", "autor", "videos", "newsletter"],
        min_section_length=26,
        page_sleep=2.0,
        article_sleep=1.5,
        fonte="Forbes_Money",
        setor="M&A",
    ),
    # --- Óleo e Gás via RSS ---
    JornalRSS(
        url="https://www.worldoil.com/rss?feed=news",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".content-body",
        fonte="World_Oil",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.offshore-energy.biz/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".entry-content, .post-content",
        fonte="Offshore_Energy",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://oilprice.com/rss/main",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".singleArticle__content",
        fonte="OilPrice",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://brazilenergyinsight.com/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".entry-content, .post-content",
        fonte="Brazil_Energy_Insight",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    # --- Óleo e Gás via Selenium ---
    # IEA: site e RSS atrás de Cloudflare (403/"Just a moment") — nunca gravou
    # nada. Google News indexa as publicações dele; só traz título e fonte, e
    # o link é o redirecionamento do Google (abre a matéria no navegador).
    JornalRSS(
        url=(
            "https://news.google.com/rss/search?q=site:iea.org+when:7d"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        headers={"User-Agent": "Mozilla/5.0"},
        texto_do_titulo=True,
        fonte="IEA",
        setor="Óleo e Gás",
    ),
    # Mesmo caminho da fonte "Reuters" (sitemap; página bloqueada).
    JornalXML(
        url="https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
        article_selector="",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        incluir_path_parts=["energy"],
        texto_do_titulo=True,
        janela_horas=36,
        pular_existentes=True,
        max_urls=300,
        fonte="Reuters_Energy",
        setor="Óleo e Gás",
    ),
    # --- Óleo e Gás via Sublinks ---
    JornalSublinks(
        url="https://agencia.petrobras.com.br/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="a.home-banner-card-link",
        title_link_selector="",
        content_selector="#main-content",
        data_regex=r"Postado em (\d{2}/\d{2}/\d{4})",
        fonte="Agencia_Petrobras",
        setor="Óleo e Gás",
        use_urljoin=True,
        sleep_seconds=0.8,
        timeout=30,
        article_is_link=True,
    ),
    JornalSublinks(
        url="https://www.gov.br/anp/pt-br/canais_atendimento/imprensa/noticias-comunicados",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector='#content-core a[href*="/noticias-comunicados/"]',
        title_link_selector="",
        content_selector='#content-core [property="rnews:articleBody"]',
        data_selector=".documentPublished .value",
        fonte="ANP",
        setor="Óleo e Gás",
        use_urljoin=True,
        sleep_seconds=0.8,
        timeout=30,
        article_is_link=True,
    ),
    # --- Economia ---
    JornalSublinks(
        # Site foi redesenhado (tema "npsd_*"); cards antigos
        # (category_single/medium_single) não existem mais.
        url="https://www.seudinheiro.com/economia/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.npsd_feed-ultimas-item",
        title_link_selector="a:has(h2)",
        data_selector="span.npsd_feed-ultimas-item-content-time",
        content_selector="div.npsd_single-main-content-notice",
        fonte="Seu_Dinheiro",
        setor="Economia",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    JornalRSS(
        url="https://www.decisorbrasil.com.br/category/negocios-transacoes/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".entry-content",
        fonte="Decisor_Brasil",
        setor="M&A",
        sleep_seconds=1.0,
    ),
    JornalSublinks(
        url="https://economiasc.com/noticias/startups/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.e-loop-item",
        title_link_selector="div.elementor-widget-theme-post-title a",
        data_selector="span.elementor-post-info__item--type-date",
        content_selector="div.elementor-widget-theme-post-content",
        fonte="EconomiaSC_Startups",
        setor="M&A",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    JornalSublinks(
        url="https://economiasc.com/noticias/economia/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.e-loop-item",
        title_link_selector="div.elementor-widget-theme-post-title a",
        data_selector="span.elementor-post-info__item--type-date",
        content_selector="div.elementor-widget-theme-post-content",
        fonte="EconomiaSC_Economia",
        setor="Economia",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    # --- Indústria via RSS ---
    JornalRSS(
        url="https://www.cimm.com.br/portal/noticia/rss",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-body",
        fonte="CIMM",
        setor="Indústria",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.industrianews.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="IndustriaNews",
        setor="Indústria",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://avozdaindustria.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.post-content",
        fonte="Voz_Industria",
        setor="Indústria",
        sleep_seconds=1.0,
    ),
    # --- Indústria via Sublinks ---
    JornalSublinks(
        url="https://revistamt.com.br/Noticias",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.noticia",
        title_link_selector="div.detalhesNoticia > a",
        data_regex=r"(\d{2}/\d{2}/\d{4})",
        content_selector="div#TextoNoticia",
        fonte="Revista_MT",
        setor="Indústria",
        use_urljoin=True,
        sleep_seconds=1.0,
        timeout=30,
    ),
    # --- Tecnologia via RSS ---
    JornalRSS(
        url="https://startups.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.single-content",
        fonte="Startups",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://itforum.com.br/feed",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        },
        content_selector="section.single-content div.content",
        fonte="IT_Forum",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://olhardigital.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.sng-cnt",
        fonte="Olhar_Digital",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.baguete.com.br/rss/noticias/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.article-body div.content-body, div.content-body",
        fonte="Baguete",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://mittechreview.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="MIT_Tech_Review_BR",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://santacatarina.builders/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="SC_Builders",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://brazileconomy.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Brazil_Economy",
        setor="Tecnologia",
        sleep_seconds=1.0,
    ),
    # --- Agência Brasil (RSS traz o texto completo no summary) ---
    JornalRSS(
        url="https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=None,
        fonte="Agencia_Brasil",
        setor="Economia",
        sleep_seconds=1.0,
    ),
    # --- CartaCapital (summary do RSS é só o lead; texto vem da página) ---
    JornalRSS(
        url="https://www.cartacapital.com.br/economia/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.content-closed",
        fonte="CartaCapital",
        setor="Economia",
        sleep_seconds=1.0,
        texto_da_pagina=True,
    ),
    # --- Liberal (Americana/SP): sem RSS; link do card é vazio, título no h2 ---
    JornalSublinks(
        url="https://liberal.com.br/brasil-e-mundo/economia/",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        article_selector="div.card",
        title_link_selector='a[href*="/brasil-e-mundo/economia/"]',
        title_selector="h2.card-title",
        content_selector="div.wysiwyg",
        # Data vem como "26 de setembro de 2026, às 08h41"; o ", às 08h41"
        # quebra o normalizar_data, então o regex captura só a data.
        data_regex=r"^(\d{1,2} de [a-zç]+ de \d{4}), às",
        fonte="Liberal",
        setor="Economia",
        timeout=30,
        sleep_seconds=1.0,
    ),
    # --- Financial Services via Selenium ---
    JornalSelenium(
        url="https://www.cnnbrasil.com.br/money/",
        link_selector="a[href^='https://www.cnnbrasil.com.br/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p",
        excluded_path_parts=["pagina", "entretenimento", "nacional", "branded-content"],
        min_section_length=40,
        fonte="CNN_Money",
        setor="Financial Services",
    ),
    # A página da editoria deixou de listar matérias (só links de seção) e a
    # fonte parou em 02/09/2026. O news sitemap traz as ~500 matérias do último
    # dia do site inteiro; filtra a editoria de economia.
    JornalXML(
        url="https://www.cnnbrasil.com.br/sitemap-news.xml",
        article_selector="",
        content_selector="article",
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        },
        incluir_path_parts=["economia"],
        janela_horas=36,
        pular_existentes=True,
        max_urls=150,
        fonte="CNN_Financas",
        setor="Financial Services",
    ),
    # --- Real Estate via Sublinks ---
    JornalSublinks(
        url="https://imobireport.com.br",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.post-card",
        title_link_selector="div.content a",
        data_selector="div.dates > div.published > span",
        content_selector="div.content-area",
        fonte="Imobi_Report",
        setor="Real Estate",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    JornalSublinks(
        url="https://revista.buildings.com.br/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.blog_grid_post_style",
        title_link_selector="h3.image-post-title a",
        data_selector="span.post-date",
        content_selector="div.post_content",
        fonte="Revista_Buildings",
        setor="Real Estate",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    JornalSublinks(
        # Site migrou para template Nuxt (mesma família da Brazil Journal);
        # cards perderam as classes semânticas antigas e o link do título
        # agora é relativo (precisa de urljoin).
        url="https://metroquadrado.com/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="article.boxarticle",
        title_link_selector="h2 a",
        data_selector="time",
        content_selector="div.flow-root",
        fonte="Metro_Quadrado",
        setor="Real Estate",
        use_urljoin=True,
        sleep_seconds=1.0,
        timeout=20,
    ),
    JornalSublinks(
        url="https://revistasacada.com.br/",
        headers={"User-Agent": "Mozilla/5.0"},
        article_selector="div.td_module_flex.td_module_wrap",
        title_link_selector="h3.entry-title a",
        data_selector="time.entry-date",
        content_selector="div.td-post-content",
        fonte="Revista_Sacada",
        setor="Real Estate",
        use_urljoin=False,
        sleep_seconds=1.0,
        timeout=20,
    ),
    # --- Real Estate via Selenium ---
    JornalSelenium(
        url="https://www.bloomberglinea.com.br/tags/real-estate/",
        link_selector="a[href*='bloomberglinea.com.br/'], a[href^='/']",
        title_selector="h1",
        date_selector="time[datetime]",
        text_selector="article p, .article-body p",
        excluded_path_parts=[
            "tags",
            "tag",
            "autores",
            "autor",
            "videos",
            "newsletter",
            "podcast",
            "especiais",
            "especiales",
        ],
        min_section_length=30,
        fonte="Bloomberg_Linea_RE",
        setor="Real Estate",
    ),
    # --- Finanças via RSS ---
    JornalRSS(
        url="https://neofeed.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="NeoFeed",
        setor="Financial Services",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://finsidersbrasil.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.single-content",
        fonte="Finsiders_Brasil",
        setor="Financial Services",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.suno.com.br/noticias/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=".newsContent__article",
        fonte="Suno",
        setor="Financial Services",
        sleep_seconds=1.0,
    ),
    # --- Varejo via RSS ---
    JornalRSS(
        url="https://mercadoeconsumo.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content, div.jeg_inner_content",
        fonte="Mercado_e_Consumo",
        setor="Varejo",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://centraldovarejo.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="#mvp-content-main",
        fonte="Central_do_Varejo",
        setor="Varejo",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://superhiper.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="#penci-post-entry-inner",
        fonte="SuperHiper",
        setor="Varejo",
        sleep_seconds=1.0,
    ),
    # Acelera_Varejo removida: o site virou plataforma de eventos (SPA) e o
    # feed não traz mais itens desde ago/2026.
    JornalRSS(
        url="https://cndl.org.br/varejosa/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="CNDL_Varejosa",
        setor="Varejo",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://consumidormoderno.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector=None,
        fonte="Consumidor_Moderno",
        setor="Varejo",
        sleep_seconds=1.0,
    ),
    # --- Saúde via RSS ---
    JornalRSS(
        url="https://saudedigitalnews.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Saude_Digital_News",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://setorsaude.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Setor_Saude",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://portalmedicinaesaude.com/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Portal_Medicina_e_Saude",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://futurodasaude.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Futuro_da_Saude",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://executivosdasaude.com.br/feed/",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        content_selector="div.entry-content",
        fonte="Executivos_da_Saude",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://panoramafarmaceutico.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Panorama_Farmaceutico",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://medicinasa.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Medicina_SA",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://portalhospitaisbrasil.com.br/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Portal_Hospitais_Brasil",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://www.saudebusiness.com/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Saude_Business",
        setor="Saúde",
        sleep_seconds=1.0,
    ),
    # --- Óleo e Gás via RSS ---
    JornalRSS(
        url="https://petronoticias.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Petronoticias",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://clickpetroleoegas.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Click_Petroleo_Gas",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://eixos.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Eixos",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://agenciainfra.com/blog/feed/",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Agencia_Infra",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    JornalRSS(
        url="https://portalpetrus.com.br/feed",
        headers={"User-Agent": "Mozilla/5.0"},
        content_selector="div.entry-content",
        fonte="Portal_Petrus",
        setor="Óleo e Gás",
        sleep_seconds=1.0,
    ),
    # Portos_e_Navios removido: domínio portosenavios.com.br está servindo
    # conteúdo de um e-commerce japonês (título genérico, produtos de
    # joias/moda) — parece domínio expirado/sequestrado, não é mais o site
    # de notícias original. Nenhum ajuste de selector resolve isso.
    # --- Varejo via Selenium (novos) ---
    JornalSelenium(
        url="https://www.supervarejo.com.br",
        link_selector="a[href^='https://www.supervarejo.com.br/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p, .post-content p",
        excluded_path_parts=["tag", "autor", "author", "page", "newsletter", "assine"],
        page_sleep=2.0,
        fonte="SuperVarejo",
        setor="Varejo",
    ),
    JornalSelenium(
        url="https://www.samais.com.br",
        link_selector="a[href*='/publicacoes/']",
        title_selector="h1",
        date_selector="time, .post-date, .entry-date",
        text_selector="article p, .entry-content p, .content p",
        excluded_path_parts=["tag", "autor", "author", "category"],
        page_sleep=3.0,
        article_sleep=2.0,
        fonte="SaMais",
        setor="Varejo",
    ),
    JornalSelenium(
        url="https://portalnovarejo.com.br",
        link_selector="a[href*='portalnovarejo.com.br/']",
        title_selector="h1",
        date_selector="time, .post-date, .entry-date",
        text_selector="article p, .entry-content p, .post-content p",
        excluded_path_parts=[
            "tag",
            "autor",
            "author",
            "page",
            "categoria",
            "category",
            "newsletter",
        ],
        page_sleep=3.0,
        article_sleep=2.0,
        fonte="Portal_Novarejo",
        setor="Varejo",
    ),
    # --- Óleo e Gás via Selenium (novos) ---
    JornalSelenium(
        url="https://www.tnpetroleo.com.br",
        link_selector="a[href*='/noticia/'], a[href*='/artigo/']",
        # Sem <h1> desde jun/2026: o título está num h3 (linhas descartadas
        # por falta de título, task "success" sem gravar).
        title_selector="h3.text-green, h1",
        date_selector="time, .date-pub, span.date",
        text_selector="article p, .article-content p, .post-content p",
        excluded_path_parts=["tag", "categoria", "autor", "author"],
        page_sleep=2.0,
        fonte="TNPetroleo",
        setor="Óleo e Gás",
    ),
    # Cloudflare bloqueia após poucos acessos ("Sorry, you have been blocked");
    # o seletor com www também não casa (home usa links relativos). Sem
    # conserto simples — ver PR.
    JornalSelenium(
        url="https://www.brasilenergia.com.br",
        link_selector="a[href^='https://www.brasilenergia.com.br/']",
        title_selector="h1",
        date_selector="time, .post-date, .entry-date",
        text_selector="article p, .entry-content p, .post-content p",
        excluded_path_parts=[
            "tag",
            "autor",
            "author",
            "page",
            "categoria",
            "category",
            "newsletter",
            "sobre",
        ],
        page_sleep=3.0,
        article_sleep=2.0,
        fonte="Brasil_Energia",
        setor="Óleo e Gás",
    ),
    # --- Agro via Selenium (novos) ---
    JornalSelenium(
        url="https://news.agrofy.com.br",
        link_selector="a[href*='/noticia/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p, .news-body p, .content p",
        excluded_path_parts=["tag", "categoria", "author", "autor", "category"],
        page_sleep=2.0,
        fonte="Agrofy",
        setor="Agro",
    ),
    JornalSelenium(
        url="https://agranjadigital.com.br",
        # Links mudaram de /artigo/ para /article/ (jul/2026).
        link_selector="a[href*='/article/']",
        title_selector="h1",
        date_selector="time, .article-date, .post-date",
        text_selector="article p, .article-content p, .content p",
        excluded_path_parts=[
            "busca",
            "colunistas",
            "revista",
            "sobre",
            "contato",
            "tag",
            "autor",
        ],
        page_sleep=8.0,
        article_sleep=3.0,
        fonte="A_Granja_Digital",
        setor="Agro",
    ),
    # --- Indústria via Selenium ---
    JornalSelenium(
        url="https://www.revistaferramental.com.br",
        link_selector="a[href*='revistaferramental.com.br/']",
        title_selector="h1",
        date_selector="time, .entry-date, .post-date",
        text_selector="article p, .entry-content p, .post-content p",
        excluded_path_parts=[
            "tag",
            "autor",
            "author",
            "category",
            "page",
            "sobre",
            "contato",
        ],
        page_sleep=5.0,
        article_sleep=3.0,
        fonte="Revista_Ferramental",
        setor="Indústria",
    ),
    # --- Economia via Selenium (novos) ---
    JornalSelenium(
        url="https://www.bloomberglinea.com.br",
        link_selector="a[href*='bloomberglinea.com.br/'], a[href^='/']",
        title_selector="h1",
        date_selector="time",
        text_selector="article p",
        excluded_path_parts=[
            "tags",
            "tag",
            "autores",
            "autor",
            "author",
            "videos",
            "newsletter",
            "podcast",
            "estilo-de-vida",
            "financas-pessoais",
            "busca",
            "especiais",
            "especiales",
        ],
        page_sleep=3.0,
        article_sleep=2.0,
        fonte="Bloomberg_Linea",
        setor="Economia",
    ),
]


# ── Setores ligados na coleta ─────────────────────────────────────────────────
# Só estes setores são coletados. As fontes dos demais (Economia, Saúde, M&A,
# Varejo, Financial Services, Real Estate) continuam configuradas em JORNAIS
# acima — para religar um setor, basta incluí-lo aqui.
SETORES_ATIVOS = {"Agro", "Óleo e Gás", "Automotivo", "Tecnologia", "Indústria"}


def jornais_ativos():
    """Fontes coletadas: as dos setores ligados."""
    return [j for j in JORNAIS if j.setor in SETORES_ATIVOS]
