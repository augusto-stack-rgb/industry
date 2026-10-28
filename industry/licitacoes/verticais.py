"""Classificação do objeto de uma licitação em UMA vertical, via Groq.

Porte do ``verticais.py`` do repositório bateleur-pipelines (lá a taxonomia era
de consultoria financeira; aqui são as áreas de ML/IA/ciência de dados/forecasting/
pesquisa operacional — ver ``config.VERTICAIS``). Garantias:

  - **uma** categoria por objeto (não multi-rótulo) — pergunta única erra menos;
  - ``temperature=0`` e resposta forçada em JSON;
  - chaves e provedores pelo roteador ``industry.shared.llm`` (troca de chave
    automática em rate-limit, chave inválida ou erro);
  - idempotente: quem chama passa só os objetos ainda não classificados.

Este módulo NÃO conhece banco nem Dagster: recebe textos, devolve rótulos. Quem
lê/grava é o asset ``licitacoes_verticais`` (``assets.py``), com o SQL em ``db.py``.
O cliente do roteador chega por parâmetro.
"""
from __future__ import annotations

import json
import logging
import re

from industry.licitacoes.config import LLM_MAX_DESC_DESAFIO, VERTICAIS
from industry.licitacoes.termos import e_desafio

log = logging.getLogger(__name__)

MAX_OBJ_CHARS = 800      # o objeto útil cabe folgado; corta boilerplate de edital


def _cortar(texto: str) -> str:
    """CPSI e desafios trazem o problema depois do texto padrão: leem mais."""
    return texto[:LLM_MAX_DESC_DESAFIO if e_desafio(texto) else MAX_OBJ_CHARS]

_ID_POR_NOME = {v["nome"]: v["id"] for v in VERTICAIS}
_NUCLEO = [v for v in VERTICAIS if v["nucleo"]]
_OPERACIONAIS = [v for v in VERTICAIS if not v["nucleo"] and v["id"] != "outros"]

_LISTA = "\n".join(f"{i}. {v['nome']}" for i, v in enumerate(VERTICAIS, start=1))
_GLOSSARIO = "\n".join(f"- {v['nome']}: {v['desc']}" for v in VERTICAIS)

SYSTEM = f"""Você classifica o objeto de uma licitação pública brasileira em EXATAMENTE uma das categorias abaixo.
Responda SOMENTE em JSON, sem texto antes ou depois: {{"categoria": "...", "confianca": 0.0-1.0}}

Categorias (use o texto EXATO):
{_LISTA}

O que cada uma contém:
{_GLOSSARIO}

Regras:
- As {len(_NUCLEO)} primeiras são as áreas de serviço do prestador (Machine Learning Engineering/MLOps, AI Engineering, Data Science, Forecasting, Data Engineering/MDM/governança de dados e Research Operations — no Brasil também chamada de pesquisa operacional ou ciência de dados industrial). Só escolha uma delas se o objeto for CLARAMENTE sobre esse tema como ATIVIDADE-FIM da contratação — não basta a palavra aparecer de passagem.
- Construção de pipelines, integração ou plataforma de dados, data lake/lakehouse/data warehouse, MDM, qualidade e governança de dados é "Data Engineering, MDM e governança de dados". Só dashboards, painéis, Power BI e relatórios (sem construir a base de dados) é "Data analytics e BI".
- Assinatura ou licença de ferramenta (inclusive de IA) sem serviço relevante de desenvolvimento/modelagem é "Licenças, SaaS e plataformas prontas"; se o objeto incluir desenvolvimento, treinamento de modelos ou implantação sob medida, escolha a área de serviço correspondente.
- Curso ou capacitação de pessoas sobre IA é "Capacitação, cursos e eventos".
- CPSI, "solução inovadora" e desafios de inovação descrevem o PROBLEMA, não a solução. Classifique pela solução que o problema pede: prever, antecipar riscos ou emitir alertas -> Data Science ou Forecasting; integrar bases -> Data Engineering; ler leis, processos ou documentos -> AI Engineering. Aqui não exija as palavras IA, ML ou dados, e não use "Outros" só porque o texto é sobre o problema.
- As demais são categorias operacionais amplas: escolha a que melhor descreve o serviço ou bem contratado (ex.: "manutenção de ar-condicionado" -> Obras, facilities, bens e equipamentos; "sustentação de sistema de protocolo" -> Desenvolvimento de sistemas e TI; "leilão de imóveis" -> Leilão, concessão e alienação de ativos).
- Use "Outros" apenas se o objeto genuinamente não se encaixar em nenhuma das \
anteriores, ou se for vago demais para decidir (ex.: "prorrogação de prazo \
contratual", "contratações através de dispensa/inexigibilidade JULHO/2025").
- "confianca" é o quanto você tem certeza da categoria escolhida."""


# Teto de saída por chamada. Sem isto o provedor estima o pior caso da janela do
# modelo ao decidir se a requisição cabe na cota, e recusa chamadas que caberiam.
MAX_TOKENS_ITEM = 300
MAX_TOKENS_POR_ITEM_LOTE = 160

# Chaves, rate-limit e troca de provedor ficam no roteador (industry.shared.llm),
# que substituiu o KeyLimiter/requests próprios deste módulo. Atenção à cota
# DIÁRIA de tokens da Groq: é por ORGANIZAÇÃO (conta), não por chave — chaves da
# mesma conta não somam cota; contas diferentes, sim.


class LimiteDiarioTokens(RuntimeError):
    """As chaves do modelo esgotaram a cota (espera longa). Retentar hoje não
    adianta: a rodada deve parar e ser retomada depois (é resumível)."""


def _limpar_json(txt: str) -> str:
    """O modelo às vezes embrulha a resposta em ```json … ``` — remove a cerca."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip()).strip()


def _chamar(cliente, system: str, user: str, max_tokens: int) -> dict | None:
    """Uma chamada JSON pelo roteador. ``None`` = saída malformada ou pedido
    recusado (o chamador retenta). Cota esgotada/sem chave viva sobem como
    :class:`LimiteDiarioTokens`."""
    from industry.shared import llm

    try:
        r = cliente.create(
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=0,
            response_format={"type": "json_object"},
            reasoning_effort="low",
            max_completion_tokens=max_tokens,
        )
        return json.loads(_limpar_json(r.choices[0].message.content))
    except (llm.CotaEsgotada, llm.SemChavesValidas) as e:
        raise LimiteDiarioTokens(str(e)) from e
    except Exception as e:  # noqa: BLE001 — JSON quebrado, 4xx do pedido, rede
        log.warning("[vert] chamada falhou: %s", str(e)[:120])
        return None


def _vertical(item: dict) -> tuple[str, float]:
    nome = str(item.get("categoria", "")).strip()
    vid = _ID_POR_NOME.get(nome)
    if vid is None:   # o modelo inventou um rótulo — cai em Outros
        log.info("[vert] categoria fora da lista: %r", nome[:60])
        vid = "outros"
    try:
        conf = max(0.0, min(1.0, float(item.get("confianca", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    return vid, conf


def classificar_objeto(objeto: str, cliente, tentativas: int = 3) -> tuple[str, float]:
    """Classifica um objeto. Retorna ``(vertical_id, confianca)``.

    Objeto vazio/curto demais vira ``("outros", 0.0)`` sem gastar chamada.
    Falha persistente devolve ``("", 0.0)`` — o chamador NÃO persiste esse
    caso, para que a próxima rodada o retente.
    """
    texto = re.sub(r"\s+", " ", str(objeto or "")).strip()
    if len(texto) < 8:
        return "outros", 0.0
    for _ in range(tentativas):
        d = _chamar(cliente, SYSTEM, f"Objeto: {_cortar(texto)}", MAX_TOKENS_ITEM)
        if isinstance(d, dict):
            return _vertical(d)
    return "", 0.0


SYSTEM_LOTE = SYSTEM.replace(
    'Responda SOMENTE em JSON, sem texto antes ou depois: '
    '{"categoria": "...", "confianca": 0.0-1.0}',
    'Você recebe uma LISTA de objetos em JSON, cada um com "id" e "texto". '
    'Classifique CADA UM independentemente. Responda SOMENTE em JSON, sem texto '
    'antes ou depois, com um resultado por objeto, na mesma ordem e com o mesmo '
    '"id": {"resultados": [{"id": N, "categoria": "...", "confianca": 0.0-1.0}]}')


def classificar_lote(objetos: list[tuple[str, str]], cliente,
                     tentativas: int = 3) -> dict[str, tuple[str, float]]:
    """Classifica vários objetos numa chamada só. ``objetos`` = [(id, texto), …].

    O prompt de sistema custa ~1000 tokens; num lote de 10 ele é amortizado e o
    custo por licitação cai ~5×. O trade-off é de qualidade (N julgamentos numa
    pergunta erram mais), por isso os ids que o modelo pular são reperguntados
    individualmente pelo chamador.

    Returns:
        ``{id: (vertical_id, confianca)}``. Ids ausentes ficam de fora — o
        chamador os trata como falha e os retenta.
    """
    validos = [(i, _cortar(re.sub(r"\s+", " ", str(t or "")).strip()))
               for i, t in objetos]
    fora: dict[str, tuple[str, float]] = {i: ("outros", 0.0) for i, t in validos if len(t) < 8}
    pedir = [(i, t) for i, t in validos if len(t) >= 8]
    if not pedir:
        return fora

    lista = json.dumps([{"id": n, "texto": t} for n, (_, t) in enumerate(pedir)],
                       ensure_ascii=False)
    for _ in range(tentativas):
        d = _chamar(cliente, SYSTEM_LOTE, lista, MAX_TOKENS_POR_ITEM_LOTE * len(pedir))
        resultados = d.get("resultados") if isinstance(d, dict) else None
        if not isinstance(resultados, list):
            continue
        por_id = {}
        for item in resultados:
            if isinstance(item, dict) and item.get("id") is not None:
                try:
                    por_id[int(item["id"])] = item
                except (TypeError, ValueError):
                    pass
        out = dict(fora)
        for n, (idu, _) in enumerate(pedir):
            if n in por_id:
                out[idu] = _vertical(por_id[n])
        return out
    return fora   # esgotou as tentativas: só os curtos demais, o resto retenta
