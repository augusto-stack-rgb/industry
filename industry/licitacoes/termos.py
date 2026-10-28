"""Filtro de relevância temática para as fontes amplas (PNCP aberto, diários).

Casa as keywords dos TEMAS (ML/IA/dados/forecasting/pesquisa operacional) e os
TERMOS_TI no texto normalizado (sem acento, minúsculo), por PALAVRA INTEIRA —
siglas curtas como "iot" e "rpa" casariam dentro de "patriota" e "carpa" se
fosse substring.
"""
from __future__ import annotations

import re
import unicodedata

from industry.licitacoes.config import TEMAS, TERMOS_EXTERIOR, TERMOS_TI


def normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


_TERMOS = sorted({kw for cfg in TEMAS.values() for kw in cfg["kw"]} | set(TERMOS_TI),
                 key=len, reverse=True)
_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in _TERMOS) + r")(?![a-z0-9])")


_RX_IA = re.compile(r"(?<![A-Za-zÀ-ú0-9])IA(?![A-Za-zÀ-ú0-9])")
# Jargão de compra que contém um termo do tema sem ser serviço de dados:
# "lubrificantes com previsão de consumo para 12 meses" (amostra de 27/10/2026).
_RX_JARGAO = re.compile(r"(?:com |cuja )?previsao de consumo(?= (?:para|anual|mensal|estimad|aproximad|de \d|de ate))"
                        r"|com previsao de consumo")


def termos_encontrados(texto: str) -> list[str]:
    """Termos de tema/TI presentes no texto (sem repetição, na ordem em que aparecem).
    A sigla "IA" só conta em maiúsculas ("ia" minúsculo é o verbo ir)."""
    achados = _RX.findall(_RX_JARGAO.sub(" ", normalizar(texto)))
    if _RX_IA.search(str(texto or "")):
        achados.append("ia")
    return list(dict.fromkeys(achados))


_TERMOS_EXT = sorted(set(TERMOS_EXTERIOR), key=len, reverse=True)
_RX_EXT = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in _TERMOS_EXT)
                     + r")(?![a-z0-9])")
# Termos da lista em português que, nos avisos internacionais, aparecem mais em
# compra genérica (servidores, SAP, celulares) do que em serviço de dados.
_GENERICOS_EXTERIOR = {"supply chain", "mdm", "iot", "rpa"}
_RX_AI = re.compile(r"(?<![A-Za-z0-9])AI(?![A-Za-z0-9])")


def termos_exterior(texto: str) -> list[str]:
    """Termos de tema em inglês/espanhol/francês, e em português (avisos de
    Moçambique, Angola, Timor). "AI" só conta em maiúsculas."""
    achados = _RX_EXT.findall(normalizar(texto)) + [
        t for t in termos_encontrados(texto) if t not in _GENERICOS_EXTERIOR]
    if _RX_AI.search(str(texto or "")):
        achados.append("ai")
    return list(dict.fromkeys(achados))


_NUCLEO = sorted({kw for cfg in TEMAS.values() for kw in cfg["kw"]}, key=len, reverse=True)
_RX_NUCLEO = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in _NUCLEO) + r")(?![a-z0-9])")


def termos_nucleo(texto: str) -> list[str]:
    """Só os termos fortes das 6 áreas (TEMAS), sem os de TI geral; inclui "IA"."""
    achados = _RX_NUCLEO.findall(normalizar(texto))
    if _RX_IA.search(str(texto or "")):
        achados.append("ia")
    return list(dict.fromkeys(achados))


_RX_CPSI = re.compile(r"(?i)\bcpsi\b|182/2021|lei complementar n?[º°.]?\s*182|marco legal das startups|"
                      r"contrato p[úu]blico (?:para|de) solu[çc][ãa]o inovadora|teste de solu[çc][ãa]o")


def e_cpsi(texto: str) -> bool:
    """CPSI (LC 182/2021) conta como tema mesmo sem os termos: o desafio é descrito
    pelo problema ("qualificação profissional", "riscos fiscais")."""
    return bool(_RX_CPSI.search(str(texto or "")))


_RX_DESAFIO = re.compile(r"(?i)\bdesafios?\b|como podemos|solu[çc][ãa]o inovadora|"
                         r"consulta p[úu]blica ao mercado|inova[çc][ãa]o aberta")


def e_desafio(texto: str) -> bool:
    """CPSI ou desafio de inovação: o objeto descreve o PROBLEMA, não a solução, e
    o pedido real (prever, antecipar risco, integrar bases) vem depois do texto
    padrão. Os LLMs leem mais desses textos (LLM_MAX_DESC_DESAFIO)."""
    return e_cpsi(texto) or bool(_RX_DESAFIO.search(str(texto or "")))
