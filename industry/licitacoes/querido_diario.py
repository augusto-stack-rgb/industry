"""Coletor Querido Diário — diários oficiais municipais (Open Knowledge Brasil).

Busca textual (``/gazettes``) nos diários oficiais de centenas de municípios.
Complementa o PNCP em dois pontos: municípios que ainda não publicam nele e
sinais ANTES do edital (portaria que institui equipe de planejamento, estudo
técnico preliminar, aviso de licitação no diário).

A API casa o diário INTEIRO e os ``excerpts`` que devolve costumam ser o começo
do diário, não o trecho do termo. Por isso o coletor baixa o texto completo de
cada diário encontrado (``txt_url``) e recorta uma janela em volta de cada
ocorrência dos termos. A janela só entra se tiver marcador de contratação e
nenhum marcador de ruído (lei, concurso, sumário). O LLM ainda julga no score.

Notas da API (testadas em 15/10/2026): host atual api.queridodiario.org.br;
buscas amplas sem aspas devolvem 500/520, por isso cada termo vai entre aspas e
com janela de datas; referência de uso educado: 60 requisições/minuto.
"""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import (
    DEST_DIR, QUERIDO_DIARIO_API, QUERIDO_DIARIO_DIAS, QUERIDO_DIARIO_TERMOS,
)
from industry.licitacoes.net import ColetorBloqueado
from industry.licitacoes.termos import normalizar, termos_encontrados

FONTE = "Querido Diário - Diários oficiais municipais"
TAM_PAGINA = 50
MAX_PAGINAS_POR_TERMO = 4
MAX_DIARIOS = 250          # textos baixados por execução
JANELA = 700               # caracteres antes/depois da ocorrência do termo
PAUSA_S = 1.1

# Marcadores de que o trecho trata de uma contratação (texto normalizado).
_CONTRATACAO = re.compile(
    r"aviso de licitacao|pregao|concorrencia|edital|chamamento publico|dispensa de licitacao|"
    r"dispensa eletronica|inexigibilidade|contratacao de empresa|contratacao de servic|"
    r"termo de referencia|estudo tecnico preliminar|equipe de planejamento|"
    r"registro de precos|solucao inovadora|empresa vencedora|homologa|adjudica|"
    r"extrato de contrato|extrato do contrato")
# Resultado/contrato já firmado: entra como fechado (tipo_documento "contrato").
_RESULTADO = re.compile(r"empresa vencedora|homologa|adjudica|extrato d[oe] contrato|"
                        r"contrato n[ºo°]|termo aditivo")
# Ruído recorrente: leis/campanhas, conteúdo de concurso, sumários.
_RUIDO = re.compile(
    r"conteudo programatico|concurso publico|prova objetiva|processo seletivo|"
    r"institui a campanha|\.{8,}|curso de aperfeicoamento|inscric[aoe]+s? (no|em) curso|"
    r"capacitacao apto|conhecimentos especificos|candidat[oa]s?\b")
_MODALIDADE = [
    ("pregao eletronico", "Pregão Eletrônico"), ("pregao", "Pregão"),
    ("concorrencia", "Concorrência"), ("dispensa", "Dispensa"),
    ("inexigibilidade", "Inexigibilidade"), ("chamamento publico", "Chamamento Público"),
    ("solucao inovadora", "CPSI (LC 182/2021)"),
    ("equipe de planejamento", "Planejamento da contratação"),
    ("estudo tecnico preliminar", "Planejamento da contratação"),
]
_TERMOS_NORM = [normalizar(t) for t in QUERIDO_DIARIO_TERMOS]


def _modalidade(texto_norm: str) -> str:
    for chave, nome in _MODALIDADE:
        if chave in texto_norm:
            return nome
    return "Diário oficial"


def _get(s: requests.Session, url: str, params: dict | None = None) -> requests.Response:
    espera = 5.0
    for _ in range(4):
        try:
            r = s.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r
            motivo = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            motivo = type(e).__name__
        time.sleep(espera)
        espera *= 2
    raise ColetorBloqueado(f"Querido Diário: {url} falhou ({motivo})")


def _diarios(s: requests.Session, termo: str, desde: str) -> list[dict]:
    out = []
    for pagina in range(MAX_PAGINAS_POR_TERMO):
        d = _get(s, QUERIDO_DIARIO_API, {
            "querystring": f'"{termo}"', "published_since": desde, "size": TAM_PAGINA,
            "offset": pagina * TAM_PAGINA, "excerpt_size": 50, "number_of_excerpts": 1,
            "sort_by": "descending_date"}).json()
        gazettes = d.get("gazettes") or []
        out += gazettes
        time.sleep(PAUSA_S)
        if len(gazettes) < TAM_PAGINA:
            break
    return out


def _janelas(texto: str) -> list[str]:
    """Trechos em volta de cada ocorrência dos termos, sem sobreposição."""
    norm = normalizar(texto)
    # normalizar() colapsa espaços: as posições do texto normalizado não batem com
    # as do original, então o recorte é feito no texto normalizado.
    pos = sorted(m.start() for t in _TERMOS_NORM for m in re.finditer(re.escape(t), norm))
    janelas, fim_ant = [], -1
    for p in pos:
        if p <= fim_ant:
            continue
        ini, fim = max(0, p - JANELA), min(len(norm), p + JANELA)
        janelas.append(norm[ini:fim])
        fim_ant = fim
    return janelas


def collect_querido_diario(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    desde = (date.today() - timedelta(days=QUERIDO_DIARIO_DIAS)).isoformat()

    diarios: dict[str, dict] = {}
    falhas = []
    for termo in QUERIDO_DIARIO_TERMOS:
        try:
            for g in _diarios(s, termo, desde):
                if g.get("txt_url"):
                    diarios.setdefault(g["txt_url"], g)
        except ColetorBloqueado as e:
            falhas.append(str(e))     # um termo instável não derruba os outros
    if falhas and len(falhas) == len(QUERIDO_DIARIO_TERMOS):
        raise ColetorBloqueado("; ".join(falhas[:3]))

    rows: dict[str, dict] = {}
    lidas = 0
    for txt_url, g in list(diarios.items())[:MAX_DIARIOS]:
        vistos_no_diario: set[tuple] = set()   # janelas do mesmo assunto viram 1 item
        try:
            texto = _get(s, txt_url).text
        except ColetorBloqueado as e:
            falhas.append(str(e))
            continue
        for jan in _janelas(texto):
            lidas += 1
            termos = termos_encontrados(jan)
            if not termos or not _CONTRATACAO.search(jan) or _RUIDO.search(jan):
                continue
            # O mesmo contrato costuma aparecer várias vezes no diário (homologação,
            # extrato, tabela de itens): janelas com os mesmos termos viram 1 item.
            assunto = tuple(sorted(termos))
            if assunto in vistos_no_diario:
                continue
            vistos_no_diario.add(assunto)
            chave = hashlib.sha1(f"{g.get('territory_id')}|{g.get('date')}|{jan[:300]}"
                                 .encode()).hexdigest()[:16]
            municipio, uf = g.get("territory_name", ""), g.get("state_code", "")
            rows[chave] = {
                "id": chave,
                "fonte": FONTE,
                "tipo_documento": "contrato" if _RESULTADO.search(jan) else "aviso",
                "tipo": _modalidade(jan),
                "titulo": f"Diário oficial de {municipio}/{uf} — {g.get('date', '')}",
                "objeto": jan,
                "data": g.get("date", ""),
                "link": g.get("url", ""),
                "orgao": f"Município de {municipio}" if municipio else "",
                "uf": uf,
                "municipio": municipio,
                "filtros_usados": "; ".join(termos),
            }
        time.sleep(0.3)

    df = pd.DataFrame(list(rows.values()))
    out = dest_dir / "querido_diario.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Querido Diário: {len(diarios)} diários, {lidas} trechos com termo, {len(df)} com "
          f"contratação (desde {desde}; {len(falhas)} falha(s)) -> {out}")
    return out


if __name__ == "__main__":
    print(collect_querido_diario())
