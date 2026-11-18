# Contexto da conversa: licitações e base de mercado (out–nov/2026)

Resumo da sessão longa com o Claude que montou a pipeline de licitações e a base de mercado.
Serve para retomar o trabalho sem reler a conversa.

## 1. Funil de licitações (DAG Dagster, `industry/licitacoes/`)

- **Recentes:** janela de 7 para 14 dias (`JANELA_BRONZE_DIAS=14`, `db.bronze_abertos_fora_da_silver`).
- **Tema:** filtros mais abertos para CPSI e desafios passarem, mesmo com algum lixo.
  Novas `PALAVRAS_EXCLUIR`, `termos.e_desafio()`, limite de descrição maior para desafios
  (`LLM_MAX_DESC_DESAFIO=2500`) e uma regra de CPSI/desafio no prompt do LLM.
- **Núcleo × tema:** o núcleo agora inclui CPSI/desafio por regex ou pela fonte.
- **Relevantes:** relevância > 35 **e** (viabilidade > 60 **ou** `exige_cnpj = false`).
- **CNPJ:** `nota_tecnica.participacao()` classifica cada edital em pessoa_fisica, startup,
  restrito, me_epp ou comum. Gera as colunas `exige_cnpj`, `requisito_cnpj` e
  `requisito_evidencia`. Os documentos com token JWT da Cemig são resolvidos por `_cemig()`.
- O funil (view em `home/supabase/schema.sql`) tem as etapas 9–12 para CNPJ, a 13 para
  "pesos conhecidos" e a 14 para "proposta > 50%".
- **Correções de infraestrutura:**
  - SQLAlchemy fixado em `<2.1`, porque a 2.1 trocou o driver para psycopg3.
  - Storage do Dagster com o scheme `postgresql+psycopg2`.
  - Bytes 0x08 nos regex vinham de heredoc: scripts com regex são salvos com Write.

## 2. Produto: inteligência de licitações (decidido em 27/10)

Plano completo em `industry/licitacoes/PLANO_PRODUTO.md`; o que ficou para depois do MVP está
em `TODO.md`, na seção "Inteligência de licitações: depois do MVP".

- **Cliente:** empresas que concorrem em licitações. Assessorias talvez mais tarde.
- **Produto:** alertas com LLM e um relatório inicial, parametrizados por cliente.
- **MVP:**
  - Brasil inteiro;
  - Supabase grátis;
  - histórico curto (30 dias);
  - entrega decidida depois (provavelmente alertas por e-mail ou PDF);
  - custo do LLM incluído na assinatura;
  - nichos de serviços e TI.
- **Fora do MVP:** saúde, limpeza e nichos parecidos; mapear quem perdeu (versão
  simplificada por enquanto).

## 3. Base de mercado (`industry/mercado/`, Postgres local via `MERCADO_PG_URL`)

O volume não cabe no Supabase grátis, então a base fica no Postgres do docker-compose
(porta 5433).

- **Fontes:**
  - PNCP (contratações e contratos do Brasil inteiro, cerca de 13 req/min; insistir
    gera 429);
  - Compras.gov.br Dados Abertos (itens e resultados, só 20–30% do PNCP e com cerca de
    1 mês de atraso);
  - SICAF (`1_consultarFornecedor`, cerca de 826 mil fornecedores ativos).
- **Quem perdeu:** não existe API aberta com os participantes perdedores.
- **Modalidade:** também vem embutida no `idCompra`, nas posições 7–8: 05 pregão,
  03 concorrência, 06 dispensa, 07 inexigibilidade, 00 credenciamento.
- **Scripts:**
  - `coleta.py`: resumível por (fonte, dia, chave), com ritmo adaptativo (`fontes.Ritmo`);
  - `fornecedores.py`: SICAF, com `--desde <página>`;
  - `modalidades.py`: resolve no PNCP a modalidade das compras ligadas a contratos de
    fora da janela;
  - `nichos.py`: embeddings MiniLM, MiniBatchKMeans e nomes dados pelo LLM; tamanho do
    nicho, HHI, CR4 e share;
  - `prospects.py`: universo de prospects.

### Definição de prospect (decidido em 06/11)

- **Quem entra:** qualquer empresa que trabalhou para o governo ou se cadastrou para
  licitar (SICAF), mesmo sem ter ganho nada. Entra em qualquer nicho, com os técnicos e de
  TI primeiro.
- **Sem cortes:** nada é filtrado. Share (em %), nicho, porte, vitórias por modalidade,
  "técnico", "gigante" e "entidade pública" viram colunas.
- **Score:** de 0 a 100, serve só para ordenar.
- **Saídas:** a tabela `mercado.prospect_universo`, o arquivo
  `.data/mercado/prospect_universo_<data>.parquet` e o arquivo
  `.data/mercado/prospects_<data>.csv`. O CSV traz quem ganhou alguma coisa ou é técnico.
- **Versão anterior:** feita com o SICAF parcial, tinha 253 mil empresas, 11,5 mil delas
  técnicas.

### Supervisor (`supervisor.ps1`)

- **O que faz:** roda as etapas na ordem SICAF → coleta → modalidades → nichos →
  prospects.
- **Proteções:**
  - mutex, para não rodar duas vezes;
  - `SetThreadExecutionState`, para o Windows não suspender;
  - religa o Docker e o Postgres antes de cada tentativa;
  - repete cada etapa até concluir.
- **Arquivos:** etapa concluída vira `.data/mercado_etapa_<etapa>.ok`, o log fica em
  `.data/mercado_supervisor.log` e o fim é marcado em `.data/mercado_supervisor.fim`.
- **Rodar:**

  ```
  powershell -NoProfile -ExecutionPolicy Bypass -File industry\mercado\supervisor.ps1
  ```

- **Atenção:** o supervisor religa o Docker (e com ele a vmmem/WSL). Para desligar tudo,
  mate primeiro o supervisor, depois o Docker Desktop e `com.docker.service`, e só então
  rode `wsl --shutdown`.
- **Reinício do Windows:** o supervisor não volta sozinho, porque a política de permissões
  negou criar o atalho na pasta Inicializar.

## 4. Estado em 13/11/2026 (tudo parado a pedido do usuário)

| Etapa | Situação |
|---|---|
| SICAF | ✅ completo (`mercado_etapa_fornecedores.ok`) |
| Coleta PNCP + Compras.gov.br, de 28/09 a 10/11 | ✅ completa (`mercado_etapa_coleta.ok`) |
| Modalidades | ⏸️ parada, cerca de 4.900 compras pendentes (umas 6 h) |
| Nichos | ⏳ falta |
| Universo de prospects | ⏳ falta |

- Docker, vmmem e o supervisor estão desligados.
- A suspensão do Windows voltou ao normal: AC 30 min, DC 15 min. Enquanto o supervisor
  roda, ela fica desligada (`powercfg /change standby-timeout-ac 0`).
- O Docker Desktop continua na inicialização do Windows (`HKCU\...\Run`) e religa a vmmem
  no logon.
- **Para retomar:** suba o supervisor. Ele pula SICAF e coleta, que já estão marcados, e
  segue a partir de modalidades.

## 5. Regras combinadas

- Nunca contatar prospects nem subir a lista para o Supabase/home sem permissão explícita.
- Ignorar licitações internacionais.
- Commits com o e-mail do Gmail como autor; commit e push estão autorizados.
- Respostas curtas e diretas.
