"""Constantes de domínio da pipeline de operações de M&A (notícias).

Extração de operações (transações) de M&A a partir das notícias já
classificadas no setor "M&A". Porte de produção do notebook analiseMnA /
noticias_dash (src/intelligence/mna_pipeline.py), adaptado para:

  - ler o pool de candidatos do Postgres (schema ``noticias``) em vez de CSV;
  - usar o roteador de chaves de LLM (``industry.shared.llm``);
  - persistir as operações no Postgres (``noticias.mna_operations``) em vez do
    DuckDB local do dashboard.

Args do DAG (schedule, tags, etc.) ficam em ``dag.py`` — aqui só constantes.
"""

# -----------------------------------------------------------------------------
# Banco — INDUSTRY_PG_URL (ver .env.example)
# Escrita legítima do PIPELINE no schema noticias (o read-only vale só p/ o
# dashboard noticias_dash, que apenas LÊ estas tabelas).
# -----------------------------------------------------------------------------
TABELA_POOL = "noticias.noticias_classificadas"   # origem: notícias já classificadas
TABELA_OPERACOES = "noticias.mna_operations"       # destino: transações extraídas
TABELA_LOG = "noticias.mna_extraction_log"         # controle de resumo (links processados)
TABELA_REJEITADAS = "noticias.mna_operations_rejeitadas"  # auditoria do que foi descartado
TABELA_EXECUCOES = "noticias.mna_execucoes"        # 1 linha por rodada (status + contadores)
TABELA_PENDENCIAS = "noticias.mna_pendencias"      # estado ATUAL do que ficou sem processar
TABELA_EVENTOS = "noticias.mna_operations_eventos"  # histórico: cada notícia mesclada num deal

# Setores (rótulo em noticias_classificadas.setor) que compõem o pool de M&A.
SETORES_MNA = ["M&A"]

# Limite de artigos por rodada. Na origem era None (GPU RTX 5090: 0,3 min para o
# gate zero-shot). Aqui o mDeBERTa roda em CPU — no worker de CPU da origem um
# backlog grande passou de 78 min sem terminar —, então cada rodada avalia no
# máximo MNA_LIMIT artigos no gate; o resto fica para as próximas (a extração é
# resumível: pula links já em TABELA_LOG). Volume diário normal fica abaixo disso.
import os  # noqa: E402

MNA_LIMIT = int(os.getenv("MNA_LIMIT", "400")) or None

# -----------------------------------------------------------------------------
# LLM — roteador de chaves (industry.shared.llm): gpt-oss-120b na Groq e na
# Cerebras, chaves no .env.
# gpt-oss-120b: context_window 131072 (idêntico ao antigo llama-3.3-70b);
# reasoning_effort="low" corta ~metade dos tokens de saída sem perder
# qualidade de extração (validado). max_tokens folgado: a saída JSON de 1
# artigo fica < 500 tokens mesmo com reasoning.
# -----------------------------------------------------------------------------
GROQ_MODEL = "openai/gpt-oss-120b"
REASONING_EFFORT = "low"
EXTRACT_MAX_TOKENS = 2048
GROQ_TIMEOUT = 60.0
# Espera máxima (s) por cooldown de rate-limit por-minuto antes de desistir do
# artigo (será retentado na próxima rodada — resumível). Acima disso presume-se
# limite diário (TPD) e a rodada para, persistindo o parcial.
MAX_MINUTE_WAIT = 75.0

# -----------------------------------------------------------------------------
# Classificação (gate) — keyword/regex + zero-shot NLI (mDeBERTa), fiel ao
# notebook. O pool já vem pré-filtrado por setor="M&A" (classificação por LLM
# via Groq); o zero-shot é uma SEGUNDA peneira sobre esses candidatos.
# -----------------------------------------------------------------------------
ZEROSHOT_MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
ZEROSHOT_LABELS = ["fusão ou aquisição de empresas", "outra notícia de negócios"]
ZEROSHOT_TEMPLATE = "Este texto é sobre {}."
ZEROSHOT_THRESHOLD = 0.70            # zero-shot forte -> positivo
ZEROSHOT_THRESHOLD_SECONDARY = 0.60  # zero-shot no limiar inferior + keyword -> positivo
ZEROSHOT_CHAR_LIMIT = 2000           # truncagem p/ mDeBERTa (~512 tokens)
ZEROSHOT_BATCH_SIZE = 16
MIN_MATCHES_REGEX = 2                # nº mínimo de padrões keyword p/ o baseline

# -----------------------------------------------------------------------------
# Dedup / extração
# -----------------------------------------------------------------------------
DEDUP_JACCARD = 0.25       # limiar de Jaccard de títulos (mesmo deal em várias fontes)
EXTRACT_CHAR_LIMIT = 5000  # truncagem do texto do artigo enviado ao LLM
PERSIST_EVERY = 25         # flush incremental a cada N artigos processados

# -----------------------------------------------------------------------------
# Validação semântica pós-extração (varredura de falsas operações)
# -----------------------------------------------------------------------------
# O gate zero-shot filtra ARTIGOS; nada filtrava as OPERAÇÕES que o LLM devolve.
# Auditoria manual de 20 amostras (jul/2026) achou 4 falsos positivos, todos com
# status "normal" (anunciado/concluído) — o campo status NÃO sinaliza o problema:
#   - disputa de herança/controle familiar tratada como transação;
#   - compra de fatia minoritária em bolsa (5% da Lucid) como aquisição;
#   - block trade / placement de ações (Tenaga) como aquisição;
#   - par de empresas que não corresponde ao artigo (TPC/JSL) — má extração.
# Cada operação vai numa chamada própria ao LLM (contexto pequeno, verdict curto):
# uma pergunta por operação erra menos que um lote pedindo N julgamentos.
VALIDAR_OPERACOES = True
VALIDACAO_MAX_TOKENS = 512
VALIDACAO_CHAR_LIMIT = 3000   # trecho do artigo enviado junto da operação

# Dedup semântico contra as operações recentes já na base: a consolidação por
# (adquirente, alvo) normalizados NÃO pega "Paramount Global" vs "Paramount" vs
# "Warner" — o mesmo deal entra 2-3x em fontes diferentes. O LLM compara a
# operação candidata com as operações dos últimos N dias.
DEDUP_LLM_OPERACOES = True
DEDUP_LLM_DIAS = 5
DEDUP_LLM_MAX_TOKENS = 512
DEDUP_LLM_MAX_RECENTES = 120  # teto de operações recentes enviadas por comparação

# -----------------------------------------------------------------------------
# Rastreio de falhas (o que NÃO foi processado precisa aparecer na BASE)
# -----------------------------------------------------------------------------
# Quando as chaves Groq esgotam (TPD) a rodada para no meio: os artigos restantes
# não entram no log de processados (para serem retomados), mas sem registro em
# tabela isso só aparecia no log do Airflow. TABELA_EXECUCOES guarda o desfecho de
# cada rodada e TABELA_PENDENCIAS o estado atual do que ficou de fora.
#
# Artigos em que a chamada ao LLM FALHOU (saída malformada, erro duro, chave
# morta) são marcados com ``motivo_falha`` e ficam FORA de links_processados
# enquanto ``tentativas < MAX_TENTATIVAS_ARTIGO`` — voltam ao pool na rodada
# seguinte em vez de virar um "0 operações" silencioso e definitivo. O teto evita
# retentar para sempre um artigo que sempre falha.
MAX_TENTATIVAS_ARTIGO = 3
