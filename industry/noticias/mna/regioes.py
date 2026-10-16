"""Região (continente) de uma operação de M&A — fonte única do mapeamento.

Extraído de ``report_html`` para poder ser importado pela DAG de email
(``DATABASE_Noticias_OperacoesMnA_Email``) SEM arrastar matplotlib/pandas: aquele
módulo carrega matplotlib no topo e não pode entrar na task de envio.

"Região" aqui é o mesmo conceito que o painel chama de continente, com **Brasil
como região própria** (é o recorte que interessa ao time) — por isso
``CONTINENTE_ORDEM`` começa por "Brasil" e o PDF filtrado por região usa
``REGIAO_BRASIL``.
"""

from __future__ import annotations

import re
import unicodedata

# --- Continentes: Oceania (e outras menores) entram como "Outros". ---
CONTINENTE_MERGE = {
    "Oceania": "Outros",
    "América do Sul": "Outros",
    "América Latina": "Outros",
    "África": "Outros",
    "Outros": "Outros",
}
# ordem de exibição (Brasil sempre 1º; "Outros"/"Não informado" no fim)
CONTINENTE_ORDEM = ["Brasil", "América do Norte", "Europa", "Ásia", "Outros", "Não informado"]

# --- De qual lado vem o continente do negócio: "alvo" ou "adquirente". ---
CONTINENTE_BASE = "alvo"

# Rótulo do recorte usado no PDF/coluna "Região" filtrados só para o Brasil.
REGIAO_BRASIL = "Brasil"

NAO_INFORMADO = "Não informado"

PAIS_CONTINENTE = {
    "brasil": "Brasil",
    "estados unidos": "América do Norte",
    "eua": "América do Norte",
    "usa": "América do Norte",
    "canada": "América do Norte",
    "mexico": "América do Norte",
    "argentina": "América do Sul",
    "chile": "América do Sul",
    "colombia": "América do Sul",
    "peru": "América do Sul",
    "uruguai": "América do Sul",
    "cuba": "América Latina",
    "reino unido": "Europa",
    "inglaterra": "Europa",
    "espanha": "Europa",
    "alemanha": "Europa",
    "franca": "Europa",
    "italia": "Europa",
    "dinamarca": "Europa",
    "suica": "Europa",
    "suecia": "Europa",
    "finlandia": "Europa",
    "holanda": "Europa",
    "paises baixos": "Europa",
    "portugal": "Europa",
    "irlanda": "Europa",
    "noruega": "Europa",
    "belgica": "Europa",
    "austria": "Europa",
    "polonia": "Europa",
    "grecia": "Europa",
    "japao": "Ásia",
    "china": "Ásia",
    "india": "Ásia",
    "coreia do sul": "Ásia",
    "indonesia": "Ásia",
    "singapura": "Ásia",
    "hong kong": "Ásia",
    "ira": "Ásia",
    "israel": "Ásia",
    "emirados arabes unidos": "Ásia",
    "taiwan": "Ásia",
    "tailandia": "Ásia",
    "vietna": "Ásia",
    "malasia": "Ásia",
    "australia": "Oceania",
    "nova zelandia": "Oceania",
    "africa do sul": "África",
    "egito": "África",
    "nigeria": "África",
    # Países que apareceram na base caindo em "Outros" (auditoria set/2026).
    "venezuela": "América do Sul",
    "paraguai": "América do Sul",
    "guatemala": "América Latina",
    "porto rico": "América Latina",
    "trinidad e tobago": "América Latina",
    "bermudas": "América Latina",
    "luxemburgo": "Europa",
    "turquia": "Europa",
    "croacia": "Europa",
    "servia": "Europa",
    "eslovenia": "Europa",
    "bosnia": "Europa",
    "montenegro": "Europa",
    "arabia saudita": "Ásia",
    "paquistao": "Ásia",
    "filipinas": "Ásia",
    "cingapura": "Ásia",
    "cazaquistao": "Ásia",
    "uzbequistao": "Ásia",
    "azerbaijao": "Ásia",
    "iraque": "Ásia",
    "angola": "África",
    "gabao": "África",
    "namibia": "África",
    "sao tome e principe": "África",
    "equatorial guinea": "África",
    "guine equatorial": "África",
    # O LLM às vezes devolve o continente no lugar do país ("empresa europeia").
    "europa": "Europa",
    "asia": "Ásia",
    "africa": "África",
    "america do norte": "América do Norte",
    "america do sul": "América do Sul",
    "america latina": "América Latina",
    "oceania": "Oceania",
}

# Separadores de país composto ("EUA/Brasil", "Suíça e Reino Unido",
# "Estados Unidos (BlackRock), Brasil (Vale)"): vale o PRIMEIRO país reconhecido.
_SEP_PAISES = re.compile(r"\s*(?:/|,|;|\be\b|\(.*?\))\s*")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _key(s) -> str:
    """Normaliza para busca no mapa. Cópia local do helper de ``report_html``
    (3 linhas) para este módulo não depender de pandas."""
    if s is None or s != s:  # None ou NaN/NaT
        return ""
    return re.sub(r"\s+", " ", _strip_accents(str(s)).lower().replace("_", " ").strip())


def continente_de(raw) -> str:
    k = _key(raw)
    if not k or k in ("nao especificado", "n/a", "desconhecido"):
        return NAO_INFORMADO
    if k in PAIS_CONTINENTE:
        return PAIS_CONTINENTE[k]
    for parte in _SEP_PAISES.split(k):
        if parte in PAIS_CONTINENTE:
            return PAIS_CONTINENTE[parte]
    return "Outros"


def ordenar_continentes(presentes) -> list:
    p = set(presentes)
    return [c for c in CONTINENTE_ORDEM if c in p] + sorted(c for c in p if c not in CONTINENTE_ORDEM)


def regiao_de(pais_base, pais_alt=None) -> str:
    """Região da operação, já com o merge de continentes menores aplicado.

    ``pais_base`` é o país do lado definido por ``CONTINENTE_BASE`` (alvo, por
    padrão); ``pais_alt`` é o outro lado, usado como fallback quando o principal
    não foi informado — mesma regra do painel, que prefere não deixar a operação
    em "Não informado" quando um dos lados é conhecido.
    """
    c = continente_de(pais_base)
    if c == NAO_INFORMADO and pais_alt is not None:
        c = continente_de(pais_alt)
    return CONTINENTE_MERGE.get(c, c)
