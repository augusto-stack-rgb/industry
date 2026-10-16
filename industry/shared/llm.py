"""Roteador de chaves de LLM: um ponto único para todas as chamadas de chat.

Junta as chaves de vários provedores com API compatível com a da OpenAI (Groq,
Cerebras, Mistral) e, a cada chamada, escolhe a próxima chave utilizável que
serve o modelo pedido. Falhas são tratadas aqui, por chave, sem o chamador
precisar saber qual chave ou provedor respondeu:

==================  ==========================================================
HTTP / erro         o que o roteador faz
==================  ==========================================================
429 / 413           bloqueia SÓ aquela chave pelo tempo informado pela API
                    (header ``retry-after`` ou "try again in ...") e tenta a
                    próxima. Cota diária ("per day") bloqueia por horas.
401 / 403           chave inválida/revogada: sai do rodízio até o processo acabar.
402                 conta sem crédito/billing (visto na Cerebras): idem.
400 / 404 / 422     erro do PEDIDO naquele provedor (parâmetro não suportado,
                    JSON inválido gerado): pula o provedor nesta chamada e tenta
                    outro; se nenhum aceitar, levanta ``ErroDoPedido``.
5xx / rede / timeout  esfria a chave por alguns segundos e tenta a próxima.
==================  ==========================================================

Quando TODAS as chaves vivas estão bloqueadas, espera se a liberação mais
próxima for em até ``max_espera`` segundos; senão levanta ``CotaEsgotada`` (com
``retry_after``) para o chamador encerrar a rodada e retomar depois. Sem nenhuma
chave viva, levanta ``SemChavesValidas``.

Chaves: ``GROQ_API_KEY_N``, ``CEREBRAS_API_KEY_N``, ``MISTRAL_API_KEY_N`` (N = 1..50),
lidas do ``.env`` da raiz do repositório (ver ``connections.carregar_env``).

Modelos: os nomes lógicos de ``MODELOS`` mapeiam para o id de cada provedor. Só
entram no rodízio de um modelo os provedores que o servem — a Mistral não tem
gpt-oss nem qwen, então hoje não participa de nenhum modelo usado aqui (as chaves
ficam disponíveis para quando um modelo dela for usado).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import httpx

from industry.shared.connections import carregar_env

log = logging.getLogger(__name__)

PROVEDORES = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "cerebras": "https://api.cerebras.ai/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}

# nome lógico -> {provedor: id do modelo no provedor}. Verificado em 15/10/2026
# pelo /models de cada provedor.
MODELOS: dict[str, dict[str, str]] = {
    "gpt-oss-120b": {"groq": "openai/gpt-oss-120b", "cerebras": "gpt-oss-120b"},
    "gpt-oss-20b": {"groq": "openai/gpt-oss-20b"},
    "qwen3.8-27b": {"groq": "qwen/qwen3.8-27b", "cerebras": "qwen-3.8-27b"},
    "mistral-small": {"mistral": "mistral-small-latest"},
    "mistral-medium": {"mistral": "mistral-medium-latest"},
}

ESFRIAR_ERRO_S = 15.0        # 5xx/rede: pausa curta na chave
BLOQUEIO_PADRAO_S = 60.0     # 429 sem tempo informado
BLOQUEIO_DIARIO_S = 3600.0   # cota diária sem tempo informado


class SemChavesValidas(RuntimeError):
    """Nenhuma chave viva para o modelo (todas inválidas/sem crédito, ou nenhuma
    configurada). Esperar não resolve."""


class CotaEsgotada(RuntimeError):
    """Todas as chaves vivas estão em rate-limit além de ``max_espera``."""

    def __init__(self, retry_after: float, detalhe: str = ""):
        self.retry_after = retry_after
        super().__init__(f"todas as chaves em rate-limit; a mais próxima libera em "
                         f"{retry_after:.0f}s. {detalhe}".strip())


class ErroDoPedido(RuntimeError):
    """Nenhum provedor aceitou o pedido (4xx de pedido em todos)."""

    def __init__(self, status: int, mensagem: str):
        self.status_code = status
        super().__init__(f"HTTP {status}: {mensagem}")


# Nomes antigos (groq_pool), mantidos para o código portado.
PoolExhausted = CotaEsgotada


def nome_logico(modelo: str) -> str:
    """Aceita o id de qualquer provedor ("openai/gpt-oss-120b") ou o nome lógico."""
    if modelo in MODELOS:
        return modelo
    for logico, ids in MODELOS.items():
        if modelo in ids.values():
            return logico
    raise ValueError(f"modelo desconhecido: {modelo!r} (conhecidos: {sorted(MODELOS)})")


@dataclass(eq=False)
class _Chave:
    """Uma chave. ``morta`` vale para TODOS os modelos (chave inválida ou conta sem
    crédito é da chave); o bloqueio por rate-limit fica em cada Roteador, porque
    a cota da Groq é por modelo."""
    provedor: str
    valor: str = field(repr=False)
    rotulo: str
    morta: str = ""            # motivo, se saiu do rodízio

    @property
    def viva(self) -> bool:
        return not self.morta


_CHAVES: list[_Chave] | None = None
_MORTE_LOCK = threading.Lock()


def _chaves_do_ambiente() -> list[_Chave]:
    """Lidas uma vez por processo e compartilhadas entre os roteadores."""
    global _CHAVES
    if _CHAVES is not None:
        return _CHAVES
    import os

    carregar_env()
    out, vistas = [], set()
    for prov in PROVEDORES:
        for i in range(1, 51):
            v = os.environ.get(f"{prov.upper()}_API_KEY_{i}", "").strip()
            if v and v not in vistas:
                vistas.add(v)
                out.append(_Chave(prov, v, f"{prov}#{i}"))
    _CHAVES = out
    return out


_RETRY_MSG = re.compile(r"try again in ((?:\d+h)?(?:\d+m(?!s))?(?:[\d.]+ms|[\d.]+s)?)", re.I)


def _segundos(duracao: str) -> float | None:
    """'1h2m3.5s' / '2.37s' / '450ms' / '12' -> segundos."""
    if not duracao:
        return None
    s = duracao.strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, achou = 0.0, False
    for valor, unidade in re.findall(r"([\d.]+)(ms|h|m|s)", s):
        achou = True
        total += float(valor) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unidade]
    return total if achou else None


def _tempo_bloqueio(r: httpx.Response) -> float:
    texto = r.text or ""
    diario = bool(re.search(r"per day|\bTPD\b|\bRPD\b|daily", texto, re.I))
    for fonte in (r.headers.get("retry-after"),
                  (_RETRY_MSG.search(texto) or [None, None])[1]):
        seg = _segundos(fonte) if fonte else None
        if seg is not None:
            return max(seg, 1.0)
    return BLOQUEIO_DIARIO_S if diario else BLOQUEIO_PADRAO_S


def _adaptar(provedor: str, params: dict) -> dict:
    """Ajusta parâmetros ao dialeto de cada provedor."""
    p = dict(params)
    if provedor == "mistral":
        if "max_completion_tokens" in p:
            p["max_tokens"] = p.pop("max_completion_tokens")
        p.pop("reasoning_effort", None)
    elif provedor == "cerebras" and "max_tokens" in p:
        p["max_completion_tokens"] = p.pop("max_tokens")
    return p


def _resposta(d: dict, provedor: str, chave: _Chave) -> SimpleNamespace:
    """Objeto no formato do SDK da OpenAI: ``resp.choices[0].message.content``."""
    choices = [SimpleNamespace(
        message=SimpleNamespace(content=(c.get("message") or {}).get("content") or ""),
        finish_reason=c.get("finish_reason"))
        for c in d.get("choices") or []]
    return SimpleNamespace(choices=choices, usage=d.get("usage") or {},
                           provedor=provedor, chave=chave.rotulo, model=d.get("model"))


class Roteador:
    """Rodízio thread-safe entre as chaves de todos os provedores de um modelo."""

    def __init__(self, modelo: str, timeout: float = 120.0, max_espera: float = 90.0,
                 chaves: list[_Chave] | None = None, transport: httpx.BaseTransport | None = None):
        self.modelo = nome_logico(modelo)
        self.ids = MODELOS[self.modelo]
        todas = chaves if chaves is not None else _chaves_do_ambiente()
        self._chaves = [c for c in todas if c.provedor in self.ids]
        self._bloqueio: dict[int, float] = {id(c): 0.0 for c in self._chaves}
        self.max_espera = max_espera
        self._lock = threading.Lock()
        self._idx = 0
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  limits=httpx.Limits(max_connections=64))
        if not self._chaves:
            raise SemChavesValidas(
                f"nenhuma chave configurada para {self.modelo} "
                f"(provedores: {', '.join(self.ids)}) — ver .env")
        log.info("[llm] %s: %d chave(s) (%s)", self.modelo, len(self._chaves),
                 ", ".join(f"{p}={sum(c.provedor == p for c in self._chaves)}" for p in self.ids))

    # ── estado ──────────────────────────────────────────────────────────────
    def status(self) -> list[dict]:
        agora = time.monotonic()
        with self._lock:
            return [{"chave": c.rotulo, "morta": c.morta or "",
                     "bloqueada_s": max(0.0, round(self._bloqueio[id(c)] - agora, 1))}
                    for c in self._chaves]

    def _proxima(self, pular: set[str]) -> _Chave | float:
        """Próxima chave livre (round-robin) ou, se não houver, segundos até a
        mais próxima liberar. Levanta SemChavesValidas se nenhuma estiver viva."""
        with self._lock:
            vivas = [c for c in self._chaves if c.viva and c.provedor not in pular]
            if not vivas:
                if any(c.viva for c in self._chaves):
                    return -1.0          # só restam provedores pulados neste pedido
                raise SemChavesValidas(
                    f"{self.modelo}: nenhuma chave viva — "
                    + "; ".join(f"{c.rotulo}: {c.morta}" for c in self._chaves))
            agora = time.monotonic()
            n = len(self._chaves)
            for k in range(n):
                c = self._chaves[(self._idx + k) % n]
                if c.viva and c.provedor not in pular and self._bloqueio[id(c)] <= agora:
                    self._idx = (self._idx + k + 1) % n
                    return c
            return min(self._bloqueio[id(c)] for c in vivas) - agora

    def _bloquear(self, c: _Chave, segundos: float, motivo: str) -> None:
        with self._lock:
            self._bloqueio[id(c)] = max(self._bloqueio[id(c)], time.monotonic() + segundos)
        log.info("[llm] %s bloqueada por %.0fs (%s)", c.rotulo, segundos, motivo)

    def _matar(self, c: _Chave, motivo: str) -> None:
        with _MORTE_LOCK:
            if not c.morta:
                c.morta = motivo
                log.warning("[llm] %s removida do rodízio: %s", c.rotulo, motivo)

    # ── chamada ─────────────────────────────────────────────────────────────
    def create(self, messages: list[dict], **params) -> SimpleNamespace:
        """``chat.completions.create`` roteado. ``model`` vem do roteador."""
        params.pop("model", None)
        pular: set[str] = set()          # provedores que recusaram ESTE pedido
        ultimo_4xx: tuple[int, str] | None = None
        erros_transitorios = 0
        limite_transitorios = max(3, 2 * len(self._chaves))
        esperado = 0.0

        while True:
            prox = self._proxima(pular)
            if isinstance(prox, float):
                if prox < 0:             # todos os provedores vivos recusaram o pedido
                    raise ErroDoPedido(*(ultimo_4xx or (400, "pedido recusado")))
                if prox > self.max_espera or esperado + prox > self.max_espera * 3:
                    raise CotaEsgotada(prox, f"[{self.modelo}]")
                time.sleep(max(prox, 0.5))
                esperado += prox
                continue

            c = prox
            corpo = {"model": self.ids[c.provedor], "messages": messages,
                     **_adaptar(c.provedor, params)}
            try:
                r = self._http.post(PROVEDORES[c.provedor], json=corpo,
                                    headers={"Authorization": f"Bearer {c.valor}"})
            except httpx.HTTPError as e:
                erros_transitorios += 1
                self._bloquear(c, ESFRIAR_ERRO_S, f"{type(e).__name__}")
                if erros_transitorios >= limite_transitorios:
                    raise
                continue

            st = r.status_code
            if st == 200:
                try:
                    return _resposta(r.json(), c.provedor, c)
                except ValueError:
                    erros_transitorios += 1
                    self._bloquear(c, ESFRIAR_ERRO_S, "resposta não-JSON")
                    continue
            msg = r.text[:300]
            if st in (429, 413):
                self._bloquear(c, _tempo_bloqueio(r), f"HTTP {st}")
            elif st in (401, 403):
                self._matar(c, f"HTTP {st} (chave inválida/sem permissão)")
            elif st == 402:
                self._matar(c, "HTTP 402 (conta sem crédito)")
            elif st in (400, 404, 422):
                log.warning("[llm] %s recusou o pedido (HTTP %s): %s", c.rotulo, st, msg[:160])
                pular.add(c.provedor)
                ultimo_4xx = (st, msg)
            else:                         # 5xx e afins
                erros_transitorios += 1
                self._bloquear(c, ESFRIAR_ERRO_S, f"HTTP {st}")
                if erros_transitorios >= limite_transitorios:
                    raise RuntimeError(f"{self.modelo}: {erros_transitorios} erros "
                                       f"transitórios seguidos; último HTTP {st}: {msg}")


class Cliente:
    """Roteador com parâmetros padrão (temperature, max_tokens...) fixados."""

    def __init__(self, roteador: Roteador, **padroes):
        self.roteador = roteador
        self.padroes = padroes
        self.model = roteador.modelo

    def create(self, messages: list[dict], **params) -> SimpleNamespace:
        return self.roteador.create(messages, **{**self.padroes, **params})


_cache: dict[tuple, Roteador] = {}
_cache_lock = threading.Lock()


def roteador(modelo: str, max_espera: float = 90.0, timeout: float = 120.0) -> Roteador:
    """Um roteador por (modelo, max_espera) por processo: as threads de uma etapa
    compartilham o mesmo estado de bloqueio das chaves."""
    chave = (nome_logico(modelo), max_espera, timeout)
    with _cache_lock:
        if chave not in _cache:
            _cache[chave] = Roteador(modelo, timeout=timeout, max_espera=max_espera)
        return _cache[chave]


def cliente(modelo: str, max_espera: float = 90.0, timeout: float = 120.0, **padroes) -> Cliente:
    return Cliente(roteador(modelo, max_espera=max_espera, timeout=timeout), **padroes)


def e_chave_invalida(e) -> bool:
    """Compatibilidade: o roteador nunca deixa escapar 401/403 cru."""
    return getattr(e, "status_code", None) in (401, 403)

