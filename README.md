# industry

Pipelines (Dagster) que alimentam o [home](../home). Toda escrita no Supabase passa por
aqui, e cada execução fica registrada em `pipelines` / `pipeline_runs`, que o home mostra
na página Pipelines.

```bash
pip install -e .
cp .env.example .env        # preencher SUPABASE_DB_URL
dagster dev                 # http://localhost:3000
```

## Assets

| grupo | asset | o que faz | agenda |
|---|---|---|---|
| jobs | `vagas_coletadas` | roda os coletores de `employment/employment/coletores` (conjunto de termos `dados` por padrão) e acrescenta em `employment/coletas/` | diária, 07:00 |
| jobs | `vagas_classificadas` | lê a descrição de cada vaga ainda não classificada e grava, por LLM (Groq, Cerebras e Mistral em paralelo, um worker por chave), a função real, os componentes do trabalho, o setor e a modalidade em `employment/classificacao/classificacao_coletas.csv`; incremental | diária, depois da coleta |
| jobs | `vagas_supabase` | sobe para `jobs` as vagas coletadas nos últimos 30 dias, com função, componentes, setor, modalidade e nível (estágio a lead) | diária, depois da classificação |
| prospects | `prospects_supabase` | sobe para `prospects` os recortes da Receita Federal exportados por `scripts/receita_federal/filtrar.py` | manual |
| outsourcers | `outsourcers_rf` | sobe para `outsourcers` as consultorias pequenas exportadas pelos filtros em `filtros/outsourcers/` | manual |
| outsourcers | `outsourcers_github` | donos de repositórios ativos com tópicos de MLOps/LLMOps/RAG/OR, filtrados por localização (Brasil por padrão) | semanal, segunda 08:00 |

Os parâmetros (fontes, termos, janela de dias, tópicos, localizações) são `Config` do
Dagster e podem ser trocados no Launchpad sem mexer no código.

### Licitações e notícias (portadas do `bateleur-pipelines`, Airflow → Dagster)

| grupo | asset(s) | o que faz | job / agenda (desligada por padrão) |
|---|---|---|---|
| licitacoes_coleta | `licitacoes_coleta_<fonte>` (15) | PNCP aberto (varredura de todos os órgãos, filtrada por ML/IA/dados/TI), PNCP por termo, diários oficiais municipais (Querido Diário), CPSI do BNDES, ComprasNet e bancos públicos | `licitacoes_job`, diária 10:00 |
| licitacoes | `licitacoes_bronze` → `silver` → `gold` → `gold_filtros` → `verticais` (+ `score_atrasados`, `prazos`) | medallion + score de relevância (keyword + zero-shot + LLM Groq) | idem |
| noticias | `noticias_coleta` → `noticias_limpas` → `noticias_classificadas` | 54 fontes ligadas (Agro, Óleo e Gás, Automotivo, Tecnologia, Indústria; as outras 52 seguem configuradas, desligadas em `SETORES_ATIVOS`) e classificação por setor | `noticias_job`, 05:00 e 20:00 |
| noticias_relatorio | `noticias_relatorios` | nota por setor (oportunidades de negócio + fatos de mercado para consultoria de ML/dados) e relatório HTML por geografia em `noticias.relatorios_email`; e-mail **desligado** e sem destinatários | `noticias_relatorio_job`, 08:00 |
| noticias_mna | `mna_operacoes` → `mna_relatorio` | extração de operações de M&A das notícias + relatório HTML/PDF (sem pool desde que o setor M&A foi desligado) | `noticias_mna_job`, 03:30 |

Detalhes do score e dos prompts em [`industry/licitacoes/README.md`](industry/licitacoes/README.md).

```bash
cp .env.example .env        # SUPABASE_DB_URL + GROQ_API_KEY_1..N
docker compose up -d --build    # Dagster em http://localhost:3001, Selenium, Postgres local
```

- **Sem GPU.** O LLM local da classificação de notícias (Ollama/Qwen 3.8 27B) virou o mesmo
  modelo via API; o zero-shot mDeBERTa (licitações e gate do M&A) roda em CPU — o M&A limita
  `MNA_LIMIT` artigos por rodada para não travar a máquina.
- **Roteador de chaves** (`industry/shared/llm.py`). Toda chamada de LLM passa por ele: lê
  `GROQ_/CEREBRAS_/MISTRAL_API_KEY_N` do `.env`, faz rodízio entre as chaves dos provedores
  que servem o modelo e troca de chave sozinho (429 bloqueia a chave pelo tempo pedido;
  401/403/402 tira a chave do rodízio; 5xx/rede esfria; 400 tenta outro provedor). Modelos:
  `gpt-oss-120b` (Groq + Cerebras), `qwen3.8-27b` (Groq + Cerebras), `gpt-oss-20b` (Groq).
  A Mistral não tem nenhum desses, então só entra se um modelo dela for usado. Teste:
  `python tests/shared/test_llm.py`.
- **Bancos.** Licitações gravam no schema `licitacoes` de `LICITACOES_PG_URL` (padrão:
  Supabase, para o home ler). Notícias gravam no schema `noticias` de `NOTICIAS_PG_URL`
  (no compose, o Postgres local): o texto integral dos artigos não cabe no plano grátis.
- **Sem e-mail.** Os e-mails e o alerta de falha da origem saíram; o alerta virou o asset
  check `licitacoes_coletas_ok`, e o resultado é lido pelo home.
- **Prompts.** Notícias: oportunidades de negócio e fatos que movem o mercado, para uma
  consultoria de ML Engineering, Data Management e Data Engineering. Licitações: ML
  Engineering, AI Engineering, Data Science, Forecasting e Research Operations.

## Decisões

- **A coleta da Receita Federal não é DAG.** São ~7 GB que mudam uma vez por mês e são
  filtrados à mão, então vive em `codigos/scripts/receita_federal`. O Dagster só lê os
  parquets exportados.
- **Upsert não apaga o que o home edita.** As pipelines mandam só colunas de dado; `status`,
  `notas`, `site` e `contato` são do home. Testado contra Postgres: uma ressincronização
  mantém o que foi editado.
- **LinkedIn não é coletado.** Exige login e os termos de uso proíbem scraping. Perfis de
  lá entram pelo cadastro manual na página Outsourcers do home.
- **Conexão direta no Postgres**, não pela API REST do Supabase. Use a connection string do
  *session pooler*, porque a conexão direta do plano grátis é só IPv6.

## Próximo

- Licitações públicas: nova frente com coleta, tabela e página própria no home.
- Enriquecimento de prospects (site, formas de contato) gravando em `prospects.contato`.
