"""Algoritmos da extração de operações de M&A (porte fiel do notebook analiseMnA).

Estágios:
  1. Classificação — baseline keyword/regex + zero-shot NLI (mDeBERTa-v3).
  2. Dedup de quase-duplicatas por Jaccard de título (mesmo deal em N fontes).
  3. Extração — Groq (``openai/gpt-oss-120b``, reasoning_effort baixo) com o
     prompt exato do notebook, via pool de chaves compartilhado.
  3b. Validação semântica — o LLM julga cada operação extraída ("é M&A de
     verdade? corresponde ao artigo?"). Filtra o que o gate do estágio 1 não
     pode pegar: ele filtra ARTIGOS, não as OPERAÇÕES que o LLM devolve.
  3c. Dedup semântico — o LLM compara a operação com as já cadastradas nos
     últimos dias. Pega o que a consolidação por nome normalizado não pega:
     mesma transação com grafias diferentes ou com adquirente/alvo invertidos.
  3d. Mescla — a duplicata atualiza a operação cadastrada (status mais avançado,
     valor ausente) em vez de ser só descartada; ver :func:`mesclar`.
  4. Consolidação — normaliza nomes de empresa e deduplica por (adquirente, alvo).

Os estágios 1-2 são determinísticos; 3, 3b e 3c consomem tokens do MESMO pool de
chaves Groq (o orçamento diário é compartilhado entre eles).

Sem dependência do Airflow no nível de módulo (imports de ``shared.*`` e dos
modelos pesados são feitos dentro das funções, em runtime de task).
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import time
import unicodedata

import pandas as pd

from industry.noticias.mna import config as C

log = logging.getLogger(__name__)


# ── 1a. classificação: keyword/regex (padrões exatos do notebook) ────────────────
PADROES_MA = [
    r"\b(adquir[ei]\w*|aquisi[çc][ãa]o|aquisitiv\w+)\b",
    r"\b(comprou|compra\s+d[aeo]s?|comprad[oa]r[ae]?)\b",
    r"\b(fus[ãa]o|fundiu-se|se\s+fund[ei]\w*|fusionaram)\b",
    r"\b(incorpor[oa]\w*|incorpora[çc][ãa]o)\b",
    r"\b(assum(iu|iram)\s+o\s+controle|controle\s+acion[áa]rio)\b",
    r"\b(joint\s+venture|joint-venture)\b",
    r"\bpor\s+(R\$|US\$|€)\s*[\d,\.]+\s*(milh[õo]es|bilh[õo]es|mi|bi)\b",
    r"\b(participa[çc][ãa]o\s+(majorit[áa]ria|minorit[áa]ria|acion[áa]ria))\b",
    r"\b(venda\s+d[aeo]s?|vendeu\s+(a|o|para))\b",
    r"\b(take[- ]over|takeover|oferta\s+p[úu]blica\s+de\s+aquisi[çc][ãa]o|opa)\b",
    r"\b(deal|transa[çc][ãa]o)\s+(de\s+)?(m&a|fus[ãa]o|aquisi[çc][ãa]o)\b",
]


def classificar_por_keyword(texto: str, min_matches: int = C.MIN_MATCHES_REGEX) -> tuple[bool, int]:
    """Notebook ``classificar_por_keyword``: True se >= ``min_matches`` padrões distintos."""
    if not isinstance(texto, str) or not texto.strip():
        return False, 0
    low = texto.lower()
    matches = sum(1 for p in PADROES_MA if re.search(p, low))
    return matches >= min_matches, matches


# ── 1b. classificação: zero-shot NLI (mDeBERTa) ──────────────────────────────────
_clf = None


def _get_zeroshot():
    """Carrega (uma vez) o pipeline zero-shot. Usa GPU se CUDA disponível, senão CPU."""
    global _clf
    if _clf is None:
        import torch
        from transformers import pipeline

        device = 0 if torch.cuda.is_available() else -1
        _clf = pipeline(task="zero-shot-classification", model=C.ZEROSHOT_MODEL, device=device)
        log.info("[mna] zero-shot carregado em %s", "GPU (cuda:0)" if device == 0 else "CPU")
    return _clf


def classificar_por_zeroshot_batch(textos: list[str]) -> list[float]:
    """Score da classe positiva (``ZEROSHOT_LABELS[0]``) para cada texto."""
    clf = _get_zeroshot()
    truncados = [(t or "")[:C.ZEROSHOT_CHAR_LIMIT] for t in textos]
    results = clf(truncados, C.ZEROSHOT_LABELS, hypothesis_template=C.ZEROSHOT_TEMPLATE,
                  batch_size=C.ZEROSHOT_BATCH_SIZE, multi_label=False)
    if isinstance(results, dict):
        results = [results]
    scores = []
    for res in results:
        score_map = dict(zip(res["labels"], res["scores"]))
        scores.append(float(score_map.get(C.ZEROSHOT_LABELS[0], 0.0)))
    return scores


# ── 2. dedup de quase-duplicatas por Jaccard de título (lógica exata do notebook) ─
_STOP = {
    "de", "da", "do", "das", "dos", "em", "e", "o", "a", "os", "as", "para", "por",
    "com", "que", "se", "no", "na", "nos", "nas", "um", "uma", "é", "ao", "à", "mais",
    "sua", "seu", "sobre", "após", "entre", "como", "isso", "este", "essa", "esta",
    "pelo", "pela",
}


def _kw(texto: str) -> set[str]:
    tokens = re.findall(r"[A-Za-zÀ-ú]{4,}", str(texto).lower())
    return {t for t in tokens if t not in _STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def dedup_por_titulo(df: pd.DataFrame, score_col: str, titulo_col: str = "titulo",
                     limiar: float = C.DEDUP_JACCARD) -> pd.DataFrame:
    """Mantém o artigo de maior ``score_col`` por grupo de títulos quase-duplicados."""
    d = df.sort_values(score_col, ascending=False).reset_index(drop=True)
    titles_kw = [_kw(t) for t in d[titulo_col].fillna("")]
    chosen, visited = [], set()
    for i in range(len(d)):
        if i in visited:
            continue
        chosen.append(i)
        for j in range(i + 1, len(d)):
            if j not in visited and _jaccard(titles_kw[i], titles_kw[j]) >= limiar:
                visited.add(j)
        visited.add(i)
    return d.iloc[chosen].reset_index(drop=True)


def selecionar_para_extracao(pool: pd.DataFrame) -> pd.DataFrame:
    """Aplica o gate (keyword + zero-shot) e o dedup, devolvendo os artigos a extrair.

    Gate combinado: zero-shot forte (>= ZEROSHOT_THRESHOLD) OU (zero-shot no limiar
    inferior E baseline de keyword positivo) — recupera deals logo abaixo do corte
    sem inundar de ruído.

    Args:
        pool: Pool de candidatos (colunas ``titulo, link, data, texto, fonte``).

    Returns:
        DataFrame dos artigos positivos, deduplicados por título, com a coluna
        ``zs_score_ma``. Vazio se o pool for vazio.
    """
    if pool.empty:
        return pool
    work = pool.copy().reset_index(drop=True)
    textos = work["texto"].fillna("").astype(str).tolist()

    kw = [classificar_por_keyword(t) for t in textos]
    work["kw_eh_ma"] = [r[0] for r in kw]
    work["zs_score_ma"] = classificar_por_zeroshot_batch(textos)
    work["mna_positivo"] = (work["zs_score_ma"] >= C.ZEROSHOT_THRESHOLD) | (
        (work["zs_score_ma"] >= C.ZEROSHOT_THRESHOLD_SECONDARY) & work["kw_eh_ma"])

    selected = work[work["mna_positivo"]].copy()
    log.info("[mna] zero-shot M&A: %d/%d positivos (>= %.2f)",
             len(selected), len(work), C.ZEROSHOT_THRESHOLD)
    selected = dedup_por_titulo(selected, "zs_score_ma", "titulo")
    log.info("[mna] após dedup de títulos: %d artigos únicos", len(selected))
    return selected


# ── 3. extração via Groq (pool de chaves compartilhado) ──────────────────────────
SYSTEM_MSG = ("Você é analista especialista em M&A. Responda SOMENTE com JSON válido, "
              "sem markdown.")

PROMPT = """Analise o artigo e extraia TODAS as transações de fusão/aquisição mencionadas.

Título: {titulo}
Fonte: {fonte} | Data: {data}
Texto: {texto}

Retorne JSON exato:
{{
  "operacoes": [{{
    "empresa_adquirente": "...",
    "empresa_alvo": "...",
    "valor": <valor absoluto na unidade da moeda, ex.: 10 bilhões de euros -> 10000000000; R$ 1,2 bilhão -> 1200000000; ou null>,
    "moeda": "USD|BRL|EUR|GBP|outro|null",
    "setor": "...",
    "status": "anunciado|concluído|rumor|cancelado|em negociação|null",
    "pais_adquirente": "...",
    "pais_alvo": "...",
    "descricao": "resumo em 1 frase em português"
  }}]
}}

Se não houver transação específica: {{"operacoes": []}}. Não invente dados.
O campo "valor" deve ser o número absoluto na unidade da moeda (bilhão = 1000000000, milhão = 1000000), sem abreviações nem separador de milhar."""


# ── 3b. validação semântica: a operação extraída é um M&A de verdade? ────────────
SYSTEM_VALIDACAO = ("Você é analista sênior de M&A revisando dados extraídos "
                    "automaticamente. Responda SOMENTE com JSON válido, sem markdown.")

# Rubrica em 3 perguntas (OBJETO / ATUAL / FOCO) antes do veredito. A versão
# anterior (regras D1-D3 + lista do que aceitar) aprovava compra de ATIVO
# (bloco de petróleo, usina, imóvel) porque a própria lista a aceitava, e deixava
# passar deal antigo citado como contexto sempre que o artigo era sobre M&A — a
# regra D2 só valia quando "o assunto da notícia é outro". Fica: empresa,
# participação societária e unidade de negócio (carve-out). Sai: ativo físico,
# deal antigo, citação de passagem, IPO/reorganização.
# Teste holdout (29/09/2026): 100 operações NUNCA usadas no ajuste do prompt,
# rótulos congelados antes de rodar, 1 repetição, mesmo modelo de produção.
#   prompt antigo: ruins barrados 19/44 (43%), bons rejeitados 0/51
#   este prompt:   ruins barrados 34/44 (77%, IC95 63-87%), bons rejeitados 0/51
# Ganho em ativo (1/14 -> 12/14) e citação de passagem (0/5 -> 3/5). REGRESSÃO
# conhecida: placement/compra de ações em bolsa (Tenaga, 5% da Lucid) e alguns
# deals antigos (Advent/Tigre) que a D1/D2 antiga pegava agora passam.
PROMPT_VALIDACAO = """Uma extração automática afirmou que o artigo abaixo descreve esta operação de M&A. Você é o revisor: decida se ELA entra num relatório de fusões e aquisições de EMPRESAS que estão acontecendo AGORA. A extração erra com frequência — seu papel é barrar o que não pertence ao relatório, não confirmar a extração.

OPERAÇÃO EXTRAÍDA
Adquirente: {adquirente}
Alvo: {alvo}
Valor: {valor} {moeda}
Setor: {setor}
Status: {status}
Descrição: {descricao}

ARTIGO (publicado em {data})
Título: {titulo}
Fonte: {fonte}
Texto: {texto}

Julgue SÓ a operação extraída — estas partes, este negócio. Um artigo pode citar várias transações; se o artigo trata de OUTRA transação e esta aparece apenas de passagem, esta não entra, por mais que a transação principal do artigo seja um M&A legítimo.

Antes do veredito, responda as três perguntas abaixo com base no ARTIGO (não na descrição da extração, que pode estar errada):

1. OBJETO — o que muda (ou mudaria, se o negócio se concretizar) de dono nesta transação?
   - "empresa": uma empresa/pessoa jurídica inteira ou uma participação societária nela (ações, cotas, fatia), inclusive fusão entre empresas, OPA/oferta pública de aquisição, fechamento de capital e participação em joint venture ou em empresa que detém uma marca;
   - "unidade_de_negocio": uma divisão, subsidiária ou operação de uma empresa vendida como negócio em funcionamento, com marcas, clientes e equipe (ex.: "operações brasileiras da General Mills", "divisão de vitaminas da Nestlé", "negócio de telefonia fixa da Oi");
   - "ativo": um bem ou conjunto de bens, e não um negócio — campo, bloco, prospecto, poço, acreage ou "operações/ativos" de petróleo e gás numa bacia ou região, participação (working interest) nesses ativos, reservas, usina ou planta, terreno, imóvel, prédio, lajes, lojas/pontos comerciais avulsos (sem a empresa ou a marca que as opera), navios/equipamentos, carteira de produtos/marcas/licenças, concessão. Participação percentual NUM ATIVO (ex.: "30% do bloco X", "interesses não operados em campos do Golfo") continua sendo "ativo";
   - "nenhum": não existe transação de compra, venda ou fusão — IPO, colocação de ações, compra de ações em bolsa/block trade/follow-on, investidor (ativista, fundo) montando ou aumentando posição numa companhia aberta, reorganização societária dentro do mesmo grupo, parceria, contrato, financiamento sem aquisição.
   Um negócio ainda não concluído (rumor, interesse de compradores, negociação, oferta, oferta rejeitada, processo de venda) é classificado pelo que ESTÁ SENDO negociado — nunca como "nenhum" só porque ainda não mudou de dono.

2. ATUAL — a transação é de agora? Se o artigo a situa em ano anterior ao da publicação ("em 2017", "em 1999", "no ano passado", "nos últimos 18 meses", "anteriormente", "já havia comprado"), ela é ANTIGA — mesmo que o artigo inteiro seja sobre M&A, sobre o setor ou sobre as mesmas empresas. Um desdobramento NOVO de um deal ainda não concluído (aprovação regulatória, oferta revisada, financiamento da aquisição pendente, rejeição de proposta, fechamento) conta como atual. Deal concluído AGORA e noticiado agora ("comprou", "concluiu a aquisição", "adquiriu" no texto de uma notícia sobre ele) também é atual — o verbo no passado não torna a transação antiga; o que conta é a data que o artigo atribui a ela.

3. FOCO — o artigo informa algo novo sobre ESTA transação, ou ela só aparece como exemplo, histórico, contexto, currículo de alguém, comparação ou lista de negócios anteriores?

Veredito: e_mna=true SOMENTE se OBJETO for "empresa" ou "unidade_de_negocio", E ATUAL, E FOCO, E as partes corresponderem ao artigo. Qualquer outro caso: e_mna=false.

Notas:
- O estágio não importa: rumor, interesse, negociação, oferta hostil ou rejeitada, anunciado, concluído ou cancelado — desde que o negócio exista e seja atual.
- Compra de participação relevante negociada com os sócios (fora de bolsa) é "empresa".
- A forma jurídica não decide: um "asset deal" que leva a operação, os clientes, os contratos e a equipe de um negócio de uma empresa é "unidade_de_negocio". "Ativo" é o bem sem o negócio que roda em cima dele.
- Pessoa física comprando bem pessoal (casa, carro, obra de arte, imóvel) é "ativo".
- Adquirente e alvo do mesmo grupo econômico (incorporação de controlada, fusão de subsidiárias, reorganização para IPO) é "nenhum".

Retorne JSON exato:
{{"objeto": "empresa|unidade_de_negocio|ativo|nenhum", "atual": true|false, "foco": true|false, "e_mna": true|false, "confianca": <0.0 a 1.0>, "motivo": "1 frase em português explicando"}}"""


# ── 3c. dedup semântico contra as operações recentes da base ─────────────────────
SYSTEM_DEDUP = ("Você é analista de M&A conferindo se um deal já está cadastrado. "
                "Responda SOMENTE com JSON válido, sem markdown.")

PROMPT_DEDUP = """Verifique se a OPERAÇÃO NOVA é a MESMA transação de alguma das OPERAÇÕES JÁ CADASTRADAS.

OPERAÇÃO NOVA
Adquirente: {adquirente}
Alvo: {alvo}
Valor: {valor} {moeda}
Data do artigo: {data}
Descrição: {descricao}

OPERAÇÕES JÁ CADASTRADAS (últimos {dias} dias)
{recentes}

É a MESMA transação quando envolve o mesmo negócio entre as mesmas partes, ainda que:
- os nomes estejam escritos de forma diferente ou mais/menos completa ("Paramount" vs "Paramount Global", "Warner" vs "Warner Bros. Discovery");
- OS PAPÉIS ESTEJAM INVERTIDOS — uma registra A comprando B e a outra B comprando A. As duas descrevem o MESMO deal entre as MESMAS DUAS PARTES: a extração automática erra a direção com frequência, e num deal entre duas empresas só existe uma transação. Direção oposta entre as mesmas duas partes, no mesmo período, É DUPLICATA;
- o valor divirja, esteja em outra moeda, ou falte em uma delas (US$ 110 bi vs US$ 81 bi do mesmo deal noticiado em dias diferentes);
- o status tenha evoluído (rumor -> anunciado -> concluído) ou a notícia seja um desdobramento (adiamento, aprovação, suspensão, questionamento judicial) do MESMO deal.

NÃO é a mesma transação quando o negócio em si é outro: partes diferentes, ou duas aquisições distintas do mesmo comprador, ou compra de ativos/unidades diferentes.

Retorne JSON exato:
{{"duplicata": true|false, "id_existente": <o id da operação cadastrada correspondente, ou null>, "motivo": "1 frase em português"}}"""


def _linha_recente(r: dict) -> str:
    """Uma operação já cadastrada, na forma compacta enviada ao LLM no dedup."""
    valor = r.get("valor")
    return (f"- id {r.get('ref')}: {r.get('empresa_adquirente') or 'n/d'} compra "
            f"{r.get('empresa_alvo') or 'n/d'} | valor "
            f"{valor if valor is not None else 'n/d'} {r.get('moeda') or ''} | "
            f"{r.get('data_artigo') or 'n/d'}")


class LimiteDiario(Exception):
    """Todas as chaves Groq esgotaram o limite (provavelmente diário/TPD). O chamador
    deve parar e persistir o parcial — a rodada seguinte retoma (resumível)."""


class ChavesInvalidas(Exception):
    """Nenhuma chave de LLM válida. Diferente de :class:`LimiteDiario`: esperar não
    resolve, é preciso renovar as chaves no .env. O chamador para a
    rodada e registra o motivo — retentar amanhã daria o mesmo erro."""


class GroqExtractor:
    """Extrai, valida e deduplica operações via LLM, pelo roteador de chaves.

    Envolve ``industry.shared.llm`` (rodízio entre as chaves Groq/Cerebras do
    modelo, bloqueio por chave em rate-limit, descarte de chave inválida ou sem
    crédito). O roteador espera sozinho cooldowns de até ``MAX_MINUTE_WAIT``;
    acima disso levanta ``CotaEsgotada`` -> :class:`LimiteDiario` (a rodada para e
    retoma depois). Sem chave viva, ``SemChavesValidas`` ->
    :class:`ChavesInvalidas` — esperar não resolve, a rodada para.

    Três chamadas distintas ao LLM, todas pelo MESMO pool (as três competem pelo
    mesmo orçamento de tokens):
      - :meth:`extract` — operações de um artigo;
      - :meth:`validar` — a operação extraída é M&A de verdade?
      - :meth:`checar_duplicata` — já está na base (últimos N dias)?
    """

    def __init__(self, max_hard_fails: int = 4) -> None:
        from industry.shared import llm

        try:
            self._pool = llm.roteador(C.GROQ_MODEL, max_espera=C.MAX_MINUTE_WAIT,
                                      timeout=C.GROQ_TIMEOUT)
        except llm.SemChavesValidas as exc:
            raise ChavesInvalidas(str(exc)) from exc
        self._max_hard_fails = max_hard_fails
        log.info("[mna] extrator pronto: modelo=%s", C.GROQ_MODEL)

    def _chamar_json(self, system: str, prompt: str, max_tokens: int,
                     etapa: str) -> tuple[dict | None, str | None]:
        """Uma chamada JSON ao pool, com a política de retry comum às 3 etapas.

        Returns:
            ``(dict, None)`` em sucesso; ``(None, motivo)`` quando a saída foi
            inválida ou os erros duros esgotaram as tentativas. O motivo SOBE
            para ser gravado na base — uma falha de LLM nunca deve virar
            "resultado válido" nem desaparecer só no log do Airflow.

        Raises:
            LimiteDiario: todas as chaves esgotadas com espera longa (TPD).
            ChavesInvalidas: nenhuma chave válida (renovar as Connections).
        """
        # ``e_chave_invalida`` vem do roteador de propósito: a definição de "erro de
        # chave" tem de ser a MESMA nos dois lados, senão o pipeline volta a
        # tratar 401 como erro duro genérico (e a revisão volta a ser fail-open).
        from industry.shared.llm import PoolExhausted, SemChavesValidas
        from industry.shared.llm import e_chave_invalida as _e_chave_invalida

        hard_fails = 0
        while True:
            try:
                resp = self._pool.create(
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0,
                    max_tokens=max_tokens,
                    reasoning_effort=C.REASONING_EFFORT,
                )
                content = (resp.choices[0].message.content or "").strip()
                out = json.loads(content)
                if isinstance(out, dict):
                    return out, None
                return None, f"{etapa}: JSON não é objeto"
            except SemChavesValidas as exc:
                # Não é retentável: sobe e para a rodada.
                raise ChavesInvalidas(str(exc)) from exc
            except PoolExhausted as exc:
                if exc.retry_after > C.MAX_MINUTE_WAIT:
                    raise LimiteDiario(str(exc)) from exc
                time.sleep(max(exc.retry_after, 1.0))
                continue
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                log.warning("[mna] %s: saída malformada do LLM.", etapa)
                return None, f"{etapa}: saída malformada ({type(exc).__name__})"
            except Exception as exc:  # noqa: BLE001 — erro duro não-rate-limit
                # Erro de chave (401/403) que escapou do pool NÃO é erro duro: se
                # caísse no contador abaixo, a revisão viraria fail-open ("operação
                # mantida sem revisão") por um problema de credencial. Sobe como
                # ChavesInvalidas para a rodada parar e alguém trocar a chave.
                if _e_chave_invalida(exc):
                    raise ChavesInvalidas(str(exc)) from exc
                hard_fails += 1
                if hard_fails >= self._max_hard_fails:
                    log.warning("[mna] %s falhou (erro não-rate-limit): %s", etapa, exc)
                    return None, f"{etapa}: {type(exc).__name__}: {str(exc)[:200]}"
                time.sleep(1.0 * hard_fails)

    def extract(self, titulo: str, fonte: str, data: str,
                texto: str) -> tuple[list[dict], str | None]:
        """Chama o LLM para um artigo.

        Returns:
            ``(operacoes, falha)``. ``falha`` não-nulo significa que a extração
            NÃO rodou de fato — o chamador deve registrar o motivo para o artigo
            voltar ao pool, em vez de gravá-lo como "0 operações".
        """
        prompt = PROMPT.format(titulo=titulo, fonte=fonte, data=data,
                               texto=texto[:C.EXTRACT_CHAR_LIMIT])
        out, falha = self._chamar_json(SYSTEM_MSG, prompt, C.EXTRACT_MAX_TOKENS, "extração")
        if out is None:
            return [], falha
        ops = out.get("operacoes", [])
        return (ops if isinstance(ops, list) else []), None

    def validar(self, op: dict, titulo: str, fonte: str, data: str,
                texto: str) -> tuple[bool, str, str | None]:
        """A operação extraída é realmente um M&A, e corresponde ao artigo?

        Returns:
            ``(aceita, motivo, falha)``. Em falha de LLM (saída malformada, erro
            duro) devolve ``aceita=True`` — na dúvida a operação ENTRA: o custo de
            perder um deal real é maior que o de deixar passar um falso positivo,
            e a falha é de infraestrutura, não julgamento do modelo. Nesse caso
            ``falha`` traz o motivo, para o artigo ser marcado como revisão
            DEGRADADA na base (e voltar ao pool).

        Raises:
            LimiteDiario: propagado para o chamador parar e persistir o parcial.
        """
        prompt = PROMPT_VALIDACAO.format(
            adquirente=op.get("empresa_adquirente") or "n/d",
            alvo=op.get("empresa_alvo") or "n/d",
            valor=op.get("valor") if op.get("valor") is not None else "n/d",
            moeda=op.get("moeda") or "",
            setor=op.get("setor") or "n/d",
            status=op.get("status") or "n/d",
            descricao=op.get("descricao") or "n/d",
            titulo=titulo, fonte=fonte, data=data,
            texto=(texto or "")[:C.VALIDACAO_CHAR_LIMIT],
        )
        out, falha = self._chamar_json(SYSTEM_VALIDACAO, prompt,
                                       C.VALIDACAO_MAX_TOKENS, "validação")
        if out is None:
            return True, "validação indisponível (falha de LLM) — operação mantida", falha
        motivo = str(out.get("motivo") or "").strip()
        # Diagnóstico da rubrica no motivo: a auditoria (mna_operations_rejeitadas)
        # mostra POR QUE saiu — ativo, deal antigo ou citação de passagem.
        diag = ", ".join(f"{k}={out[k]}" for k in ("objeto", "atual", "foco") if k in out)
        if diag:
            motivo = f"[{diag}] {motivo}"
        if out.get("e_mna") is False:
            conf = out.get("confianca")
            return False, (f"{motivo} (confiança {conf})" if conf is not None else motivo), None
        return True, motivo, None

    def checar_duplicata(self, op: dict,
                         recentes: list[dict]) -> tuple[bool, str, str | None, object]:
        """A operação já está na base (comparada às ``recentes``)?

        Args:
            op: operação candidata.
            recentes: operações já cadastradas, cada uma com ``ref`` (rótulo
                usado na resposta), ``empresa_adquirente``, ``empresa_alvo``,
                ``valor``, ``moeda``, ``data_artigo``.

        Returns:
            ``(e_duplicata, motivo, falha, ref)``. ``ref`` é a ``ref`` da operação
            cadastrada correspondente — SÓ quando está entre as ``recentes``
            enviadas (o LLM às vezes inventa um id); senão None. Sem recentes, ou em falha de LLM,
            ``e_duplicata=False`` — na dúvida a operação ENTRA (duplicata é menos
            grave que perda, e a consolidação por par ainda pega os casos de nome
            idêntico). ``falha`` traz o motivo quando o LLM não respondeu.

        Raises:
            LimiteDiario: propagado para o chamador parar e persistir o parcial.
        """
        if not recentes:
            return False, "", None, None
        linhas = "\n".join(_linha_recente(r) for r in recentes)
        prompt = PROMPT_DEDUP.format(
            adquirente=op.get("empresa_adquirente") or "n/d",
            alvo=op.get("empresa_alvo") or "n/d",
            valor=op.get("valor") if op.get("valor") is not None else "n/d",
            moeda=op.get("moeda") or "",
            data=op.get("data_artigo") or "n/d",
            descricao=op.get("descricao") or "n/d",
            dias=C.DEDUP_LLM_DIAS,
            recentes=linhas,
        )
        out, falha = self._chamar_json(SYSTEM_DEDUP, prompt, C.DEDUP_LLM_MAX_TOKENS, "dedup")
        if out is None:
            return False, "", falha, None
        if out.get("duplicata") is True:
            ref = out.get("id_existente")
            motivo = str(out.get("motivo") or "").strip()
            refs = {str(r.get("ref")): r.get("ref") for r in recentes}
            ref_ok = refs.get(str(ref).strip()) if ref is not None else None
            if ref is not None and ref_ok is None:
                log.warning("[mna] dedup apontou id %r fora das operações comparadas — "
                            "duplicata registrada sem mescla.", ref)
            return (True, (f"duplicata de id {ref}: {motivo}" if ref is not None else motivo),
                    None, ref_ok)
        return False, "", None, None


# ── 3d. revisão: validação semântica + dedup contra as recentes ──────────────────
def revisar_operacoes(extractor: "GroqExtractor", ops: list[dict], *, titulo: str,
                      fonte: str, data: str, texto: str, recentes: list[dict],
                      indice: list[dict] | None = None,
                      ) -> tuple[list[dict], list[dict], list[str]]:
    """Passa cada operação pela validação de "é M&A?" e pelo dedup semântico.

    A ordem importa: valida ANTES de deduplicar — não faz sentido gastar uma
    comparação de duplicidade com uma operação que nem é M&A. Cada operação
    aprovada entra em ``recentes`` (via retorno), para que duas notícias do MESMO
    deal na MESMA rodada não passem as duas.

    Args:
        extractor: instância já construída (compartilha o pool de chaves).
        ops: operações extraídas do artigo, já com os metadados do artigo.
        titulo/fonte/data/texto: o artigo de origem (contexto da validação).
        recentes: janela por data (últimos ``DEDUP_LLM_DIAS``) + as aprovadas
            nesta rodada. Rede ampla: pega duplicata mesmo quando os nomes não se
            parecem ("Meta" vs "Facebook").
        indice: TODAS as operações da base, para o blocking por nome. Pega o deal
            longo, noticiado por meses, que a janela por data não alcança.

    Returns:
        ``(aprovadas, rejeitadas, falhas)``. As rejeitadas levam
        ``motivo_rejeicao`` e ``etapa`` ("validacao" ou "dedup") para a tabela de
        auditoria; as de dedup levam também ``ref_existente`` (a operação
        cadastrada a atualizar via :func:`mesclar`, ou None). ``falhas`` lista as revisões que NÃO rodaram por falha de LLM
        (a operação entrou sem ser revisada) — o chamador grava isso no log do
        artigo para ele voltar ao pool.

    Raises:
        LimiteDiario: propagado (o chamador para e persiste o parcial).
    """
    aprovadas: list[dict] = []
    rejeitadas: list[dict] = []
    falhas: list[str] = []
    for op in ops:
        # Comparação = janela por data + operações do MESMO par de empresas em
        # qualquer data (deal longo é noticiado por meses). Sem o segundo termo, o
        # caso Paramount/Warner escaparia: as linhas vão de abril a julho.
        # Candidatos por nome entram PRIMEIRO: são o sinal de maior precisão e não
        # podem ser cortados pelo teto se a janela por data já estiver cheia.
        comparar = candidatos_dedup(op, indice or [], C.DEDUP_LLM_MAX_RECENTES)
        vistos = {r.get("ref") for r in comparar}
        for r in recentes:
            if len(comparar) >= C.DEDUP_LLM_MAX_RECENTES:
                break
            if r.get("ref") not in vistos:
                comparar.append(r)
                vistos.add(r.get("ref"))
        if C.VALIDAR_OPERACOES:
            aceita, motivo, falha = extractor.validar(op, titulo=titulo, fonte=fonte,
                                                      data=data, texto=texto)
            if falha:
                falhas.append(falha)
            if not aceita:
                log.info("[mna] rejeitada (não é M&A): %s / %s — %s",
                         op.get("empresa_adquirente"), op.get("empresa_alvo"), motivo)
                rejeitadas.append({**op, "motivo_rejeicao": motivo, "etapa": "validacao"})
                continue

        if C.DEDUP_LLM_OPERACOES:
            duplicata, motivo, falha, ref = extractor.checar_duplicata(op, comparar)
            if falha:
                falhas.append(falha)
            if duplicata:
                log.info("[mna] duplicata: %s / %s — %s",
                         op.get("empresa_adquirente"), op.get("empresa_alvo"), motivo)
                # ref_existente: a operação cadastrada que esta notícia ATUALIZA
                # (status/valor) — o chamador aplica a mescla. None = sem mescla.
                rejeitadas.append({**op, "motivo_rejeicao": motivo, "etapa": "dedup",
                                   "ref_existente": ref})
                continue

        aprovadas.append(op)
    return aprovadas, rejeitadas, falhas


# ── 4. consolidação (notebook _norm + dedup por par) ─────────────────────────────
_SUFIXOS = [r"\binc\.?\b", r"\bcorp\.?\b", r"\bltda\.?\b", r"\bs\.a\.?\b", r"\bsa\b",
            r"\bllc\b", r"\bplc\b", r"\bgroup\b", r"\bholding\b"]


def _norm_company(s) -> str:
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    for p in _SUFIXOS:
        s = re.sub(p, "", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ── blocking por nome: quais operações VALE comparar entre si ────────────────────
# Tokens sem poder discriminante: aparecem em dezenas de empresas distintas e, se
# considerados, casariam "Banco A / Banco B" com "Banco C / Banco D". Medidos por
# frequência na própria tabela.
_TOKENS_GENERICOS = {
    "capital", "energy", "energia", "grupo", "group", "ltd", "ltda", "holding",
    "holdings", "partners", "partner", "global", "brasil", "brazil", "management",
    "investimentos", "investment", "investments", "media", "banco", "banca", "bank",
    "resources", "oil", "gas", "technologies", "technology", "company", "corp",
    "corporation", "inc", "plc", "spa", "international", "business", "ativos",
    "unidade", "unidades", "subsidiaria", "subsidiarias", "estate", "real", "llc",
    "participacao", "participacoes", "acoes", "operacoes", "operacao", "divisao",
    "negocios", "the", "and", "des", "consorcio", "fundo", "fundos", "companhia",
    "sociedade", "limited", "com", "sas", "nao", "especificado", "potenciais",
    "compradores", "empresa", "empresas", "controlada", "controladas",
}
# Tokens de 2 letras CONTAM (BP, B3, C6, XP, 3M são o nome inteiro da empresa —
# descartá-los deixava "BP" e "C6 Bank" sem token nenhum, fora do blocking). Estes
# são as exceções: preposições, artigos e siglas societárias/geográficas.
_TOKENS_CURTOS_GENERICOS = {
    "de", "do", "da", "di", "du", "of", "la", "le", "el", "et", "em", "in", "na",
    "no", "to", "sa", "co", "ag", "nv", "bv", "se", "ab", "as", "oy", "me", "ep",
    "us", "uk", "eu", "eua",
}


def _sem_acento(s: str) -> str:
    """"Participações" -> "participacoes" (``_norm_company`` quebraria em "participa")."""
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def tokens_empresa(nome) -> set[str]:
    """Tokens discriminantes do nome de uma empresa, para blocking de duplicatas."""
    if not isinstance(nome, str):
        return set()
    limpo = re.sub(r"[^a-z0-9 ]", " ", _sem_acento(nome).lower())
    return {t for t in limpo.split()
            if len(t) > 1 and t not in _TOKENS_GENERICOS and t not in _TOKENS_CURTOS_GENERICOS}


def mesmo_par(op_a: dict, op_b: dict) -> bool:
    """As duas operações envolvem as MESMAS duas partes (em qualquer direção)?

    Blocking barato que decide o que vale mandar ao LLM. Aceita direção invertida
    de propósito: a extração troca adquirente/alvo com frequência e as duas linhas
    são o mesmo deal.
    """
    a1, b1 = tokens_empresa(op_a.get("empresa_adquirente")), tokens_empresa(op_a.get("empresa_alvo"))
    a2, b2 = tokens_empresa(op_b.get("empresa_adquirente")), tokens_empresa(op_b.get("empresa_alvo"))
    if not (a1 or b1) or not (a2 or b2):
        return False
    direta = bool(a1 & a2) and bool(b1 & b2)
    invertida = bool(a1 & b2) and bool(b1 & a2)
    return direta or invertida


def candidatos_dedup(op: dict, indice: list[dict], limite: int) -> list[dict]:
    """Operações do ``indice`` que vale comparar com ``op`` no dedup via LLM.

    Complementa a janela por data: um deal noticiado ao longo de meses (o caso
    Paramount/Warner tem linhas de abril a julho) NÃO seria pego só por
    "operações dos últimos N dias".
    """
    iguais = [r for r in indice if mesmo_par(op, r)]
    return iguais[:limite]


# ── mescla: a duplicata ATUALIZA a operação cadastrada em vez de sumir ───────────
# O dedup trata "rumor -> anunciado -> concluído" como a MESMA transação (correto),
# mas descartava a notícia nova inteira: o deal ficava parado no primeiro status
# noticiado. Caso real: Advent + Bain / Amil seguia como "rumor" na base depois de
# a notícia do cancelamento ter sido rejeitada como duplicata.
#
# A regra é determinística (sem LLM): o LLM só decide "é o mesmo deal?".
_ETAPA_STATUS = {
    "rumor": 1, "em estudo": 1, "planejado": 1, "proposta de parceria": 1,
    "em negociação": 2, "anunciado": 3, "aprovado": 4, "concluído": 5,
}
# Desfechos negativos: vencem qualquer etapa, desde que a notícia não seja ANTERIOR
# à cadastrada (um "cancelado" velho não derruba um "concluído" mais novo).
_STATUS_ENCERRADO = {"cancelado", "rejeitado", "recusada", "bloqueado"}
# Valor divergente NÃO substitui o cadastrado, só fica registrado: no histórico de
# 04/08 a 02/10/2026, metade das divergências era erro de magnitude da extração
# (Paramount/Warner com US$ 429 bi e US$ 8,1 bi; AMD/World Labs com US$ 44,3 bi).
_DIVERGENCIA_VALOR = 0.05
# Campos que acompanham o status: a linha passa a apontar para a notícia que
# trouxe a etapa mais avançada do deal.
_CAMPOS_ARTIGO = ("titulo_artigo", "link_artigo", "data_artigo", "fonte_artigo", "descricao")
# Preenchidos com o da notícia nova só quando a cadastrada não tem.
_CAMPOS_COMPLEMENTO = ("setor", "pais_adquirente", "pais_alvo", "descricao")


def _vazio(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    return isinstance(v, str) and v.strip().lower() in {"", "n/d", "null", "none", "nan"}


def _status(v) -> str:
    return v.strip().lower() if isinstance(v, str) else ""


def _data_iso(v) -> str:
    return "" if _vazio(v) else str(v)[:10]


def etapa_status(v) -> int:
    """Posição do status no ciclo do deal (0 = desconhecido; encerrado = 6)."""
    s = _status(v)
    return 6 if s in _STATUS_ENCERRADO else _ETAPA_STATUS.get(s, 0)


def mesclar(base: dict, nova: dict) -> tuple[dict, list[str]]:
    """O que a notícia ``nova`` (duplicata) muda na operação cadastrada ``base``.

    Returns:
        ``(alteracoes, divergencias)``. ``alteracoes`` = {campo: valor novo}, só os
        campos que mudam (vazio = nada a atualizar). ``divergencias`` = frases sobre
        o que conflita e NÃO foi aplicado (valor diferente, desfecho contraditório),
        para o histórico.
    """
    alt: dict = {}
    div: list[str] = []
    sb, sn = _status(base.get("status")), _status(nova.get("status"))
    db_, dn = _data_iso(base.get("data_artigo")), _data_iso(nova.get("data_artigo"))

    avancou = False
    if sn in _STATUS_ENCERRADO and sb not in _STATUS_ENCERRADO:
        if not db_ or not dn or dn >= db_:
            avancou = True
        else:
            div.append(f"status '{sn}' em notícia de {dn}, anterior à cadastrada ({db_})")
    elif sb in _STATUS_ENCERRADO and sn and sn not in _STATUS_ENCERRADO:
        div.append(f"cadastrada como '{sb}', notícia de {dn or 'n/d'} diz '{sn}'")
    elif _ETAPA_STATUS.get(sn, 0) > _ETAPA_STATUS.get(sb, 0):
        avancou = True
    if avancou:
        alt["status"] = nova.get("status")
        for c in _CAMPOS_ARTIGO:
            if not _vazio(nova.get(c)) and nova.get(c) != base.get(c):
                alt[c] = nova.get(c)

    # Valor 0 é "não informado" que a extração devolveu como número.
    vb, vn = (coerce_valor(base.get("valor")) or None), (coerce_valor(nova.get("valor")) or None)
    if vb is None and vn is not None:
        alt["valor"] = vn
        if not _vazio(nova.get("moeda")):
            alt["moeda"] = nova.get("moeda")
    elif vb is not None and vn is not None and vb != vn:
        if abs(vb - vn) / max(abs(vb), abs(vn)) > _DIVERGENCIA_VALOR:
            div.append(f"valor {vn:,.0f} {nova.get('moeda') or ''} (notícia de "
                       f"{dn or 'n/d'}) difere do cadastrado {vb:,.0f} "
                       f"{base.get('moeda') or ''}".replace("  ", " "))

    for c in _CAMPOS_COMPLEMENTO:
        if c not in alt and _vazio(base.get(c)) and not _vazio(nova.get(c)):
            alt[c] = nova.get(c)
    return alt, div


def escolher_sobrevivente(ops: list[dict]) -> dict:
    """A operação de um grupo de duplicatas que recebe as demais (mescla).

    Critério, em ordem: tem valor > mais campos preenchidos > etapa mais avançada do
    deal > artigo mais recente > ``op_id`` maior (determinismo). Como as outras são
    MESCLADAS nela (status, valor ausente), a escolha não perde informação — só
    decide qual linha e qual ``op_id`` ficam.
    """
    def peso(o: dict) -> tuple:
        preenchidos = sum(1 for v in o.values() if not _vazio(v))
        return (coerce_valor(o.get("valor")) is not None, preenchidos,
                etapa_status(o.get("status")), _data_iso(o.get("data_artigo")),
                int(o.get("op_id") or 0))

    return max(ops, key=peso)


def agrupar_por_par_exato(ops: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """Grupos (>1 linha) com o MESMO (adquirente, alvo) normalizado, e as linhas sem
    nome nenhum. Mesmo critério de :func:`consolidar_operacoes`."""
    grupos: dict[tuple[str, str], list[dict]] = {}
    sem_nome: list[dict] = []
    for op in ops:
        chave = (_norm_company(op.get("empresa_adquirente")), _norm_company(op.get("empresa_alvo")))
        if chave == ("", ""):
            sem_nome.append(op)
            continue
        grupos.setdefault(chave, []).append(op)
    return [g for g in grupos.values() if len(g) > 1], sem_nome


def consolidar_operacoes(operacoes: list[dict]) -> pd.DataFrame:
    """Deduplica operações por (adquirente, alvo) normalizados, mantendo o registro
    mais completo (mais campos não-nulos)."""
    if not operacoes:
        return pd.DataFrame()
    df = pd.DataFrame(operacoes)
    df["_adq"] = df.get("empresa_adquirente", "").apply(_norm_company)
    df["_alvo"] = df.get("empresa_alvo", "").apply(_norm_company)
    df["_comp"] = df.notna().sum(axis=1)
    df = df.sort_values("_comp", ascending=False)
    df = (df[df["_adq"].ne("") | df["_alvo"].ne("")]
          .drop_duplicates(subset=["_adq", "_alvo"], keep="first")
          .drop(columns=["_adq", "_alvo", "_comp"])
          .reset_index(drop=True))
    return df


# ── normalização de valor/data (recupera o valor absoluto; conserta separadores) ─
_SCALE_PATTERNS = [
    (1e12, re.compile(r"trilh|trillion|\btri\b|\d\s*t\b", re.I)),
    (1e9,  re.compile(r"bilh|billion|\bbi\b|\bbn\b|\d\s*b\b", re.I)),
    (1e6,  re.compile(r"milh|million|\bmi\b|\bmn\b|\bmm\b|\d\s*m\b", re.I)),
    (1e3,  re.compile(r"\bmil\b|thousand|\d\s*k\b", re.I)),
]


def _parse_scale(text: str) -> float:
    for mult, pat in _SCALE_PATTERNS:
        if pat.search(text):
            return mult
    return 1.0


def _parse_number(text: str) -> float | None:
    neg = text.strip().startswith("-")
    s = re.sub(r"[^\d,.]", "", text)
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = (s.replace(",", ".") if s.count(",") == 1 and len(s.rsplit(",", 1)[1]) != 3
             else s.replace(",", ""))
    elif "." in s:
        if not (s.count(".") == 1 and len(s.rsplit(".", 1)[1]) != 3):
            s = s.replace(".", "")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def coerce_valor(v) -> float | None:
    """Normaliza o valor extraído para float absoluto na unidade da moeda."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, float) and v != v:  # NaN
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if not s or s in {"null", "none", "n/a", "nan", "-"}:
        return None
    num = _parse_number(s)
    if num is None:
        return None
    return num * _parse_scale(s)


def coerce_data(v) -> str | None:
    """Devolve ``YYYY-MM-DD`` se o valor começar com uma data ISO, senão None."""
    s = str(v)[:10]
    return s if re.match(r"\d{4}-\d{2}-\d{2}", s) else None
