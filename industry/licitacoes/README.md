# licitacoes

Pipeline medallion que coleta licitações públicas, filtra e **pontua a relevância** para
serviços de **Machine Learning Engineering/MLOps, AI Engineering, Data Science,
Forecasting e Data Engineering, MDM e governança de dados**.
Porte da DAG `COMERCIAL_Licitacoes` do bateleur-pipelines, onde o foco era consultoria
financeira (M&A, business consulting, serviços financeiros, risco de crédito).

> Histórico das decisões da origem: [`DECISOES.md`](./DECISOES.md). As seções sobre
> e-mail, MinIO, GPU e Airflow não se aplicam mais; as regras de dedup, datas, prazos e
> recência continuam valendo.

## Fluxo (assets em `assets.py`)

```
licitacoes_coleta_<fonte> (20) → bronze → silver → gold → gold_filtros → verticais → score_atrasados
                                                                   ├→ prazos              └→ detalhes
```

- **Coleta — fontes gerais (qualquer órgão):**
  - `pncp_abertas` — varre TODAS as contratações com proposta aberta no PNCP (~28 mil, federal,
    estadual e municipal) e fica com as que casam com os temas de ML/IA/dados ou de TI
    (`TEMAS` + `TERMOS_TI`, `termos.py`). Traz a data de encerramento, usada direto no filtro
    "acionável".
  - `pncp` — busca por termo (`BUSCA_TERMOS_PNCP`) + CNPJs dos bancos (herdado).
  - `querido_diario` — diários oficiais municipais (Open Knowledge Brasil): municípios fora do
    PNCP e sinais antes do edital. Só entra trecho com marcador de contratação + tema.
  - `bndes_inovacao` — chamadas de solução inovadora (CPSI) do BNDES, várias de dados/IA.
- **Coleta — fora do PNCP (out/2026):**
  - `sebrae` — Canal do Fornecedor: processos de todos os Sebrae (regulamento próprio, não
    publicam no PNCP), inclusive "Desafio da Inovação" e credenciamentos.
  - `petronect` — oportunidades abertas da Petrobras, Transpetro e subsidiárias (Lei 13.303).
- **Coleta — internacionais (aceitam empresa do Brasil):** filtro de tema em inglês,
  espanhol, francês e português (`TERMOS_EXTERIOR`, `termos.termos_exterior`); o país vai
  em `local_abertura`.
  - `banco_mundial` — avisos de projetos financiados pelo Banco Mundial (API pública),
    só com prazo futuro. Manifestação de interesse vai por e-mail ao contato do aviso.
  - `pnud` — todos os avisos abertos do PNUD. "IC - Individual contractor" é contratação de
    pessoa física, sem CNPJ.
  - `ungm` — portal das agências da ONU (OMS, UNICEF, OIT, FAO...), menos o PNUD. "Call for
    individual consultants" também é pessoa física. Para responder é preciso cadastro
    gratuito de fornecedor no UNGM.
  - `prodoc` — editais de consultor individual (pessoa física) da UNESCO, do IICA e de
    outros organismos em projetos de cooperação com ministérios (páginas "editais em
    seleção" no gov.br, `PRODOC_PAGINAS`). Lê cada PDF (objeto, projeto, prazo); fica o que
    tem prazo aberto, sem filtro de tema, porque são poucos e o score ordena.
  - **Marcação para filtrar** (`bronze.marcar`, colunas `ambito` e `pessoa_fisica` do
    bronze, expostas na view `licitacoes_painel`): `ambito` é `internacional` para as fontes
    de `FONTES_INTERNACIONAIS` e `nacional` para o resto; `pessoa_fisica` é `sim` quando o
    aviso aceita consultor individual sem CNPJ (PNUD "IC", UNGM "Call for individual
    consultants", Banco Mundial "Individual Consultant Selection" e todo o PRODOC). No home,
    filtros "onde" e "quem pode concorrer".
  - Ficaram de fora: BID e AfDB (bloqueiam acesso automático, 403) e Finep (só
    financiamento; as compras dela já saem no PNCP).
- **Coleta — fontes bancárias (herdadas):** ComprasNet e portais de bancos públicos.
  Coletor que falha registra o erro e não trava o resto; o asset check
  `licitacoes_coletas_ok` fica vermelho.
- **Gold:** remove objetos com `PALAVRAS_EXCLUIR` e mantém só o "acionável" (janela de
  proposta aberta). O filtro pelo texto do PDF do edital está desligado
  (`LICITACOES_FILTRAR_ANEXO`), porque termos genéricos aparecem em todo edital.
- **Detalhes** (`detalhes.py` → `licitacoes.detalhes`): para os itens pontuados, do mais
  relevante ao menos (até 80 por execução), completa o que as listagens não trazem:
  modalidade normalizada, critério de julgamento, benefício ME/EPP, registro de preços,
  modo de disputa, amparo legal, orçamento sigiloso, situação, prazos, link de onde a
  disputa acontece e a lista de anexos. No PNCP vem do detalhe, dos itens e dos arquivos
  da contratação; nas outras fontes, do que o coletor trouxe. Sem valor estimado, baixa o
  PDF do edital e procura "valor estimado/global/máximo ... R$" (`valor_origem = edital`).
- **Nota técnica e quem concorre** (`nota_tecnica.py` → `licitacoes.nota_tecnica`, sem
  LLM): lê o edital de cada licitação aberta e pontuada (PDF, zip, ou os documentos por
  token do portal da Cemig) e grava a divisão da nota técnica entre histórico e proposta e
  o requisito de participação: `pessoa_fisica` (aceita CPF), `startup` (LC 182: até 10
  anos de CNPJ e receita até R$ 16 mi), `restrito` (veda MEI, capital/patrimônio ou tempo
  mínimo), `me_epp` (exclusiva ME/EPP, MEI entra), `comum` (CNPJ sem restrição achada) ou
  nulo (sem edital legível). Pessoa física só conta com frase que abre a disputa a ela
  ("poderão participar pessoas físicas"): a palavra solta aparece em todo edital.
- **Destino:** schema `licitacoes` em `LICITACOES_PG_URL` (padrão: Supabase).

## Score de relevância

`score_combinado = 0.15·s_kw + 0.15·s_zs + 0.7·s_llm` (renormalizado se faltar sinal);
relevante quando `≥ 0.5`; corte de exibição sugerido `0.35`.

| Sinal | Origem |
|---|---|
| `s_kw` | keyword dos 5 `TEMAS` no objeto (0/1) |
| `s_zs` | zero-shot mDeBERTa em CPU (máximo entre labels); `LICITACOES_ZEROSHOT=0` desliga |
| `s_llm` | Groq `openai/gpt-oss-120b`, nota 0-100 ÷ 100, áreas A, B, C, D e F |

**Áreas do LLM** (`config.LLM_SYS`, colunas `llm_*`): (A) ML Engineering/MLOps,
(B) AI Engineering, (C) Data Science, (D) Forecasting, (F) Data Engineering, MDM e
governança. A antiga (E) Research Operations saiu do prompt em 18/10/2026; a coluna
`llm_research_operations` continua no banco, vazia daqui em diante. Os termos de pesquisa
operacional seguem na lista de palavras-chave (`TEMAS`).
Notas altas para serviços técnicos nessas áreas; médias para plataforma de IA com
implantação/customização; **baixas para data analytics/BI**, sistemas sem IA,
infraestrutura, licenças prontas, terceirização de mão de obra, cursos e consultoria
de gestão.

**Vocabulário brasileiro.** Os termos de mercado em inglês aparecem pouco em editais.
Os temas incluem os equivalentes usados aqui: *pesquisa operacional*, *otimização
matemática*, *roteirização*, *planejamento e controle da produção*, *S&OP*, *supply
chain / cadeia de suprimentos*, *ciência de dados industrial*, *manutenção preditiva*,
*gêmeo digital* (Research Operations); *previsão de demanda/carga/vazões*, *séries
temporais* (Forecasting); *aprendizado de máquina*, *modelos preditivos*, *IA
generativa*, *agentes de IA*, *visão computacional*, *processamento de linguagem
natural*. Nada de data analytics (BI, dashboards, painéis, data warehouse).

**Palavras de exclusão.** Da lista herdada saíram os termos que matavam justamente
os editais de interesse ("Máquina", "Treinamento", "Software", "Nuvem", "Servidores",
"Aquisição", "Registro de Preços", "Produção", "Indústria", "Manutenção", "Equipamentos"
etc.); o motivo de cada um está no comentário em `config.py`.

## Verticais

`licitacoes.verticais` recebe UMA categoria por objeto (`config.VERTICAIS`): as 5 áreas
de serviço como núcleo, mais "Data analytics, BI e engenharia de dados", "Licenças, SaaS
e plataformas prontas", "Desenvolvimento de sistemas e TI", "Capacitação" e as
operacionais herdadas. Teto de 300 por execução por causa da cota diária da Groq.
