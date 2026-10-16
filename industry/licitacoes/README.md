# licitacoes

Pipeline medallion que coleta licitações públicas, filtra e **pontua a relevância** para
serviços de **Machine Learning Engineering/MLOps, AI Engineering, Data Science,
Forecasting e Research Operations** (pesquisa operacional / ciência de dados industrial).
Porte da DAG `COMERCIAL_Licitacoes` do bateleur-pipelines, onde o foco era consultoria
financeira (M&A, business consulting, serviços financeiros, risco de crédito).

> Histórico das decisões da origem: [`DECISOES.md`](./DECISOES.md). As seções sobre
> e-mail, MinIO, GPU e Airflow não se aplicam mais; as regras de dedup, datas, prazos e
> recência continuam valendo.

## Fluxo (assets em `assets.py`)

```
licitacoes_coleta_<fonte> (15) → bronze → silver → gold → gold_filtros → verticais → score_atrasados
                                                                   └→ prazos
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
- **Coleta — fontes bancárias (herdadas):** ComprasNet e portais de bancos públicos.
  Coletor que falha registra o erro e não trava o resto; o asset check
  `licitacoes_coletas_ok` fica vermelho.
- **Gold:** remove objetos com `PALAVRAS_EXCLUIR` e mantém só o "acionável" (janela de
  proposta aberta). O filtro pelo texto do PDF do edital está desligado
  (`LICITACOES_FILTRAR_ANEXO`), porque termos genéricos aparecem em todo edital.
- **Destino:** schema `licitacoes` em `LICITACOES_PG_URL` (padrão: Supabase).

## Score de relevância

`score_combinado = 0.15·s_kw + 0.15·s_zs + 0.7·s_llm` (renormalizado se faltar sinal);
relevante quando `≥ 0.5`; corte de exibição sugerido `0.35`.

| Sinal | Origem |
|---|---|
| `s_kw` | keyword dos 5 `TEMAS` no objeto (0/1) |
| `s_zs` | zero-shot mDeBERTa em CPU (máximo entre labels); `LICITACOES_ZEROSHOT=0` desliga |
| `s_llm` | Groq `openai/gpt-oss-120b`, nota 0-100 ÷ 100, áreas A–E |

**Áreas do LLM** (`config.LLM_SYS`, colunas `llm_*`): (A) ML Engineering/MLOps,
(B) AI Engineering, (C) Data Science, (D) Forecasting, (E) Research Operations.
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
