"""Ponto de entrada de toda task de coleta: roda o coletor da fonte e grava a
saúde da execução (monitor.coleta_noticias), inclusive quando ele falha."""

import time

from . import saude
from .configuracoes import (
    JornalPloneAPI,
    JornalRSS,
    JornalSelenium,
    JornalSublinks,
    JornalXML,
)


def _coletor(config):
    # Import tardio: selenium/feedparser só carregam na task que precisa.
    if isinstance(config, JornalPloneAPI):
        from .coletar_plone_api import coletar_plone_api
        return coletar_plone_api
    if isinstance(config, JornalXML):
        from .coletar_xml import coletar_xml
        return coletar_xml
    if isinstance(config, JornalSublinks):
        from .coletar_sublinks import coletar_sublinks
        return coletar_sublinks
    if isinstance(config, JornalRSS):
        from .coletar_rss import coletar_rss
        return coletar_rss
    if isinstance(config, JornalSelenium):
        from .coletar_selenium import coletar_selenium
        return coletar_selenium
    raise TypeError(f"Sem coletor para {type(config).__name__}")


def executar_coleta(config, run_id: str | None = None, engine=None) -> dict:
    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

    engine = engine or get_sqlalchemy_engine(NOTICIAS_PG)
    s = saude.iniciar(config.fonte, type(config).__name__, config.url)
    inicio = time.monotonic()
    try:
        df = _coletor(config)(config, engine=engine)
        s.gravados = 0 if df is None else len(df)
        # Retorno pequeno: o DataFrame inteiro ia para o XCom à toa.
        return {"fonte": config.fonte, "gravados": s.gravados}
    except Exception as e:
        s.erro = f"{type(e).__name__}: {e}"[:500]
        raise
    finally:
        s.duracao_s = round(time.monotonic() - inicio, 1)
        saude.gravar(s, run_id, engine)
