# Plano: produto de inteligência de licitações

Decidido com o Augusto em 27/10/2026. Ainda não implementado. Expansões que ficaram
fora do MVP estão em `industry/TODO.md`, seção "Inteligência de licitações: depois do MVP".

## O produto

- **Cliente:** empresas que concorrem em licitações. Assessorias de licitação podem vir depois,
  mas não são o alvo inicial.
- **O que recebe:**
  - um **report inicial**: mercado do nicho dele, concorrentes, share dele, documentos mais
    exigidos. Pode ser refeito quando os dados se atualizarem;
  - **alertas por e-mail** quando sai uma licitação nova que serve para ele.
- **Diferencial:** a triagem por LLM (relevância, viabilidade, quem pode concorrer, nota
  técnica), não a lista de editais, que qualquer portal tem. O LLM vem incluso na
  assinatura: não é cobrado por uso, então o custo precisa ser controlado (filtro barato
  antes, cache por perfil e licitação).
- **Formato de entrega:** decidido por último. O mais provável é e-mail com PDF.
- **Prospects:** os players mapeados na frente 4 são os prospects deste produto.

## Restrições

- **Abrangência:** Brasil inteiro.
- **Infra:** Supabase grátis (500 MB), sem plano pago. O universo completo (licitações, itens,
  resultados, players) fica no Postgres local do compose (`postgres`, porta 5433). No Supabase
  só sobe o que o home e os clientes leem: funil por perfil, alertas e prospects.
- **Histórico:** o mais curto possível no MVP, 3 meses de PNCP. Share calculado em 3 meses
  oscila mais; serve para validar o fluxo.

## Frentes, na ordem

### 1. Coleta completa (base das outras)

- Coleta do PNCP **sem filtro de tema**: todas as contratações publicadas, todas as
  modalidades. O tema passa a ser um filtro por perfil, depois da coleta.
- Campos: datas (publicação, abertura e encerramento da proposta, sessão, homologação,
  vigência); local (UF, município, código IBGE, região, esfera, poder); órgão (CNPJ, nome,
  unidade); processo (modalidade, critério, modo de disputa, SRP, amparo, benefício ME/EPP,
  situação); valores (estimado, homologado, desconto); itens (descrição, CATMAT/CATSER,
  quantidade, valores); resultado por item (vencedor, CNPJ, porte, valor); contratos e atas.
- Tabelas no Postgres local: `licitacao`, `item`, `resultado`, `contrato`, `orgao`,
  `fornecedor`.

### 2. Nichos e visão de mercado

- Nicho = grupo CATSER/CATMAT refinado por clusters de embeddings da descrição do item; um
  LLM dá nome a cada cluster; nichos vizinhos no espaço de embeddings são "adjacentes".
- Por nicho: tamanho e crescimento, concentração (HHI, 4 maiores), disputa (participantes
  quando houver, desconto médio), barreira (documentos da frente 4).
- **MVP: só serviços e TI.** Obra, saúde, limpeza e compra de bens ficam de fora.

### 3. Players e prospects

- Ficha por CNPJ (grupo pela raiz do CNPJ e sócios da base da Receita em
  `scripts/receita_federal`): nichos, valor ganho, vitórias, participações, taxa de vitória
  onde houver, órgãos e UFs, porte, capital, CNAE, contato.
- **Quem perdeu, no MVP:** só onde vem pronto, no Compras.gov.br federal e no TCE-RS
  (LicitaCon). No resto do Brasil, só vencedores.
- **Prospect** = ativo (ganhou ao menos 1 vez em 12 meses no nicho ou adjacente), share abaixo
  de **2%** num nicho de **mais de R$ 5 mi/ano** e não dominado, com espaço grande; prioridade
  maior com sinal de dor (taxa de vitória baixa, muitas participações e poucas vitórias, poucas
  UFs). Os cortes são do MVP e serão recalibrados.
- Saída: tabela `prospects` do home, origem "licitações", com a frase de evidência. Nenhum
  contato sai sem autorização do Augusto.
- Atualização mensal (script), não DAG diária.

### 4. Documentos exigidos (script avulso)

- `scripts/licitacoes_documentos/analisar.py`: amostra estratificada (modalidade × esfera ×
  faixa de valor × nicho), lê a habilitação dos editais com regras (como `nota_tecnica.py`),
  valida com LLM numa subamostra, e gera frequência de cada documento por estrato.
- Entra no report inicial ("o que você precisa ter para disputar este nicho").

### 5. Parametrização por cliente

- Perfil por cliente: quem ele é (o texto que hoje está em `LLM_SYS`), temas e termos,
  palavras excluídas, verticais e núcleo, recorte geográfico, faixa de valor, modalidades,
  cortes, regra de participação, fontes.
- `gold_filtros`, `verticais` e `nota_tecnica` passam a ter chave `(perfil_id, id_unificado)`;
  a view `funil` filtra por perfil.
- O uso atual vira o perfil "augusto", e o funil dele precisa bater com o de hoje (teste de
  regressão).
- Perfil gerado por LLM a partir da descrição do cliente, revisado por ele.
- Alertas: licitação nova que passa no funil do perfil gera um alerta (o envio de e-mail fica
  para quando a entrega for decidida).
