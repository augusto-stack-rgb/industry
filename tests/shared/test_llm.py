#!/usr/bin/env python3
"""Testes do roteador de chaves (industry.shared.llm) — sem rede.

Um transporte HTTP falso responde por chave, simulando cada falha. Roda com
`python` puro (sai com código 1 se algo falhar), como os demais testes:

    python tests/shared/test_llm.py
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from industry.shared import llm  # noqa: E402
from industry.shared.llm import _Chave  # noqa: E402

FALHAS: list[str] = []


def checar(nome: str, cond: bool, detalhe: str = "") -> None:
    print(("  ok   " if cond else "  FALHA ") + nome + (f" — {detalhe}" if detalhe and not cond else ""))
    if not cond:
        FALHAS.append(nome)


def ok_body(texto="ok"):
    return {"choices": [{"message": {"content": texto}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 3}, "model": "x"}


def transporte(respostas: dict, log: list):
    """respostas: {valor_da_chave: callable(request) -> httpx.Response}."""
    def handler(req: httpx.Request) -> httpx.Response:
        chave = req.headers["Authorization"].split()[-1]
        log.append((chave, req.url.host, json.loads(req.content)))
        return respostas[chave](req)
    return httpx.MockTransport(handler)


def R(status, body=None, headers=None, text=None):
    def f(_req):
        if text is not None:
            return httpx.Response(status, text=text, headers=headers or {})
        return httpx.Response(status, json=body if body is not None else {}, headers=headers or {})
    return f


def roteador(chaves, respostas, log, modelo="gpt-oss-120b", max_espera=2.0):
    return llm.Roteador(modelo, chaves=chaves, max_espera=max_espera,
                        transport=transporte(respostas, log))


print("\n── troca de chave em cada tipo de falha")
log: list = []
ch = [_Chave("groq", "k401", "groq#1"), _Chave("groq", "k429", "groq#2"),
      _Chave("groq", "k500", "groq#3"), _Chave("groq", "kok", "groq#4")]
r = roteador(ch, {
    "k401": R(401, {"error": {"message": "Invalid API Key"}}),
    "k429": R(429, {"error": {"message": "Rate limit. Please try again in 30s"}},
              headers={"retry-after": "30"}),
    "k500": R(500, text="boom"),
    "kok": R(200, ok_body("resposta")),
}, log)
resp = r.create([{"role": "user", "content": "oi"}], temperature=0)
checar("resposta veio da chave boa", resp.choices[0].message.content == "resposta"
       and resp.chave == "groq#4", str(resp.chave))
st = {s["chave"]: s for s in r.status()}
checar("401 tira a chave do rodízio", "inválida" in st["groq#1"]["morta"])
checar("429 bloqueia pelo retry-after", 25 < st["groq#2"]["bloqueada_s"] <= 30, str(st["groq#2"]))
checar("5xx esfria a chave", 0 < st["groq#3"]["bloqueada_s"] <= llm.ESFRIAR_ERRO_S)
log.clear()
r.create([{"role": "user", "content": "de novo"}])
checar("chamada seguinte vai direto para a chave livre", [c for c, *_ in log] == ["kok"],
       str([c for c, *_ in log]))

print("\n── 402 (sem crédito) e troca de provedor")
log = []
ch = [_Chave("cerebras", "c402", "cerebras#1"), _Chave("groq", "gok", "groq#1")]
r = roteador(ch, {"c402": R(402, {"message": "Payment required"}),
                  "gok": R(200, ok_body())}, log)
r.create([{"role": "user", "content": "x"}])
r.create([{"role": "user", "content": "y"}])
checar("402 remove a chave e a Groq responde", [c for c, *_ in log] == ["c402", "gok", "gok"],
       str([c for c, *_ in log]))
checar("id do modelo por provedor", log[0][2]["model"] == "gpt-oss-120b"
       and log[1][2]["model"] == "openai/gpt-oss-120b")

print("\n── 400 em um provedor tenta o outro; em todos, ErroDoPedido")
log = []
ch = [_Chave("groq", "g400", "groq#1"), _Chave("cerebras", "cok", "cerebras#1")]
r = roteador(ch, {"g400": R(400, {"error": {"message": "unsupported param"}}),
                  "cok": R(200, ok_body("da cerebras"))}, log)
checar("400 na Groq -> Cerebras responde",
       r.create([{"role": "user", "content": "x"}]).provedor == "cerebras")
checar("400 não mata a chave", not r.status()[0]["morta"])
log = []
r = roteador([_Chave("groq", "a", "groq#1"), _Chave("groq", "b", "groq#2")],
             {"a": R(400, {"error": {"message": "bad"}}), "b": R(400, {})}, log)
try:
    r.create([{"role": "user", "content": "x"}])
    checar("ErroDoPedido quando nenhum provedor aceita", False)
except llm.ErroDoPedido as e:
    checar("ErroDoPedido quando nenhum provedor aceita", e.status_code == 400)
checar("400 não é repetido nas outras chaves do mesmo provedor", len(log) == 1, str(len(log)))

print("\n── cota esgotada e chaves inválidas")
log = []
r = roteador([_Chave("groq", "a", "groq#1"), _Chave("groq", "b", "groq#2")],
             {k: R(429, {"error": {"message": "Rate limit reached ... tokens per day (TPD) ... "
                                              "Please try again in 1h2m3.5s"}}) for k in "ab"}, log)
t0 = time.monotonic()
try:
    r.create([{"role": "user", "content": "x"}])
    checar("CotaEsgotada", False)
except llm.CotaEsgotada as e:
    checar("CotaEsgotada com retry_after da mensagem", 3700 < e.retry_after <= 3724,
           str(e.retry_after))
checar("não esperou à toa (espera > max_espera)", time.monotonic() - t0 < 1.0)

r = roteador([_Chave("groq", "a", "groq#1"), _Chave("groq", "b", "groq#2")],
             {k: R(401, {}) for k in "ab"}, [])
try:
    r.create([{"role": "user", "content": "x"}])
    checar("SemChavesValidas", False)
except llm.SemChavesValidas:
    checar("SemChavesValidas quando todas são inválidas", True)

print("\n── espera curta: bloqueio de 1s é aguardado e a chamada passa")
estado = {"n": 0}


def primeiro_429(_req):
    estado["n"] += 1
    if estado["n"] == 1:
        return httpx.Response(429, json={}, headers={"retry-after": "1"})
    return httpx.Response(200, json=ok_body("depois da espera"))


r = roteador([_Chave("groq", "a", "groq#1")], {"a": primeiro_429}, [], max_espera=5)
checar("aguarda cooldown curto e repete",
       r.create([{"role": "user", "content": "x"}]).choices[0].message.content == "depois da espera")

print("\n── modelo sem provedor configurado / dialetos")
try:
    llm.Roteador("gpt-oss-20b", chaves=[_Chave("cerebras", "c", "cerebras#1")])
    checar("modelo sem chave do provedor -> SemChavesValidas", False)
except llm.SemChavesValidas:
    checar("modelo sem chave do provedor -> SemChavesValidas", True)
checar("Mistral: max_completion_tokens -> max_tokens e sem reasoning_effort",
       llm._adaptar("mistral", {"max_completion_tokens": 5, "reasoning_effort": "low"})
       == {"max_tokens": 5})
checar("Cerebras: max_tokens -> max_completion_tokens",
       llm._adaptar("cerebras", {"max_tokens": 5}) == {"max_completion_tokens": 5})
checar("nome lógico aceita id do provedor", llm.nome_logico("openai/gpt-oss-120b") == "gpt-oss-120b")
checar("parse '1h2m3.5s'", llm._segundos("1h2m3.5s") == 3723.5)
checar("parse '450ms'", abs(llm._segundos("450ms") - 0.45) < 1e-9)

print("\n── concorrência: 40 chamadas em 8 threads, 1 chave morre no meio")
log = []
lock = threading.Lock()
cont = {"k2": 0}


def morre_depois(_req):
    with lock:
        cont["k2"] += 1
        n = cont["k2"]
    return httpx.Response(200, json=ok_body()) if n <= 3 else httpx.Response(401, json={})


r = roteador([_Chave("groq", "k1", "groq#1"), _Chave("groq", "k2", "groq#2"),
              _Chave("groq", "k3", "groq#3")],
             {"k1": R(200, ok_body()), "k2": morre_depois, "k3": R(200, ok_body())}, log)
erros = []


def trabalho():
    for _ in range(5):
        try:
            r.create([{"role": "user", "content": "x"}])
        except Exception as e:  # noqa: BLE001
            erros.append(e)


ts = [threading.Thread(target=trabalho) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
checar("nenhuma chamada falhou", not erros, str(erros[:2]))
checar("chave que morreu saiu do rodízio", "inválida" in r.status()[1]["morta"])
checar("carga distribuída entre as chaves vivas",
       all(sum(c == k for c, *_ in log) >= 10 for k in ("k1", "k3")))

print("\n" + "=" * 72)
if FALHAS:
    print(f"{len(FALHAS)} falha(s): {FALHAS}")
    sys.exit(1)
print("todos os testes passaram")
