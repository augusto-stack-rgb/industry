"""Embeddings locais em CPU (transformers), com o modelo carregado uma vez por processo.

Substitui o ``/api/embed`` do Ollama da origem. Usado para agrupar rótulos curtos
de assunto (dezenas a poucas centenas por setor), o que roda bem em CPU.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger(__name__)

_modelos: dict[str, tuple] = {}
_lock = threading.Lock()


def _carregar(nome: str):
    with _lock:
        if nome not in _modelos:
            import torch
            from transformers import AutoModel, AutoTokenizer

            log.info("[embed] carregando %s em CPU", nome)
            tok = AutoTokenizer.from_pretrained(nome)
            mod = AutoModel.from_pretrained(nome)
            mod.eval()
            _modelos[nome] = (tok, mod, torch)
        return _modelos[nome]


def embed(textos: list[str], modelo: str, lote: int = 32) -> list[list[float]]:
    """Vetores normalizados (pooling CLS, como o bge-m3 recomenda)."""
    tok, mod, torch = _carregar(modelo)
    out: list[list[float]] = []
    with torch.inference_mode():
        for i in range(0, len(textos), lote):
            enc = tok(textos[i:i + lote], padding=True, truncation=True,
                      max_length=64, return_tensors="pt")
            cls = mod(**enc).last_hidden_state[:, 0]
            cls = torch.nn.functional.normalize(cls, dim=-1)
            out.extend(cls.tolist())
    return out
