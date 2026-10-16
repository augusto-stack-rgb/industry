# COMERCIAL_Licitacoes — Decisões de Arquitetura

Registro das decisões de projeto do pipeline de licitações de bancos. Mantém o
*porquê* das escolhas para não serem revertidas por engano.

## Visão geral do fluxo

```
coletores → montar_bronze → montar_silver → montar_gold → montar_gold_filtros → enviar_email
 (portais)    (landing)       (limpo)       (filtro palavras)   (score/GPU)       (Excel)
```

O pipeline é **incremental (delta)**, não snapshot. Cada estágio só processa ids
ainda não vistos (`existing_ids(tabela)`), então **cada licitação passa pela esteira
uma única vez** — na run em que é coletada pela primeira vez. As tabelas guardam o
histórico completo; o **email é só o delta** daquela run (anti-spam).

---

## 1. Infra / execução

- **Task com GPU:** `montar_gold_filtros` roda com `@task(queue="gpu")` (zero-shot
  mDeBERTa na RTX 5090 + score LLM Groq). Só o serviço **`airflow-worker-gpu`**
  (`celery worker -q gpu`, concurrency=1) consome essa fila.
  - **Decisão (14/07):** o worker gpu foi adicionado ao `docker-compose.local.yaml`
    (antes só existia no `docker-compose.yaml`). Sem ele, a task fica presa em
    `queued` para sempre e o email sai "Nenhuma nova" falso.
  - Requer `--build` (torch cu130 + transformers no requirements) e NVIDIA Container
    Toolkit no host.
- **Cuidado:** há dois stacks Airflow em paralelo — `bateleur-pipelines_dev6` (código
  novo, este) e um `airflow` legado (código antigo, tasks consolidar/inserir_no_banco).
  Não confundir metadado/logs.

## 2. Bronze — dedup e integridade

- **Dedup por `row_hash` E por `id_unificado`.** O PK do bronze é `id_unificado`.
  - **Bug corrigido (14/07):** o dedup era só por `row_hash`; uma licitação
    re-coletada com conteúdo alterado gerava row_hash novo, passava pelo filtro e
    colidia no `bronze_pkey`. Adicionado filtro extra por `id_unificado` já existente
    (`existing_ids("bronze")`). Bronze é landing **imutável** (first-seen wins).

## 3. Recência — o que é "novo" vs "recente"

- **"Novo" ≠ "recente".** `run_date` (quando coletamos) é independente da data de
  publicação do portal. Editais antigos entram como "novos" quando vistos pela 1ª vez
  (backlog de runs que falharam, defasagem do índice de busca do portal).
- **Decisão (15/07):** filtrar o que segue para o email por **recência de portal**,
  não por coleta. Janela = hoje + ontem (`RECENCIA_DIAS=2`, 1 dia de folga).
  - Usa a data de **publicação** (`data`/`data_publicacao`), **nunca** abertura/sessão
    (datas futuras de evento). Ignora datas no futuro.
  - **Fonte sem data de publicação → fallback `run_date`** (1ª vez que coletamos).
    Decidido com o usuário: não perder fontes que a origem não datou.
- **Corte grosso no bronze + corte fino no gold** (ver seção 6): o bronze poda backlog
  antigo (`JANELA_BRONZE_DIAS`, poupa download de anexos); o gold aplica o critério
  preciso de "acionável".

## 4. Coleta de datas por fonte (auditoria 15/07)

Nem toda fonte publica data de publicação. Situação real:

| Fonte | Data no portal | Coletamos? | Nota |
|---|---|---|---|
| PNCP | publicação (ISO) | ✅ | `data_publicacao_pncp` |
| Banestes | publicação (DD/MM) | ✅ | |
| Banrisul | publicação | ✅ | em `data_publicacao` (não `data`) |
| Banrisul TCE-RS | **sessão** (não publicação) | ✅ | data futura → recência usa run_date |
| B3 | ✅ | ✅ | DD/MM |
| Banco Amazônia | ✅ (86%) | ✅ | DD/MM |
| BCB | só **abertura** (`DataAbertura`) | ✅ | GRUPOS já mapeia p/ `data_abertura` → silver |
| BRB | só **sessão** (`realizacao`) | ✅ | GRUPOS já mapeia p/ `data_sessao` → silver |
| BDMG | sessão (na pág. detalhe) | ✅ **corrigido** | regex quebrado (`DATA e HORÁRIO: …`) — consertado 15/07 |
| CAIXA SICVE | "Data de Publicação no DOU" (detalhe) | ⚠️ **ligado** | enriquecimento (era desligado) só p/ certames novos |
| ComprasNet | contratos: vigência | ✅ **corrigido** | `data=""` fixo → cadeia de campos (a validar em run real) |

- **Correção BDMG:** regex tolerante a rótulo com texto entre a palavra e a data.
- **CAIXA:** enriquecimento de detalhe agora ligado (`CAIXA_MAX_ENRICH=80`), mas só
  para certames **ainda não no bronze** (pula os já coletados — barato).
- **ComprasNet:** contratos passam a capturar `dataVigenciaInicio`/`dataAssinatura`/…
  (cadeia de nomes prováveis; não verificado com resposta ao vivo).

## 5. Score e email

- **Score combinado** = média ponderada de 3 sinais: keyword (`s_kw`), zero-shot
  mDeBERTa (`s_zs`) e LLM Groq (`s_llm`).
  - **Decisão (15/07):** pesos **LLM 70% / keyword 15% / zero-shot 15%**
    (`W_KW, W_ZS, W_LLM = 0.15, 0.15, 0.7`). O LLM é o sinal mais alinhado ao negócio
    (consultoria/assessoria financeira); keyword e zero-shot produziam falsos-positivos
    que afundavam os relevantes. Antes eram pesos iguais e o ranking era ruído.
- **Colunas do Excel do email (decisão 15/07):** só **Fonte, Tipo, Objeto, Valor,
  Data, Situação, Contratado, Link, Score de Relevância** (score por último, renomeado).
- **Bug corrigido (15/07):** `to_excel` quebrava com `IllegalCharacterError` — objetos
  com caracteres de controle (`\x0b`). Sanitiza com `ILLEGAL_CHARACTERS_RE` antes de gerar.
- **Falha de design (aberta):** `enviar_email` tem `trigger_rule="all_done"` +
  `(gold_result or {}).get("count",0)` → se `montar_gold_filtros` não roda, manda
  "Nenhuma nova" mascarando a falha. Corrigir tratando XCom nulo como erro.

## 6. Filtro "acionável" por janela de proposta (decisão 15/07)

**Problema:** filtrar por data de **publicação** é proxy ruim de "vale a pena agir".
O sinal correto é **"a janela de propostas ainda está aberta"** (`dataEncerramentoProposta`).

| Caso | Publicado | Prazo | Filtro por publicação | Filtro por prazo |
|---|---|---|---|---|
| Pregão aberto, publicado há 30d | 15/06 | 22/07 | ❌ corta (perde oportunidade viva) | ✅ mantém |
| Pregão publicado ontem, prazo aberto | 14/07 | 25/07 | ✅ mantém | ✅ mantém |
| Dispensa recente, prazo vencido | 12/07 | 13/07 | ✅ mantém (já fechado) | ❌ corta |
| Inexigibilidade antiga (contrato direto) | 28/02 | sem prazo | ❌ corta | ❌ corta (fallback recência) |

**Restrições descobertas:**
- `dataEncerramentoProposta` **não vem na API de busca** do PNCP — só na API de
  **detalhe** (`/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}`), 1 chamada HTTP por
  edital. A API de detalhe é **instável** (reseta conexão) → exige retry + pausa.
- Só o PNCP tem esse dado. Outras fontes não expõem prazo → caem na recência.

**Desenho implementado:**
```
bronze:  corte GROSSO por publicação (JANELA_BRONZE_DIAS=120, fallback run_date)
         → poda backlog antigo, poupa download de anexos
gold:    (após filtro de palavras — conjunto pequeno)
         enriquece dataEncerramentoProposta SÓ dos itens PNCP (paced, com retry)
         mantém se:  prazo >= hoje                       (janela aberta)
                     OU  sem prazo E publicado hoje/ontem (fallback recência)
                     OU  falha na API                     (fallback recência — nunca perde por erro)
```
- Enriquecer só os sobreviventes do filtro de palavras (dezenas/run) mantém barato.
- Fallback seguro em toda falha de API: cai na recência por publicação.
- Custo: ~dezenas de chamadas HTTP extra/run + pausa; dependência de API instável do PNCP.

## 7. Delta vs Snapshot — dois modelos, duas DAGs (decisão 15/07)

O pipeline tem **dois filtros em série** (E lógico): `email = É_NOVO (id nunca visto) E É_ACIONÁVEL`.
O filtro de "novo" (dedup no bronze) vem primeiro — por isso re-rodar quase nunca traz
nada: já foi tudo coletado. O filtro acionável (§6) só refina *quais dos novos* valem email.

Isso dá o modelo **DELTA**: cada licitação notificada **uma vez**, na run em que aparece.
Bom para produção (anti-spam), ruim para "ver tudo que está aberto agora".

Para "ver todas as abertas sempre", é o modelo **SNAPSHOT** — lê a base, ignora o "novo".

**Decisão:** manter os dois, em DAGs separadas.
- **`COMERCIAL_Licitacoes`** (delta) — agendada, **produção**. Inalterada.
- **`INTERNO_licitacoes_Snapshot`** (snapshot) — **`schedule=None`** (disparo manual),
  ferramenta de **dev**. Nunca roda sozinha em produção. Lê `gold_filtros ⨝ prazos_proposta`,
  filtra `data_encerramento_proposta >= hoje` (todas as abertas), ordena por Score de
  Relevância, gera Excel (colunas do delta + "Prazo p/ proposta") + email. Não coleta nada.

**Persistência do prazo (tabela nova, não altera schema existente):**
- Tabela `licitacoes_novo.prazos_proposta` (id_unificado PK, data_abertura_proposta,
  data_encerramento_proposta, situacao_pncp, atualizado_em).
- Populada pelo `filtrar_acionaveis` (§6): quando consulta o prazo de um item PNCP, faz
  upsert aqui (inclusive dos que ele descarta — o snapshot precisa saber o prazo de todos).
- O snapshot lê dessa tabela → barato (sem re-consultar o PNCP).
- **Bootstrap:** a tabela começa vazia e enche conforme as runs do delta processam editais
  PNCP. Um snapshot logo após o deploy pode vir curto até o delta popular os prazos.
  A própria DAG de snapshot faz **backfill** dos prazos faltantes (itens PNCP do
  gold_filtros sem prazo), bounded (`MAX_BACKFILL`) e paced — autossuficiente.

## 8. Como uma entrada é avaliada — metaprompt (LLM) e termos do mDeBERTa

O score de cada licitação combina 3 sinais (§5). Os dois "inteligentes" são:

### 8.1 LLM (Groq `openai/gpt-oss-120b`) — o metaprompt (`config.LLM_SYS`)

**Decisão (15/07):** o LLM passou a devolver uma **NOTA 0-100** (era `relevante` true/false).
Motivo: com sim/não, o sinal `s_llm` só empurrava para 1.0 ou 0.0 — mesmo tendo o maior peso
(0.7). Com nota, `s_llm` fica **contínuo** (`nota/100`) e o ranking fica muito mais fino.

Enviado como *system prompt*; o *user message* é um JSON `[{"id","texto"}]` das licitações,
e a resposta é JSON `{"resultados":[{"id","score","areas","motivo"}]}`. Texto literal:

> A Bateleur é uma empresa especializada em SERVIÇOS FINANCEIROS DE CONSULTORIA
> ESTRATÉGICA. Áreas de atuação: (A) assessoria financeira e estratégica; (B) consultoria
> especializada em finanças; (C) planejamento estratégico e business consulting; (D)
> modelagem de risco de crédito. Para CADA licitação, dê uma NOTA de 0 a 100 de quanto o
> OBJETO é a CONTRATAÇÃO DE SERVIÇOS DE CONSULTORIA/ASSESSORIA em que a Bateleur seria a
> CONTRATADA prestando o serviço (100 = é claramente esse tipo de consultoria/assessoria;
> 0 = claramente não é; use a faixa toda para o meio-termo). Seja rigoroso. Notas BAIXAS
> para: fornecimento de material/equipamento, obra de engenharia, limpeza, vigilância,
> transporte, TI/software genérico, eventos, patrocínio, E TAMBÉM leilões/concessões/PPPs/
> alienação ou venda de ativos/contratos em que o vencedor OPERA ou EXPLORA um ativo (não
> presta consultoria). Notas ALTAS apenas quando se contrata CONSULTORIA/ASSESSORIA
> financeira, econômica, estratégica, atuarial, de risco de crédito, valuation, modelagem
> financeira, due diligence, ou planejamento/business consulting. A Bateleur TAMBÉM presta
> (trate como ALTA relevância quando o objeto envolver): modelagem financeira; viabilidade
> financeira; modelagem de churn; alavancas de caixa; avaliação financeira de alternativas
> estratégicas. Responda SOMENTE JSON: {"resultados":[{"id":N,"score":0-100,"areas":["A"...],
> "motivo":"≤12 palavras"}]} — um objeto por licitação, mesma ordem/id.

**Decisão (15/07):** adicionados ao prompt os 5 serviços acima (que a Bateleur também presta).

Saída → `s_llm = nota/100` (contínuo); `gptoss_relevante` **derivado** (nota ≥ 50 → "sim",
senão "não" — mantido só para exibição/compat); `gptoss_areas` (A-D); `gptoss_motivo`.
Nota crua fica recuperável em `s_llm × 100` (não há coluna nova — sem mudança de schema).

### 8.2 Zero-shot mDeBERTa — modelo, hipótese e termos (`config.TEMAS`, `ZS_*`)

- **Modelo:** `MoritzLaurer/mDeBERTa-v3-base-mnli-xnli` (roda na GPU).
- **Hipótese:** `"Este edital de licitação trata de {}."` (o `{}` = label do tema).
- **Threshold:** `ZS_THRESHOLD = 0.62` (score zero-shot ≥ 0.62 marca o tema).
- **3 temas** — cada um com um *label* (frase usada como candidate_label do zero-shot) e uma
  lista de *keywords* (usadas no sinal keyword `s_kw`, casadas no objeto normalizado):

| Tema | Label (candidate_label do zero-shot) |
|---|---|
| `consultoria_estrategica` | "planejamento estratégico ou consultoria estratégica e de gestão" |
| `risco_credito` | "modelagem de risco de crédito" |
| `servicos_financeiros` | "serviços financeiros, consultoria financeira, contábil ou atuarial" (decisão 15/07: **removido "auditoria"**) |

**Labels EXTRA do zero-shot (`ZS_LABELS_EXTRA`, decisão 15/07)** — serviços que a Bateleur
também presta, adicionados como candidate_labels. **Entram no `s_zs` (máximo), mas NÃO viram
colunas `tag_zs_*`** (sem mudança de schema no gold_filtros):
`modelagem financeira` · `viabilidade financeira` · `modelagem de churn` ·
`alavancas de caixa` · `avaliação financeira de alternativas estratégicas`.

Keywords por tema (`TEMAS[tema]["kw"]`):
- **consultoria_estrategica:** planejamento estrategico, consultoria estrategica, consultoria de
  gestao, plano de negocios, plano diretor, governanca corporativa, reestruturacao,
  transformacao digital, assessoria estrategica, estudo de viabilidade, mapeamento de processos,
  gestao estrategica, planejamento institucional, consultoria organizacional, plano estrategico,
  gestao de mudanca, redesenho de processos, eficiencia operacional, consultoria empresarial.
- **risco_credito:** risco de credito, modelagem de risco, modelo de risco, rating, scoring,
  score de credito, perda esperada, perdas esperadas, provisao, ifrs 9, resolucao 4966, basileia,
  stress test, teste de estresse, inadimplencia, credit risk, capital regulatorio, rwa, pd lgd,
  ead, risco de mercado, risco operacional, modelos preditivos de risco, validacao de modelos,
  gestao de risco.
- **servicos_financeiros:** servicos financeiros, consultoria financeira, assessoria financeira,
  gestao financeira, tesouraria, contabil, auditoria, atuarial, valuation, avaliacao
  economico-financeira, fusoes e aquisicoes, m&a, mercado de capitais, due diligence, modelagem
  financeira, precificacao, derivativos, alm, compliance, consultoria economica, consultoria
  tributaria, pericia contabil, calculo atuarial, financas, investimentos.

Combinação final: `score_combinado = 0.15·s_kw + 0.15·s_zs + 0.7·s_llm` (renormalizado quando
falta sinal); `relevante_combinado = 1` se `score_combinado ≥ SCORE_THRESHOLD (0.5)`. Ver §5.

## 9. Email — corpo + Excel (estado ATUAL, consolidado 16/07)

> Esta seção substitui as versões anteriores (que evoluíram de 2→3→4 abas e de "dia"→"semana").
> É a descrição vigente. Config: `EMAIL_SCORE_MIN=0.35`, `DELTA_DIAS=7`.

**Sempre enviado**, mesmo sem novas (antes abortava em "Nenhuma nova" sem anexo).
**Arquivamento (decisão 16/07):** o `.xlsx` do email é salvo no MinIO em
`COMERCIAL_Licitacoes/<run_id>/email_licitacoes_<data>.xlsx` (best-effort; antes só os CSVs
intermediários iam pro MinIO, o Excel do email era efêmero).

**Corpo:** as **novas da semana** com `score_combinado > EMAIL_SCORE_MIN (0,35)`, em ordem de
score, mostrando **apenas o Objeto**. (Corte 0,35 desde 16/07; era 0,5.)

**Excel — até 4 abas:**
1. **Relevantes (score>0,35)** — catálogo: TODOS os relevantes da base (`gold_filtros`),
   ordenados por score. Sempre presente. Coluna **"Prazo p/ proposta"** (ver §10):
   data (aberto confirmado) / `fechado (data)` / `sem janela (inexigibilidade)` /
   `prazo não consultado`. **Não usa mais "em aberto"** para prazo não confirmado.
2. **Todos do dia (pós-palavras)** — coletadas HOJE que passaram no **filtro de palavras**
   (gold ⨝ bronze por `run_date = hoje`), antes de score/janela. Vazia quando o filtro remove
   tudo do dia (ex.: 16/07 = 0, todos os 25 coletados eram material/vigilância/eventos).
3. **Novas na semana - editais** — scoradas nos últimos `DELTA_DIAS` dias (`gold_filtros` por
   `run_date`), categoria edital (não contrato/ata/inexig).
4. **Novas na semana - contr-inexig** — idem, categoria contrato/ata + inexigibilidade.

**Janela "novas" = SEMANA por DATA EFETIVA (decisão 16/07):** abas 3-4 e o corpo filtram a
**data efetiva** em **`[hoje-6, hoje]`** (últimos `DELTA_DIAS`=7 dias). Data efetiva:
```
data_efetiva = data de publicação, se existir e não for futura (<= hoje)
               SENÃO data de coleta (run_date = 1ª vez que a vimos)
```
- Só **PNCP, Banestes, Banrisul** têm data de **publicação** real (ver §11). Para elas, usa a
  publicação → exclui backlog antigo (publicado há meses).
- Para as **demais** (sem data de publicação: BCB, BRB, BDMG, Banrisul-TCE, ComprasNet, B3,
  CAIXA…), usa a **data de coleta**: "não estava ontem, apareceu hoje → data = hoje". É a
  melhor inferência quando a fonte não expõe publicação (assume coleta diária/confiável).
- **Futuro excluído:** data de publicação futura (ex.: sessão do Banrisul-TCE) não conta como
  publicação → cai no run_date.
- **NÃO usar run_date puro** (o que trazia backlog: publicado há meses, processado só agora).
- ⚠️ Janela móvel: DAG diário repete as mesmas por 7 dias; para digest sem repetição, agendar semanal.

**Categorização edital vs contr-inexig** (`tipo_documento`/modalidade do bronze via
`db.meta_bronze`, `tipo` normalizado por `db.normalizar_modalidade`): `tipo_documento` ∈
{contrato, ata} OU modalidade = Inexigibilidade → **contr-inexig**; senão → **editais**.

**O que alimenta o "novas" — `filtrar_acionaveis` (§6):** descarta **apenas editais PNCP com
janela JÁ FECHADA** (`dataEncerramentoProposta < hoje`); mantém o resto. Backlog muito antigo
é podado no **bronze** (`JANELA_BRONZE_DIAS = 120`).

**Limitações:**
- Contratos publicados há +120 dias são podados no bronze (aumentar `JANELA_BRONZE_DIAS` se preciso).
- `prazos_proposta` é populada aos poucos (API PNCP instável) → muitos relevantes ficam
  "prazo não consultado" até o delta preencher.
- Normalização de `tipo` é só de **exibição**; dados nas tabelas seguem crus.

## 10. Normalização de modalidade + rótulo de prazo (decisão 16/07)

**Problema:** o campo `tipo` (modalidade) vinha com 40+ variantes da mesma coisa
("Pregão - Eletrônico" vs "Pregão eletrônico" vs "Pregão (Lei 13.303)"…), sufixos de lei,
e valores malformados (ex.: `{'id':4,'tipo':'pregão'}` — bug de coletor).

**Decisão:** `db.normalizar_modalidade(tipo)` reduz tudo a **17 rótulos canônicos** (Pregão
Eletrônico, Pregão Presencial, Concorrência, Dispensa, Dispensa Eletrônica, Inexigibilidade
[inclui "Inaplicabilidade"], Contrato, Empenho, Credenciamento, Leilão/Concessão, Concurso,
Convite, Tomada de Preços, Chamamento Público, Diálogo Competitivo, Outros/Genérico, (vazio)).
Aplicado no **email** (coluna Tipo das abas) e na **categorização** (_aba usa o canônico).
Dados nas tabelas seguem crus (normalização é de exibição; `dados_originais` preserva tudo).

**Rótulo de prazo (coluna "Prazo p/ proposta") — decisão 16/07:** NÃO usar "em aberto" a menos
que confirmado. Regra em `_prazo_disp`:
- Inexigibilidade → `sem janela (inexigibilidade)` (contratação direta, não há proposta).
- Prazo futuro (>= hoje) → a data (janela aberta, confirmada).
- Prazo passado → `fechado (data)`.
- Sem prazo coletado → `prazo não consultado` (NÃO "em aberto" — não está confirmado).

## 11. Quais fontes têm data de PUBLICAÇÃO (auditoria 16/07)

Nem toda fonte expõe data de publicação; o campo `data` muda de significado por fonte.

**Coluna `data_publicacao` (específica):** só **Banrisul** (principal) preenche. Todas as outras = 0.

**Data de publicação DE FATO (em `data` ou `data_publicacao`):**

| Fonte | `data` é… | Publicação? |
|---|---|---|
| **PNCP** | `data_publicacao_pncp` | ✅ sim |
| **Banestes** | publicação | ✅ sim |
| **Banrisul** (principal) | publicação (em `data_publicacao`) | ✅ sim |
| Banrisul TCE-RS | sessão | ❌ |
| BCB | abertura (em `data_abertura`; `data`=vazio) | ❌ |
| BRB | sessão | ❌ |
| BDMG | sessão | ❌ |
| ComprasNet | vigência do contrato | ❌ |
| B3 | data do leilão/edital | ❌ |
| BNB / BNDES / Banco Amazônia | `data` preenchido, semântica a confirmar | ⚠️ |
| CAIXA SICVE | "Data de Publicação no DOU" (só via enriquecimento; hoje 0) | ⚠️ |

**Conclusão:** **3 fontes** têm data de publicação confiável (PNCP, Banestes, Banrisul). Para as
demais, o pipeline usa a **data de coleta** (`run_date`) como data efetiva (ver §9) —
"apareceu quando coletamos".


## 12. Coletores: falha explícita em vez de silenciosa (decisão 17/07)

**Contexto.** Auditoria dos coletores de BCB, BRB e ComprasNet (a run de 17/07 trouxe
poucas linhas de BCB/BRB e ComprasNet vazio). Diagnóstico:

| Coletor | Estado real | Causa |
|---|---|---|
| BCB | ✅ saudável | 28 linhas = **todos** os editais de 2026; a queda vs 07-13 (3.049) foi `ANOS_ALVO` estreitado para o ano corrente (recorte recente — o histórico já está no bronze). |
| ComprasNet | ✅ saudável | ~0 é **real**: os bancos-alvo são empresas públicas, publicam via PNCP e quase não constam no SISG. API respondendo 200/JSON. |
| **BRB** | ❌ **quebrado (intermitente)** | O WAF da BRB devolve `<html>…Request Rejected…` com **HTTP 200**. Como o status é 200, o `raise_for_status()` passava; o `resp.json()` estourava `JSONDecodeError`, **engolido** por um `except: break` → coleta parcial reportada como "sucesso". |

**O bug de fundo:** os coletores gravavam um CSV vazio/parcial e a task do Airflow ficava
**verde** mesmo quando a fonte estava bloqueada. Numa DAG semanal, isso passa despercebido.

**Decisão.** Falhar a task quando há erro real, sem mascarar "vazio legítimo":

- Novo módulo [`net.py`](./net.py): `json_or_raise` / `get_json` levantam **`ColetorBloqueado`**
  quando a resposta é HTTP 200 **não-JSON** (WAF), 4xx/5xx, ou falha de rede persistente.
  Lista JSON vazia (`{"data":[]}`) **não** levanta — é vazio legítimo.
- `bcb.py`, `brb.py`, `comprasnet.py` passam a usar esse helper. Em `brb.py` foi **removido**
  o `except: break` que engolia o WAF. Em `comprasnet.py`, `_gj` distingue 404 (sem dados p/
  aquele órgão → OK) de WAF/5xx (→ levanta).
- **Não precisou** mexer no orquestrador: `montar_bronze` já tem `trigger_rule="all_done"`
  (um coletor quebrado falha visível, mas **não** trava o pipeline — as demais fontes seguem)
  e `DEFAULT_ARGS` tem `email_on_failure=True` (a falha dispara alerta por email).

**Verificação (17/07):** teste determinístico — corpo real do WAF (HTTP 200 + HTML) → levanta;
JSON válido e JSON vazio → passam; HTTP 500 → levanta. Ao vivo: BCB 28 (2026) OK; BRB coletou
60 quando o WAF estava aberto (é intermitente) e levanta quando bloqueado.

**BRB — conserto (17/07):** o WAF da BRB é **intermitente por taxa** (bloqueia sob rajada e
libera segundos depois — testado: 5/5 bloqueado num momento, 6/6 OK minutos depois, com os
mesmos headers). Logo não é caso de trocar header, e sim de **resiliência**: `get_json` passou a
**re-tentar com backoff** (4 tentativas: 3s, 6s, 9s) tanto em rede quanto em WAF/HTTP-200-não-JSON,
levantando `ColetorBloqueado` só ao esgotar. `brb.py` ganhou uma `Session` com **warm-up** (GET na
página antes da API, pega cookies do F5). BCB passou a usar o mesmo `get_json`; `comprasnet._gj`
idem (re-tenta WAF/5xx, 404 = sem dados, levanta ao esgotar). Verificado end-to-end: BRB 60 linhas,
BCB 28. (Opção não implementada: guard de "queda >80% vs última run" — descartado por ora porque a
queda legítima do recorte de ano geraria falso-alarme.)


## 13. Filtro de objeto: substring → word-boundary (`\b`) (conserto 17/07)

**Bug.** `gold.py` tinha o filtro do **objeto** em `PALAVRAS_EXCLUIR` usando **substring**
(`re.escape(p)`), enquanto o do **anexo** já usava `\b`. Palavras curtas casavam dentro de outras:
`"Sol"` casava em "so**lu**ção"/"con**sol**idação" (2.577 objetos no silver tocados), matando
**silenciosamente** licitações relevantes (ex.: "prestação de serviços de **consultoria** para
diagnóstico…"). Medição: das que dizem "solução" e parecem relevantes, **0 de 145** chegavam ao gold.

**Conserto.**
- `_OBJ` agora usa `\b` (igual ao `_ANX`) — [gold.py](./gold.py).
- Na `PALAVRAS_EXCLUIR`, `"Sol"` (que só funcionava como substring e sob `\b` ficaria inútil) foi
  **substituída** pelos termos completos da intenção real (energia solar): `Solar`, `Solares`,
  `Fotovoltaico/a/os/as` — [config.py](./config.py).

**Impacto medido (silver, 39.517 linhas):** passavam **4.881** → agora **5.491** (**+610
recuperadas**), sem afrouxar demais. Exemplos recuperados: consultoria em gestão de custos,
serviços de consultoria p/ adequação, asseguração limitada.

**Follow-up:** as 610 estão só no **silver** (nunca entraram no gold). Para recuperá-las de fato
(gold → score → email) é preciso um **reprocessamento** pontual silver→gold→gold_filtros — não
acontece sozinho no delta, porque elas já estão deduplicadas no silver.


## 14. Links dos itens: PNCP, CAIXA e Banestes (conserto 17/07)

Auditoria dos links exibidos no email/Excel. Três problemas distintos:

**PNCP — links do backfill não abriam.** A API devolve `item_url` **relativo**
(`/compras|/contratos|/atas/…`) e `_link_app` traduz certo para
`/app/{editais|contratos|atas}/…`. Coletas novas (14/07+) estão 100% corretas. Mas o **backfill
histórico** (run_date 2020-01-01) trouxe **17.213** links pré-montados no formato **absoluto
antigo** `https://pncp.gov.br/compras/…` (sem `/app/`), que o SPA do portal abre **vazio**.
- Conserto de código: `_link_app` ([pncp.py](./pncp.py)) agora normaliza **relativo E absoluto**
  (tira o host, mapeia a rota, respeita quem já está em `/app/`).
- Conserto de dados: `UPDATE` traduzindo os 17.213 em bronze/silver (+1.117 gold, +1.117
  gold_filtros). Verificado: 0 links PNCP sem `/app/`.

**CAIXA — todas com o mesmo link vazio.** `caixa.py` gravava `link = DETALHE_URL`
(`detalhamento.jsf`) **fixo** p/ toda linha. O SICVE é JSF: `detalhamento.jsf` só renderiza via
POST+ViewState (abre vazio por GET) e não tem id na URL. Conserto: link passa a apontar p/
`resultado.jsf` (página de resultados **pesquisável**) e o **nº do certame** vai no objeto (abaixo).
`UPDATE` também nos 45/20 registros existentes.
> **Superado em 04/09/2026:** a premissa "não tem id na URL" estava errada — existe
> `visualizar.jsf?lote=<id>`, e o id do lote já vem na listagem. Ver **§21**.

**Banestes — cai na página da categoria.** Não há URL por processo (o link já leva à página certa
da categoria: `aviso_inexigibilidade.html`, `editais_pregao.html`, …), mas sem apontar o item.

**Solução p/ fontes sem link por item (Banestes + CAIXA):** prefixar o **nº do processo** no
início do objeto — `"[182/2026] <objeto>"` — para localizar o item na página (Ctrl+F). Decisão do
user: gravar **na coluna `objeto` do banco** (não só no email). Feito no **coletor**
([banestes.py](./banestes.py), [caixa.py](./caixa.py)) p/ coletas futuras, e por `UPDATE`
idempotente (`position('[nº] ' in objeto) <> 1`) no histórico (2.808 bronze / 608 gold / 21
gold_filtros; linhas sem `numero` ficam sem prefixo). Só o **bronze** tem coluna `numero`;
silver/gold/gold_filtros foram atualizados por join em `id_unificado`.

## 15. Email: "dia"/"semana" → "semana"/"mês" (decisão 29/07)

**Pedido do user:** a aba "Todos do dia" virar "Todos da semana"; a aba "Relevantes" e as
2 abas de novas ("editais"/"contr-inexig") virarem escopo **mês** (mantendo a mesma separação
editais vs contr-inexig — isso não mudou, só a janela de tempo).

**Mudanças ([config.py](./config.py), [task.py](./task.py)):**
- Nova constante `DELTA_DIAS_MES = 30` (rolling 30 dias, mesmo estilo do `DELTA_DIAS = 7` —
  **não** é mês-calendário/dia 1, é janela móvel, para ficar consistente com a semana).
- **Aba 2** ("Todos do dia" → **"Todos da semana"**): a consulta usava
  `b.run_date >= CURRENT_DATE - 1` (hoje+ontem, hardcoded); passou a usar
  `CURRENT_DATE - (DELTA_DIAS - 1)` (7 dias, mesma janela do corpo/aba semana).
- **Aba 1** ("Relevantes" → **"Relevantes do mês"**): antes era catálogo da **base inteira**
  (sem filtro de data, decisão 16/07 §9 — "independe do delta"). Agora filtra por DATA
  EFETIVA nos últimos `DELTA_DIAS_MES` dias, mesma lógica de data efetiva das abas de novas
  (§9). **Isso é uma mudança de comportamento**: itens relevantes antigos (fora da janela de
  30 dias) somem do catálogo — se precisar do catálogo histórico completo de novo, é outra aba.
- **Abas 3/4** ("Novas na semana" → **"Novas no mês"**): janela de `DELTA_DIAS` para
  `DELTA_DIAS_MES` na query e no filtro de data efetiva. Categorização editais vs
  contr-inexig (`_aba`, §9) **inalterada**.
- **Corpo do email:** a lista de Objetos do corpo (`itens`) vem do mesmo `df` que alimenta as
  abas 3/4 — como a query é uma só, o corpo passou a refletir o mês junto (não dava para
  manter corpo=semana e abas=mês sem duplicar a query/lógica de data efetiva). Se precisar
  separar de novo, seria uma 2ª query igual à de antes com `DELTA_DIAS`.
- Refatorado: `_efetiva`/`_na_janela` viraram funções auxiliares reusadas pelas 3 janelas
  (antes só existia inline para a aba de novas).

## 16. Objeto com o(s) termo(s) de keyword casados (decisão 29/07)

**Pedido do user:** "adicionar no objeto o termo para pesquisar nos sites". Não existe busca
por termo em nenhum coletor (PNCP busca por CNPJ do banco, `q=<cnpj>`; os demais raspam tudo
do portal e filtram depois). O único conceito de "termo" que já existe no pipeline é
`tag_kw_<tema>_termos` ([score.py](./score.py) `_keyword_tags`): a(s) keyword(s) de
`config.TEMAS` que casaram no objeto (sinal `s_kw`).

**Interpretação aplicada:** prefixar o `objeto` exibido no email/Excel com esse(s) termo(s),
ex.: `"[consultoria estrategica] <objeto original>"` — mesmo padrão já usado para nº de
processo (Banestes/CAIXA, §14), mas **só no e-mail** (não grava no banco, ao contrário do §14 —
aqui é derivado do score, que já mora em `gold_filtros`, sem motivo pra duplicar na tabela).

**Limitação:** só as abas que vêm de `gold_filtros` (Relevantes do mês, Novas no mês
editais/contr-inexig) têm o termo — a aba "Todos da semana" vem direto de `gold` (antes do
score rodar), não tem `tag_kw_*_termos`. Quando não há termo casado (ex.: item entrou só via
zero-shot ou LLM, sem keyword), o objeto fica sem prefixo.

## 17. PNCP: `valor` ausente em editais/atas (investigado 29/07, não corrigido)

**Pedido do user:** "revisar valor de pncp, alguns não coletados". Auditoria direta no
Postgres (`licitacoes_novo.bronze`, fonte PNCP):

| `tipo_documento` | total | sem `valor` |
|---|---|---|
| contrato | 1.081 | 0 (0%) |
| edital | 2.882 | 2.882 (**100%**) |
| ata | 855 | 855 (**100%**) |

**Diagnóstico:** não é bug de nome de campo — `dados_originais` (linha crua devolvida pela
API de busca, `/api/search/`) **não tem a chave de valor** para editais/atas, só para
contratos (onde a API devolve `"valor"`, ex.: `"0.0"`). `pncp.py` lê
`it.get("valor_global", "")` corretamente; o problema é que a **API de listagem/busca do
PNCP simplesmente não expõe valor para edital/ata** — provavelmente porque, para esses tipos,
o valor só existe no **detalhe** de cada item (mesma API instável usada em
`buscar_prazo_proposta`, §6 — `/api/consulta/v1/orgaos/{cnpj}/compras/{ano}/{seq}`).

**Não corrigido nesta rodada porque:**
- Não consegui confirmar ao vivo se o endpoint de detalhe realmente devolve valor para
  editais (a chamada de teste travou/resetou — mesma instabilidade já documentada no §6;
  não vou supor o nome do campo sem ver uma resposta real).
- Mesmo que devolva, viraria **mais uma chamada HTTP por item** (ou reaproveitar a mesma
  chamada de `buscar_prazo_proposta`, que já é paced/com retry) — custo e risco parecidos
  aos do enriquecimento de prazo (§6), e é uma mudança de escopo maior que cabe numa decisão
  separada, não escondida dentro do ajuste de abas do email.

**Próximo passo (se aprovado):** estender `buscar_prazo_proposta` (ou uma função nova) para
também extrair o campo de valor do JSON de detalhe **na mesma chamada** já feita para o
prazo (zero custo de rede extra), e persistir em `prazos_proposta` ou direto em
`gold_filtros` via UPDATE — mas só depois de inspecionar uma resposta real do endpoint de
detalhe para achar o nome certo do campo.

## 18. DAG diário 10h BRT + email só com novas do dia (decisão 31/07)

**Pedido do user:** rodar diariamente de manhã, num horário fora da janela da pipeline de
notícias, e **não enviar email se não houver licitação nova**.

**Schedule:** `START_DATE` passou a ser `pendulum.datetime(..., tz="America/Sao_Paulo")`
(mesmo padrão de `DATABASE_Noticias_CLASSIFICACAO` etc. — `airflow.cfg` tem
`default_timezone = utc`, então sem tz-aware o cron seria interpretado em UTC, não BRT).
`SCHEDULE = "0 10 * * *"` → 10h BRT, fora das janelas já usadas por outras DAGs de notícias:
`DATABASE_Noticias_OperacoesMnA` 03:30, `DATABASE_Noticias_COLETA` 05:00/20:00,
`DATABASE_Noticias_CLASSIFICACAO` 06:30, `MARKETING_EnvioNoticias` seg 08:00 (todas BRT).

**Gate de envio (reverte parte do §9 "sempre enviado"):** `enviar_email` agora aborta ANTES
de montar Excel/HTML se `count(*) FROM gold_filtros WHERE run_date = CURRENT_DATE` for 0.
Usa `run_date = HOJE` (não a janela rolante de
`DELTA_DIAS_MES` do corpo/Excel) porque o motivo de existir do gate é justamente evitar
repetir o mesmo digest de ~30 dias todo dia sem nada de fato novo — problema que só apareceu
porque a DAG passou a rodar diário (§9 já alertava: "DAG diário repete as mesmas por 7 dias").
Quando há novas, o corpo/Excel continuam mostrando a janela rolante normalmente (nenhuma
mudança lá).

**Correção do gate (mesmo dia 31/07):** a primeira versão do gate exigia também
`score_combinado > EMAIL_SCORE_MIN`. Isso estava mais restritivo que a intenção: a aba
"Todos da semana (pós-palavras)" existe precisamente para mostrar o que passou no filtro de
palavras ANTES do score, e o gate a tornava inalcançável justamente nos dias em que ela era
a única novidade. Medição na base (`gold_filtros` por `run_date`) no dia da correção:

| run_date | no gold | score máx. | acima de 0,35 | gate com score |
|---|---|---|---|---|
| 31/07 | 48 | 0,316 | 0 | BARRADO |
| 29/07 | 36 | 0,929 | 2 | enviaria |
| 22/07 | 1 | 0,130 | 0 | BARRADO |
| 21/07 | 5 | 0,167 | 0 | BARRADO |
| 20/07 | 4 | 0,270 | 0 | BARRADO |
| 15/07 | 85 | 1,000 | 9 | enviaria |
| 14/07 | 40 | 0,421 | 3 | enviaria |

4 dos 7 dias com coleta barrados, todos com conteúdo no gold. O critério passou a ser
"entrou algo novo no gold hoje" (qualquer score). A relevância (`EMAIL_SCORE_MIN`) continua
filtrando o CONTEÚDO — corpo do email e abas Relevantes/editais/contr-inexig —, só não
decide mais o ENVIO. Nos dias sem nada acima do corte o email sai com "Relevantes do mês"
vazia, `n_novos = 0` ("nenhuma" no corpo, abas 3/4 não criadas) e a aba "Todos da semana"
preenchida; o código já tratava todos esses vazios (`if not base.empty`, `tem_delta`).

## 19. Contrato TCE-RS aparecendo como "novo" e com modalidade errada (fix 26/08)

**Reclamação do cliente:** no e-mail, item 6 e 7 eram o mesmo objeto — um edital (aba1)
e o contrato já assinado decorrente dele (aba2), mesmo `objeto` (o TCE-RS replica o texto
nas duas tabelas). O contrato aparecia como se fosse oportunidade nova, coluna "Prazo p/
proposta" dizia "prazo não consultado" em vez de indicar que já estava fechado. Além disso a
modalidade mostrada era "Pregão Eletrônico", quando o dado oficial do TCE-RS (`CD_TIPO_MODALIDADE
= PRI` nas duas tabelas, `licitacao.csv` e `contrato.csv`) é "Pregão (Lei 13.303)" — regime
de estatal (Banrisul), não necessariamente eletrônico.

**Causas encontradas (investigação com dado bruto do TCE-RS + logs do Airflow via MinIO):**
1. `banrisul_tce.py`: a linha de CONTRATO nunca lia `CD_TIPO_MODALIDADE` do `contrato.csv`
   (o campo existe lá, igual ao da licitação de origem) — `tipo` ficava fixo em `"Contrato"`,
   perdendo a modalidade real.
2. `db.normalizar_modalidade`: qualquer "preg" sem a palavra "presencial" virava "Pregão
   Eletrônico" por padrão — "Pregão (Lei 13.303)" não diz eletrônico nem presencial, e a
   função assumia eletrônico, mascarando a distinção.
3. Aba "Relevantes do mês" (`task.py`, `_prazo_disp`): só sabe dizer "fechado" quando existe
   linha em `prazos_proposta` (só preenchida pro PNCP) ou quando a modalidade é
   inexigibilidade. Não olhava `tipo_documento` — um CONTRATO (fechado por definição, tem
   `data_fim_vigencia`) caía no fallback genérico "prazo não consultado", indistinguível de
   um edital ainda não checado.
4. Lista numerada do corpo do e-mail ("Novas no mês com score > 0,35") não respeitava o
   split aba1 (editais) / aba2 (contrato/ata/inexigibilidade) que já existe pro Excel —
   listava os dois juntos, duplicando visualmente o objeto.

**Fix:** (1) contrato também lê `CD_TIPO_MODALIDADE` via o mesmo dicionário `MODALIDADE`;
(2) `normalizar_modalidade` preserva "Pregão (Lei 13.303)" quando o texto não diz eletrônico
nem presencial explicitamente; (3) `_prazo_disp` rotula `tipo_documento in (contrato, ata)`
como `"fechado (contrato assinado)"`, sem precisar de `prazos_proposta`; (4) a lista do corpo
do e-mail passou a usar só `aba1` (editais), igual ao Excel.

**Não corrigido (sem evidência pra confirmar):** o cliente também mencionou "no nosso
controle está leilão" — não achei "Leilão" em nenhum lugar dos dados coletados (bruto ou
normalizado) pra esse processo; TCE-RS diz Pregão(Lei 13.303) nas duas tabelas. Pode ser
sobre outro item, ou informação de outra fonte (controle manual da equipe, portal do
próprio Banrisul). Não mexido.

## 20. Auditoria: outras fontes com processo já fechado na aba Relevantes (26-27/08)

Depois do fix do §19, pergunta óbvia: será que só PNCP/TCE-RS/ComprasNet têm esse problema?
Auditei `situacao` de todas as 11 fontes contra o bronze real.

**Achado ativo (corrigido):** BRB devolve `situacao` como objeto `{'id':N,'fase':'...'}`
direto da API (`brb.py`, sem tratamento) — fases incluem "Licitação homologada/fracassada/
deserta/revogada/anulada". Coleta ativa e recente (última em 13/08), `silver.py` só faz
passthrough do campo, nada usava isso pra filtrar. Ainda não tinha vazado pro email por
sorte de score (nenhum item BRB com fase fechada bateu score>0,35 até agora), mas era risco
real e crescente, não teórico.

**Achados de dado morto (não corrigidos, não é bug ativo):** fontes "BNDES - Banco Nacional..."
(313 "Homologada"), "Banrisul - Licitações (secao_id=885/888/889)", "Banrisul - TCE-RS
(Contratos)" — nenhuma existe em coletor algum do código atual (nomes de fonte de versões
antigas, removidas/renomeadas). Dados de 2020-2025 com `run_date` placeholder, fora da
janela de 30 dias — não aparecem no email hoje independente de qualquer fix. Não vale a
pena tocar código por causa disso (poderia limpar do banco se algum dia incomodar).

**Sem sinal nenhum coletado (fora de escopo — não é "campo ignorado", é campo inexistente):**
BCB, BDMG, Banestes, Banco da Amazônia, B3, Banrisul (atual), CAIXA SICVE nunca capturam
status de fechamento nos coletores atuais (grep confirma: nenhum desses arquivos seta
`"situacao"` com informação de status real). Cobrir isso exigiria engenharia nova por fonte
(descobrir se a fonte sequer expõe esse dado, e como raspar/parsear) — não é o mesmo tipo de
fix (usar dado que já coletamos e ignoramos).

**Fix:**
1. `brb.py`: extrai `fase` do dict antes de gravar `situacao` (bônus: Excel para de mostrar
   o repr cru do dict Python).
2. `task.py`: `_situacao_indica_fechado()` — checa palavras-chave de fechamento
   (`homologad/fracassad/desert/revogad/anulad/cancelad`; **"suspens" fica de fora de
   propósito** — suspenso pode reabrir, não é fechamento definitivo). Usado junto com
   `tipo_documento` tanto no filtro da aba "Relevantes do mês" quanto no split aba1/aba2 de
   "Novas no mês" (item com situação fechada agora cai em aba2, igual contrato/inexigibilidade).

Verificado contra o banco: as 3 linhas BNDES "Homologada" (dado morto, já fora da janela)
também caem nesse filtro agora, de graça — não fazia diferença hoje, mas fecha a brecha se
algum dia esse coletor voltasse a existir com o mesmo nome de fonte.

---

## 21. Verticais: classificação por LLM virou task da DAG (04/09/2026)

**Problema.** A classificação do objeto em vertical de negócio nunca foi automatizada:
rodou UMA vez, em 31/07/2026 (única data em `licitacoes_novo.verticais.classificado_em`),
e desde então o gold cresceu sem classificar. Medido no banco em 04/09/2026:

| | |
|---|---|
| linhas no `gold` | 4.800 |
| linhas em `verticais` | 4.150 |
| sem vertical | 650 |
| dias desde a última rodada | 35 |
| média de linhas novas/dia | ~19 |

As 650 sem vertical são justamente as MAIS NOVAS, e o dashboard
(https://datahub.bateleur.com.br/licitacoes-bancos/) trabalha em janelas de 7 e 30 dias:
nenhuma licitação do recorte visível tinha vertical, os 12 chips de filtro mostravam zero e
os gráficos/tabela da seção Verticais ficavam vazios. O aviso de cobertura do painel
(`db.cobertura_vertical()`) só tornava o furo visível — a correção é a task.

**Posição na esteira:** entre `montar_gold_filtros` e `enviar_email`. As linhas novas do dia
só existem depois do gold_filtros, e assim o email E o painel já saem com a vertical
preenchida.

**Fila padrão (CPU), não `gpu`:** aqui é só HTTP para a Groq. O worker de GPU fica livre
para o zero-shot do `score.py`.

**Chaves por Airflow Connection**, `get_secrets(GROQ_API_KEYS)` (`groq_api_1..6`) — o
`GROQ_API_KEYS` do `.env` pertence só ao CLI manual; na DAG variável de ambiente para
credencial é proibido (convenção do repo). Mesmo padrão do `score.py`.

**Teto de 300 itens/execução (`VERTICAIS_LIMITE`).** A cota que trava é o **TPD da
ORGANIZAÇÃO** Groq: 200.000 tokens/dia no tier on_demand, compartilhado por TODAS as chaves
da conta — e o `score.py` da MESMA run já consome dela, para as mesmas linhas.

| cenário | itens | chamadas (lote 10) | tokens | % do TPD |
|---|---|---|---|---|
| dia normal | ~19 | 2 | ~4.400 | 2% |
| teto da task | 300 | 30 | ~66.000 | 33% |
| backlog de 04/09 | 650 | 65 | ~143.000 | 71% |

Ou seja: o regime diário é trivial; o backlog é que é caro. **Decisão: backlog NÃO se drena
dentro da DAG** — roda-se o CLI manual, fora do horário da DAG (10h BRT):

```bash
cd /home/augusto/Documentos/bateleur-artifacts/licitacoes-bancos
python3 classificar_verticais.py       # ~650 pendentes, lote 10 por padrão
```

**Não derruba a DAG.** `trigger_rule="all_done"` (igual ao `enviar_email`): a task roda mesmo
se o gold_filtros falhar (o worker de GPU cair não impede classificar o que o `montar_gold`
já inseriu), e o email sai mesmo se a classificação falhar. Cota diária esgotada
(`LimiteDiarioTokens`, diagnosticada pelo CORPO do 429 — "tokens per day" — porque os headers
por chave continuam saudáveis com o TPD zerado) encerra a rodada com **aviso** no log, não em
falha: retentar hoje é inútil e a task é resumível.

**Módulo duplicado de propósito.** `verticais.py` é cópia de `app/verticais.py` do
bateleur-artifacts (`licitacoes-bancos`), e a taxonomia `VERTICAIS` é cópia do `app/config.py`
de lá. Import cross-repo não funciona no worker (a imagem só carrega `dags/`), e pacote
compartilhado exigiria mexer no build das duas imagens — mesmo precedente de
`normalizar_modalidade`/`to_date`. **Mudar prompt ou taxonomia obriga mudar NOS DOIS:** o CLI
e a task gravam na MESMA tabela, então divergência gera rótulos inconsistentes na mesma coluna.

**Armadilhas herdadas do módulo (já pagas, não reverter):**

- `requests`, nunca `urllib`: o Cloudflare da Groq responde `HTTP 403 / error code: 1010` ao
  User-Agent do `urllib`.
- `max_completion_tokens` é obrigatório (300 por item, 160 por item em lote). Sem ele a Groq
  estima o pior caso da janela de saída do modelo ao decidir se a requisição cabe na cota, e
  recusa chamadas que caberiam.
- Dentro do minuto a cota é **TPM (8.000/chave)**, não RPM. O `KeyLimiter` conta TOKENS em
  janela deslizante de 60s por chave; um limiter que contasse requisições levaria a 429 em
  série.
- Em lote o modelo às vezes devolve MENOS itens do que os enviados. Sem o fallback item-a-item
  esses itens falhariam em silêncio a cada rodada e nunca convergiriam.
- Idempotência: só entra `id_unificado` ausente de `verticais`, commit por resposta, e falha
  **não é persistida** — senão congelaria um "Outros" errado para sempre.

## 22. O que cada fonte realmente entrega (auditoria 16/09/2026)

Levantado sobre o `bronze` inteiro (fontes com >50 linhas). Percentual de linhas
com o campo preenchido. Serve para não confundir "lacuna do pipeline" com
"a fonte não publica isso" — as duas coisas existem e têm conserto diferente.

| Fonte | linhas | link | valor | data | tipo |
|---|---:|---:|---:|---:|---:|
| PNCP | 22.598 | 100% | 56% | 100% | 100% |
| Banrisul | 5.433 | 0% | 0% | 0%→100%¹ | 100% |
| Banestes - Editais e Avisos | 4.565 | 100% | 70% | 98% | 100% |
| Banrisul - TCE-RS (LicitaCon) | 2.838 | 100% | 42% | 100% | 100% |
| BCB - Banco Central do Brasil | 2.788 | 0% | 0% | 0%² | 100% |
| ComprasNet | 1.670 | 0%→43%³ | 43% | 46% | 100% |
| BRB - Banco de Brasília | 712 | 0% | 0% | 0%² | 100% |
| B3 | 454 | 100% | 0% | 100% | 0% |
| BDMG | 349 | 100% | 0% | 1% | 91% |
| BNDES | 344 | 100% | 100% | 98% | 100% |
| CAIXA SICVE | 107 | 100% | 49% | 68% | 100% |
| Banco da Amazônia | 107 | 100% | 0% | 79% | 100% |

¹ **Corrigido.** O Banrisul sempre teve a data de publicação (`publishdate`,
5.433/5.433), mas `GRUPOS["data"]` procura a chave NATIVA `data_publicacao` e a
do Banrisul tem outro nome. O canônico `data_publicacao` era preenchido e `data`
— que é o campo que silver/gold/janela usam — ficava vazio. O bronze agora cai
para o canônico. Vale para qualquer fonte futura na mesma situação.

² **Não tem conserto no coletor.** BCB só publica `DataAbertura` e BRB só
`data_sessao`. Ambas são POSTERIORES à publicação: usá-las como `data` faria um
edital antigo parecer recente e o seguraria dentro da janela do mês. Ficam sem
`data` e caem no `run_date` (§9), que é o comportamento correto.

³ **Corrigido para os editais.** As licitações do ComprasNet são as mesmas do
PNCP e vêm com `numeroControlePNCP`, que o coletor usava só para montar o `id`.
O link do portal é reconstruído a partir dele (verificado ao vivo: 200). Os 950
contratos não têm controle PNCP e continuam sem link — limitação da fonte.

**`valor` a 0% em Banrisul, BCB, BRB, B3, BDMG e Banco da Amazônia não é bug do
parser:** as APIs de listagem dessas fontes não trazem valor nenhum. Confirmado
inspecionando as chaves nativas preservadas em `bronze.dados_originais`. Obter o
valor exigiria abrir o edital de cada item — não feito. `valor` vazio nessas
fontes significa "a fonte não informa", não "ninguém coletou".

**`tipo` a 0% na B3 e no BNB:** a B3 não publica modalidade (o campo `Status` não
é modalidade) e as 517 linhas do BNB vieram da carga histórica sem esse campo.
Todas caem em "(vazio)" no normalizador, o que está certo.

## 23. Prazo da proposta: o gate passou a ser o número de controle (fix 16/09/2026)

Achado testando a branch unificada da auditoria, depois de reativar o ComprasNet.

`filtrar_acionaveis` e `backfill_prazos` só consultavam o prazo quando
`fonte == 'PNCP'`. Mas as licitações do ComprasNet **são editais do próprio
PNCP**, coletados por outra porta: vêm com `numeroControlePNCP` e o link aponta
para `pncp.gov.br/app/editais` (§22, nota ³). Consequência medida numa fatia
real de 49 itens do ComprasNet no gold: **todos** caíam no ramo "não-PNCP" —
nenhum ganhava prazo (exibidos para sempre como "prazo não consultado") e
nenhum era excluído por janela já fechada.

O critério agora é **ter número de controle do PNCP**, não a coluna `fonte`
valer "PNCP". O número carrega, ele mesmo, os três campos que a API de detalhe
pede: `<CNPJ 14>-<tipo>-<sequencial>/<ano>`. Isso importa porque o ComprasNet
não traz CNPJ do órgão em coluna própria — medido: 0 de 935 linhas com
`orgao_cnpj`, 737 com controle. As colunas nativas (`orgao_cnpj`/`ano`) viram
reserva, para o coletor do PNCP, que as preenche separadamente.

Onde mudou:
- `pncp.identificadores_de_controle` / `identificadores_para_prazo` (novas);
- `gold.filtrar_acionaveis`: o gate, e o contador `nao_pncp` virou
  `sem_controle_pncp` (dispensa/contrato de fonte própria continuam mantidos,
  assumidos abertos, §9);
- `gold.backfill_prazos`: a consulta passou a dar JOIN no bronze e a exigir
  `numero_controle_pncp` não-vazio, no lugar de `fonte = 'PNCP'` — o número mora
  no bronze porque silver/gold descartam a coluna. O retorno ganhou
  `sem_identificador`, contado à parte para não se disfarçar de falha de API.

Efeito medido contra a API real do PNCP (Postgres descartável, 49 itens do
ComprasNet no gold): 38 prazos gravados, 3 com janela aberta, **8 com janela
fechada passaram a ser excluídos** do e-mail, 27 sem janela (inexigibilidade e
afins, mantidos), 5 sem controle e 6 falhas de API re-tentáveis. No
`backfill_prazos`, 12 de 12 gravados na primeira passada, todos ComprasNet.

**O que isto muda no e-mail:** edital do ComprasNet com prazo vencido some da
aba "Relevantes do mês". Antes aparecia, sem prazo, como se fosse oportunidade
viva. É a regra do §6/§9 aplicada a linhas que sempre foram editais do PNCP.

## 24. Número de controle é dado, não identidade (fix 17/09/2026)

Consequência não prevista do §23, achada ao auditar a branch antes do merge.

Para consultar o prazo, o coletor do ComprasNet passou a gravar
`numero_controle_pncp` (antes o número só aparecia dentro do `id` nativo). Mas o
`bronze.base_id` **prefere** esse campo para montar o `id_unificado`. Adotá-lo
numa fonte que já tem 1.670 linhas no bronze reescreve a identidade delas: a
mesma licitação volta com id novo, o dedup por id não a reconhece, e o dedup por
`row_hash` também não (a linha agora tem link e controle preenchidos, hash
diferente).

Medido contra o bronze real de produção, rodando a coleta de hoje: **716 das 935
linhas entrariam como novas**, mesmo `id_nativo` e id diferente —
`comprasnet::2026-90001` viraria `comprasnet::2026-33657248000189-1-000001/2026`.
Iriam para silver/gold e apareceriam no e-mail e no dashboard como se fossem
licitações inéditas.

`FONTES_ID_SEM_CONTROLE` mantém o ComprasNet com a base de id que sempre teve
(`numero`). O controle continua gravado na coluna — é o que o §23 consulta —, só
não decide identidade. Depois do fix, a mesma coleta entra com **20 linhas
novas**, que são a colisão de `numero` dentro do lote que a fonte sempre teve
(produção já tem 149 ids ComprasNet com sufixo `#`), não regressão desta leva.

O `base_id` saiu de dentro do `inserir_bronze` e virou função de módulo para
poder ser testado sem banco (`test_prazo_controle.py`): um engano nele reingere
uma fonte inteira, e isso não pode depender de alguém reparar no log.

## 25. Correção de parser não pode reingerir a fonte (fix 17/09/2026)

Achado rodando a DAG **inteira** pelo scheduler, contra um clone descartável de
produção — nenhum teste anterior desta leva pegava, porque todos exercitavam
funções isoladas.

O §22/D6 fez o Banrisul preencher `data` a partir de `publishdate`. Isso muda o
conteúdo da linha, logo muda o `row_hash`: das 5.283 linhas coletadas, **zero**
casaram por hash contra o banco. O dedup caiu no `id_unificado`, e ali esbarrou
num segundo problema: as linhas antigas do mesmo item estão guardadas sob o
esquema de id ANTIGO, quando a base era `numero` (`banrisul::2026-0132/2026`),
enquanto a coleta de hoje monta a base com `id_nativo`
(`banrisul::2026-354076`). Sem casar por hash nem por id, **5.316 linhas
entraram como novas**, 5.255 delas com gêmea de mesmo `id_nativo` já no banco.
Uma run normal do Banrisul insere de 8 a 63.

O efeito era visível no produto: o e-mail gerado trouxe 394 linhas em "Todos da
Semana", 382 delas Banrisul.

Duas mudanças:

1. Colisão de `id_unificado` contra o banco, nas 3 fontes de `id_nativo`, virou
   **descarte** em vez de sufixo `#N`. Com `id_nativo` como base, colidir
   significa "mesmo item recoletado". O sufixo ficou só para colisão dentro do
   lote, onde evita violar o `bronze_pkey`.
2. O descarte passou a olhar também `(fonte, id_nativo)` — `db.existing_native_ids`.
   É a única identidade que sobrevive às duas coisas: troca do esquema do id e
   mudança de conteúdo. Sem ela, 3.895 das 5.316 escapavam mesmo com (1).

Medido com a mesma coleta, contra o mesmo clone: **5.316 → 11 linhas novas**, na
faixa da run normal. Coberto por `test_dedup_bronze.py`, que stuba o banco.

**Não há backfill.** As 5.466 linhas antigas do Banrisul continuam com `data`
vazia no bronze; o §22/D6 vale das coletas novas em diante. Corrigir o passado
exigiria UPDATE no bronze, que é landing imutável por design — decisão adiada.

## 26. Teto de tempo nas duas tasks que consultam o PNCP (fix 17/09/2026)

A DAG não tinha `execution_timeout` em task nenhuma, e as duas funções que
consultam o PNCP item a item não tinham limite de duração — só teto de
quantidade. A API de detalhe responde em ~0,3s quando está boa e custa ~15s por
item quando recusa (4 tentativas com espera crescente, ver `buscar_prazo_proposta`).

Medido rodando a DAG inteira em 17/09/2026: o `backfill_prazos` passou de 17
minutos e gravou 1 prazo, com a API em recusa. Com `MAX_BACKFILL_PRAZOS = 300`, o
pior caso passa de uma hora ocupando um worker.

Três limites, do mais específico ao mais genérico:

- `MAX_ACIONAVEL_SEGUNDOS = 420` no `filtrar_acionaveis`. Ele roda dentro do
  `montar_gold`, que é a montante do e-mail — não pode virar refém da API. Quem
  não deu tempo de consultar é MANTIDO (nunca se descarta por não ter olhado,
  §9) e cai no backfill.
- `MAX_BACKFILL_SEGUNDOS = 600` no `backfill_prazos`, com gravação a cada
  `LOTE_GRAVACAO_PRAZOS = 25`. A gravação em lote é o que torna o corte seguro:
  antes a função acumulava tudo para gravar no fim, então parar no meio jogaria
  fora o trabalho de API já feito. O retorno ganhou `parou_por_tempo`.
- `execution_timeout = 30min` em `DEFAULT_ARGS`, como rede de segurança para
  qualquer task (coletor com conexão pendurada, por exemplo). É folgado de
  propósito: a task mais lenta medida é o Banrisul, com 7,4 min.

Coberto por `test_orcamento_tempo.py`, com relógio, rede e banco falsos.

## 27. Email de 12/10: backlog pontuado como "novo", Banestes e alerta de falha (fix 13/10/2026)

**O sintoma.** O email de 12/10/2026 saiu com 39 "relevantes do mês", contra 7 no
de 10/10. A lista trazia editais do BDMG de 2018–2023 e contratos do Banestes de
2019–2025, com acento quebrado ("Presta√ß√£o"), "[nº não informado]" e o mesmo
objeto repetido.

**A causa — duas coisas somadas.**

1. *Pontuação atrasada.* A carga manual de 14/07/2026 (19.508 linhas no bronze,
   o histórico inteiro dos sites) pôs 2.494 linhas no gold sem passar pelo
   `gold_filtros`: só 39 ganharam nota. Ficaram ~2,5 meses sem nota, fora de
   qualquer aba. O `scorar_atrasados` (desde 03/10) pontua 200/dia desse backlog
   e grava `gold_filtros.run_date` = dia da NOTA. Restavam 594 em 13/10.
2. *Data efetiva errada.* Sem data de publicação, o email usava o
   `gold_filtros.run_date` como "1ª vez que vimos" (§9). Na coleta diária a nota
   sai no mesmo dia, então dava certo; no backlog, um contrato visto em 14/07 e
   pontuado em 11/10 virava "novo em outubro". Só vazou quem não tinha data: BDMG
   (só expõe sessão, §11) e 345 itens do Banestes cuja data não foi lida.

**Correção do email.** A data efetiva cai no `bronze.run_date` (coluna
`visto_em`), que é de fato a 1ª vez que o item apareceu — `task.data_efetiva`.
O gate do envio também passou a exigir `bronze.run_date = hoje`: backlog
pontuado hoje não é motivo para mandar email. Simulado com os dados de 13/10:
6 relevantes / 9 novas (antes: 55 / 66). O `scorar_atrasados` continua gravando
a data da nota em `gold_filtros.run_date` — o campo é isso; quem o lia como
"data de aparição" é que estava errado.

**Banestes — três defeitos no coletor.**

- *Encoding.* As páginas declaram UTF-8, mas contratos/pregão/inexigibilidade
  têm meia dúzia de bytes UTF-8 truncados no próprio HTML (`ESP\xc3?RITO`). Com
  bytes crus o BeautifulSoup desiste do UTF-8 e adivinha outro encoding para a
  página INTEIRA — milhares de caracteres quebrados por página. Agora decodifica
  como UTF-8 com `errors="replace"` antes do parser (`soup_da_pagina`): sobram
  7–17 "�" por página, que é o que o site de fato corrompeu.
- *Data.* Pegava a PRIMEIRA coisa com cara de data no texto. Em contrato, isso
  casava dentro do ID CidadES do TCE (`2026.500E1600004.09.0062` → `04/09/0062`,
  descartada como inválida). Agora: `span.data-publicacao` (publicação, quando o
  site expõe); para contrato, início da vigência / "Firmado em"; senão VAZIO —
  e vazio cai no `bronze.run_date`, que é a regra certa. Contrato "contados a
  partir da assinatura" e dispensa sem publicação ficam sem data mesmo: o site
  não tem.
- *Código e link.* O prefixo do objeto (§14) usava o `numero` normalizado
  ("251/2026"), que não aparece escrito na página ("nº 251-26"), e contrato sem
  "NNN/AAAA" ficava sem prefixo nenhum. Agora o prefixo é o identificador do
  TÍTULO como o site o mostra (`[4600000860]`, `[251-26]`), e o `link` vai direto
  no PDF do item quando existe (antes: só a página da categoria). O
  `Processo n. 2025-WR7B1` vai na coluna `processo`.

**Por que precisou de migração.** O id do Banestes é
`md5(categoria‖numero‖texto)`. Consertar o texto muda o id de 3.291 itens — e a
próxima coleta os reingeriria como novos (o erro do §25). A migração
(`scripts/COMERCIAL_Licitacoes/migracao_banestes_encoding_datas.py`) parseia o
MESMO HTML do jeito antigo e do novo, item a item, e reescreve cada linha antiga
com o que o coletor novo produz, inclusive o `id_nativo` novo (o
`existing_native_ids` do bronze passa a casar). `id_unificado` não muda. Itens que
já saíram do site têm o mojibake desfeito por transformação reversa
(`mac_roman`/`cp1252` → UTF-8) e a data de contrato relida do texto. Também
propaga `numero` do bronze para silver/gold/gold_filtros onde ficou vazio (carga de
julho — o "[nº não informado]"). Faz backup integral em `bkp_s27_<tabela>`.
**A migração tem que rodar ANTES do deploy do coletor novo.**

*Aplicada em 13/10/2026*, com a DAG pausada: 4.382 linhas casadas (3.291 com
`id_nativo` novo), 210 sem par consertadas (inclui links de PDF com
`publicacoes_legais/` dobrado, que davam 404), `numero` propagado em 13.025
silver / 1.955 gold / 1.938 gold_filtros. Objetos com mojibake: 2.803 → 0 no
bronze, 0 nas demais. Trava pós-migração: a próxima coleta traz só 2 itens de
fato ausentes do banco. Dois detalhes que só apareceram ao gravar:

- `row_hash` é UNIQUE no bronze. As linhas repetidas da carga de julho viram a
  MESMA linha nova; só a 1ª do grupo leva o hash novo, as outras (214) mantêm o
  antigo. Quem barra a reingestão é o `id_nativo`, não o hash.
- Parte do que parecia repetição não é: "041/2024" duas vezes são os contratos
  4600000860 e 4600000859 do mesmo processo.
- A 1ª versão do script não era idempotente (numa 2ª execução, as linhas já
  migradas caíam no ramo "sem par"). Agora `pares_do_site` indexa também pelo id
  novo: rodar de novo é no-op (verificado: `id_nativo muda em 0`, `0 a consertar`).

**BDMG — falha silenciosa.** Desde ~01/10 o Akamai do BDMG devolve 403 na
listagem; o coletor fazia `break` e gravava CSV vazio com a task VERDE (o bronze
só logava "No columns to parse from file"). Agora levanta `ColetorBloqueado`
(§12). O bloqueio em si não foi contornado.

**Alerta de falha.** `email_on_failure` por task (genérico, um email por task)
deu lugar a UMA task final, `alertar_falhas` (`all_done`, a jusante de todas):
lista as etapas `failed`/`upstream_failed` com o erro tirado do log remoto
(MinIO) e manda para `ALERTA_DESTINATARIOS`. Depois de alertar, ela também falha
— é a única folha da DAG, e se saísse verde a run inteira ficaria `success`.
Coletor verde com 0 linhas gera `[AVISO]` sem derrubar nada (início de ano pode
ser vazio de verdade).

**Fica em aberto.** As linhas repetidas da carga de julho (mesmo `id_nativo`
com sufixo `#2`, `#3`) continuam no banco — removê-las é decisão à parte. O
`scorar_atrasados` de 13/10 falhou por falta de VRAM (`CUDA out of memory`, 347
MiB livres de 31 GiB): a GPU estava ocupada por outro processo.

Coberto por `test_email_backlog_banestes.py`.
