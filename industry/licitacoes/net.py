"""Helpers de rede dos coletores.

Transformam FALHA MASCARADA (WAF, corpo não-JSON, erro HTTP) em exceção — para a
task do Airflow falhar de verdade, em vez de gravar um CSV vazio/parcial e
reportar "sucesso". Ver DECISOES.md §12.

Modo de falha real que motivou isto (BRB, 07/2026): a API devolvia **HTTP 200**
com um corpo HTML de WAF (`<html>...Request Rejected...`). O `raise_for_status()`
passava (status 200), o `resp.json()` estourava `JSONDecodeError`, e o coletor
engolia num `except: break` — coletando parcialmente e reportando sucesso.

"Vazio legítimo" (a fonte respondeu JSON válido com lista vazia) NÃO é erro e não
levanta aqui — quem decide isso é o coletor.
"""
from __future__ import annotations

import time

import requests


class ColetorBloqueado(RuntimeError):
    """A fonte respondeu, mas com bloqueio/erro — não é 'vazio legítimo'.

    Casos: página de WAF ("Request Rejected") com HTTP 200, corpo HTML onde se
    esperava JSON, erro HTTP 4xx/5xx, ou falha de transporte persistente.
    """


def json_or_raise(resp: requests.Response, fonte: str):
    """Valida a resposta e devolve o JSON decodificado, ou levanta ColetorBloqueado."""
    resp.raise_for_status()  # 4xx/5xx -> falha explícita
    txt = (resp.text or "").lstrip()
    if not txt or txt[0] not in "{[":
        raise ColetorBloqueado(
            f"{fonte}: HTTP {resp.status_code} mas corpo não-JSON "
            f"(possível WAF/bloqueio). Início: {txt[:120]!r}"
        )
    try:
        return resp.json()
    except ValueError as e:
        raise ColetorBloqueado(
            f"{fonte}: corpo não decodificável como JSON ({e}). Início: {txt[:120]!r}"
        ) from e


def get_json(url, *, fonte, session=None, params=None, headers=None,
             timeout=40, tentativas=4, backoff=3.0):
    """GET resiliente que devolve JSON, ou levanta ColetorBloqueado ao esgotar.

    Re-tenta com backoff linear (backoff, 2·backoff, …) tanto em erro de REDE
    quanto em **bloqueio de WAF / HTTP-200-não-JSON**. Motivo: o WAF da BRP é
    **intermitente por taxa** (bloqueia sob rajada e libera segundos depois) —
    re-tentar resolve o caso transitório. Só levanta depois de `tentativas`
    falhas seguidas, aí sim falhando a task (vermelha + email) por bloqueio
    persistente. 'Vazio legítimo' (JSON válido, lista vazia) volta normal.
    """
    getter = (session or requests).get
    ultimo = None
    for i in range(tentativas):
        try:
            resp = getter(url, params=params, headers=headers, timeout=timeout)
            return json_or_raise(resp, fonte)
        except (requests.RequestException, ColetorBloqueado) as e:
            ultimo = e
            if i < tentativas - 1:
                time.sleep(backoff * (i + 1))
    raise ColetorBloqueado(
        f"{fonte}: falha após {tentativas} tentativas — {ultimo}"
    )
