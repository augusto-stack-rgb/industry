"""Filtro de relevância temática para as fontes amplas (PNCP aberto, diários).

Casa as keywords dos TEMAS (ML/IA/dados/forecasting/pesquisa operacional) e os
TERMOS_TI no texto normalizado (sem acento, minúsculo), por PALAVRA INTEIRA —
siglas curtas como "iot" e "rpa" casariam dentro de "patriota" e "carpa" se
fosse substring.
"""
from __future__ import annotations

import re
import unicodedata

from industry.licitacoes.config import TEMAS, TERMOS_TI


def normalizar(texto: str) -> str:
    s = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s)


_TERMOS = sorted({kw for cfg in TEMAS.values() for kw in cfg["kw"]} | set(TERMOS_TI),
                 key=len, reverse=True)
_RX = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(t) for t in _TERMOS) + r")(?![a-z0-9])")


def termos_encontrados(texto: str) -> list[str]:
    """Termos de tema/TI presentes no texto (sem repetição, na ordem em que aparecem)."""
    return list(dict.fromkeys(_RX.findall(normalizar(texto))))
