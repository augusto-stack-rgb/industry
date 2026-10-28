# TODO

Lista interna. Só anotações: nada aqui está em andamento.

## Licitações: o que é preciso para concorrer

- [x] Juntar uma amostra de editais abertos e relevantes (score alto) e baixar os documentos:
      edital, termo de referência e anexos. No PNCP saem pela API de arquivos da contratação.
      Feito em 18/10: 8 editais com relevância 40 ou mais e prazo aberto (7 com texto legível).
- [x] Ler os requisitos de habilitação desses editais e resumir o que se repete, em geral
      (resultado em `industry/licitacoes/COMO_CONCORRER.md`):
      - habilitação jurídica (contrato social, CNPJ, procuração);
      - regularidade fiscal e trabalhista (certidões federais, estaduais, municipais, FGTS, CNDT);
      - qualificação técnica (atestados de capacidade técnica, equipe mínima, certificações);
      - qualificação econômico-financeira (balanço, índices, patrimônio líquido ou capital mínimo,
        certidão de falência);
      - cadastros (SICAF, cadastro estadual ou municipal) e enquadramento ME/EPP;
      - garantia de proposta ou de contrato, visita técnica, amostra ou prova de conceito.
- [x] Devolver ao Augusto o que precisa ser providenciado (o que já existe, o que falta,
      o que é impeditivo para um time pequeno, como atestados e capital mínimo). Em
      `COMO_CONCORRER.md`: todos pedem CNPJ; impeditivos são atestado, patrimônio líquido de 10%,
      garantia e prova de conceito.
- [ ] Avaliar extrair esses requisitos automaticamente (LLM sobre o texto do edital) para uma
      tabela `licitacoes.requisitos` e mostrar no home.

## Licitações: etapas de cada licitação com revisão humana

Pensado para as licitações novas que forem abrindo. O score automático continua como
triagem a priori; a decisão é do Augusto, pelo home.

- [ ] Registrar a etapa de cada licitação conforme ela é coletada e processada, inclusive as
      que pararam no caminho: coletada (bronze), passou ou caiu no filtro de palavras (gold),
      acionável ou não (prazo), pontuada (relevância e viabilidade), detalhada. Uma tabela de
      estado por `id_unificado`, com a etapa atual, o motivo de ter parado e a data de cada
      passagem.
- [ ] Revisão humana: estados definidos pelo Augusto (ex.: nova, em análise, interessa, vou
      aplicar, aplicada, descartada), gravados no banco por botões na aba Licitações do home,
      com data e observação opcional. A pipeline nunca sobrescreve um estado marcado à mão.
- [ ] O score a priori (palavras, zero-shot, LLM, viabilidade) continua definindo a ordem e o
      que chega à fila de revisão; não decide sozinho.
- [ ] No home: fila do que ainda não foi revisado, filtro por estado e histórico das marcações.
- [ ] Usar as marcações como rótulo para calibrar o score depois (o que foi descartado com
      nota alta, o que interessou com nota baixa).

## Licitações: o que é aplicável (definir antes de implementar)

Objetivo: marcar de forma determinística, sem depender de pedir caso a caso, se uma licitação
é aplicável para o Augusto. **Não implementar ainda**: primeiro definir o critério.

- [ ] Definir o que torna uma licitação aplicável. Candidatos a critério: aceita pessoa física
      ou exige CNPJ; exigências de habilitação (atestado, patrimônio líquido mínimo, garantia,
      prova de conceito, equipe mínima, certificações); valor e porte; prazo aberto; tema dentro
      dos serviços; idioma e país; modalidade e exclusividade ME/EPP; critério de julgamento e,
      quando houver nota técnica, quanto dela vem de histórico (atestados, experiência, equipe)
      e quanto da proposta (metodologia, solução). Ex.: CPSI de Itajaí, 100 pontos todos da
      proposta; Banco Mundial INDV, seleção só por CV (histórico); pregão de menor preço, sem
      nota técnica.
- [ ] Depois de definido: extrair esses campos do edital e da listagem (regra explícita, com o
      trecho do edital como prova) e gravar a marcação `aplicavel` com o motivo, para filtrar
      no home.

## Aplicações

- [ ] Aplicar para o CPSI The Ocean Race Itajaí 2027 (Edital nº 192/2026/SETEC). Propostas por
      e-mail para cpsi.setec@itajai.sc.gov.br até 23/10/2026, 23h59 (confirmar: o TR fala em 13/10;
      esclarecimentos até 20/10). Desafios mais próximos: D01 (inteligência de visitantes) e D03
      (inteligência da cadeia náutica), até R$ 1,6 mi cada.

## Licitações: novas fontes

- [x] Pesquisar fontes de licitações estrangeiras que aceitem empresas do Brasil (organismos
      multilaterais, governos e agências de outros países). Integradas em 17/10: Banco Mundial,
      PNUD e UNGM (agências da ONU). BID e AfDB bloqueiam acesso automático (403). Governos de
      outros países (TED da UE, SAM.gov dos EUA) não foram vistos. Em 18/10, com foco em pessoa
      física: entrou o PRODOC (consultor individual da UNESCO e do IICA nos ministérios) e a
      marcação `ambito` e `pessoa_fisica` no bronze, no painel e no home. Fora: ADB (Brasil não
      é membro), ReliefWeb (precisa de appname aprovado) e avaliador do Horizon Europe
      (cadastro, não licitação).
- [x] Pesquisar como aplicar para essas licitações estrangeiras (cadastro, documentos, idioma,
      representação local, moeda e pagamento). Feito em 18/10, em `COMO_CONCORRER.md`: consultor
      individual (Banco Mundial INDV, PNUD IC, UNGM, PRODOC) dispensa CNPJ; avisos de empresa
      exigem registro da empresa. Cadastro, envio, imposto (carnê-leão) e vedações por fonte.
- [x] Pesquisar outras fontes de contratos parecidos com licitações (chamadas, editais de inovação,
      compras de empresas privadas e estatais fora do PNCP). Integradas em 17/10: Sebrae (Canal
      do Fornecedor, todos os estados) e Petronect (Petrobras e subsidiárias). Finep ficou de fora
      (só financiamento). Não vistos: SESI/SENAI (um portal por estado) e compras privadas.

## Licitações: só o que está aberto para concorrer

- [ ] Separar o que é oportunidade do que é só registro publicado. Inexigibilidade, contrato, ata,
      homologação, adjudicação e extrato não estão abertos para disputa.
- [ ] Criar um status único por licitação (aberta, encerrada, sem disputa, desconhecido) a partir
      de modalidade, tipo de documento, situação e prazo de proposta. Hoje sem prazo conhecido é
      "assumido aberto".
- [ ] No home, mostrar só as abertas por padrão (com opção de ver o resto).

## Licitações: campos ainda não coletados ou não padronizados

Feito em 17/10 pelo asset `licitacoes_detalhes` (tabela `licitacoes.detalhes`, aparece no home):

- [x] Modalidade normalizada em todas as fontes (chega ao painel).
- [x] Situação, datas de abertura e encerramento da proposta para fontes fora do PNCP. Só onde a
      fonte publica: as novas trazem; bancos antigos e Querido Diário seguem sem prazo.
- [x] Critério de julgamento, modo de disputa, exclusividade ME/EPP, registro de preços. Completo
      no PNCP; nas outras fontes só o que a listagem traz (ME/EPP também lido no edital).
- [x] Link direto do edital e dos anexos (PNCP, Petronect e anexos que os coletores já traziam).
- [x] Valor estimado onde a fonte não traz: lido no PDF do edital (até 25 editais por execução).

## Pendências do que já foi feito

- [ ] LLM oscilando, medido em 18/10: 20 licitações de nota intermediária, pontuadas 4 vezes
      com o mesmo modelo (gpt-oss-120b, temperatura 0, lotes em ordem diferente). Relevância:
      amplitude média 17 pontos (de 0 a 100), mediana 18, máximo 60; metade dos itens varia 20
      pontos ou mais. Viabilidade: média 27, mediana 20, máximo 70. Itens claramente fora do tema
      (pneu, fralda) ficam sempre em 0. O modelo é o mesmo na Groq e na Cerebras; nenhum modelo
      existe nos três provedores (a Mistral só serve os dela). Falta decidir a correção (ex.:
      mediana de 3 rodadas, pontuar um item por chamada, ou critérios explícitos por campo).
      Diagnóstico (18/10, só Groq, 7 editais, 4 repetições): um edital por chamada varia 6
      (relevância) e 13 (viabilidade); lote fixo de 15, 3 e 11; lote embaralhado, como em
      produção, 13 e 30. O lote também DESLOCA a nota: cadastro territorial dá 80 sozinho e
      40 a 45 no lote; suporte de TI dá viabilidade 20 sozinho e 80 no lote.
- [ ] Não pontuar a viabilidade (ou descartar) quando a relevância já for baixa: a viabilidade
      só importa para o que é do tema. Definir o corte de relevância antes. Não fazer ainda.
- [ ] Cerebras sem crédito: as 4 chaves dão HTTP 402 na geração (a listagem de modelos
      funciona). Toda a pontuação está rodando só na Groq.
- [ ] BDMG respondendo 403 (bloqueio do site).
- [ ] Registrar as execuções de licitações em `pipelines`/`pipeline_runs` para aparecerem no home.
- [ ] Testar a aba de licitações no home (já está na main). Depois, quando o Augusto pedir.
- [ ] Notícias, M&A (herdado do bateleur-pipelines): os assets `mna_operacoes` e `mna_relatorio`
      extraem operações de fusão e aquisição das notícias do setor "M&A", que foi desligado em
      `SETORES_ATIVOS`. Rodam sem entrada. Decidir se desliga os assets.
- [x] Duas chaves Groq inválidas (HTTP 401): GROQ_API_KEY_4 e GROQ_API_KEY_6, ambas sem rótulo de
      conta no `.env`. Tiradas do rodízio em 18/10 (comentadas). As outras 6 da Groq, 4 da
      Cerebras e 4 da Mistral respondem.

## Prospects clientes: ganchos para abordar (não implementar sem pedido)

Universo: as empresas da segunda pipeline (`scripts/receita_federal/avaliar_clientes.py`,
página Clientes do home), que podem usar RAG sobre documentos ou dashboard de analytics. Mirar
em empresas pequenas e médias. Gancho = fato recente e verificável que liga a empresa a um dos
dois pacotes. Nenhum contato é feito pela pipeline.

Fontes levantadas em 20/10/2026, da mais forte e viável para a menos:

- [x] (20/10, `ganchos_vagas`: LinkedIn e Gupy; rende pouco nesse porte) Vagas que revelam dor (BI, PCP, controladoria, qualidade, documentação, assistência
      técnica, SAC técnico): Gupy, Indeed, Catho, Vagas.com e o "trabalhe conosco" do site.
      Casar com as vagas que a pipeline de vagas já coleta, por CNPJ ou nome. Os dois pacotes.
- [x] (20/10, `ganchos_rf` pela minhareceita.org e `ganchos_filiais` pelos zips da RF) Diferenças mensais na base da RF: filial nova, aumento de capital, CNAE novo, sócio ou
      administrador novo. Dashboard.
- [x] (20/10, `ganchos_sites`: sitemap e Last-Modified dos PDFs) Diferenças no site: PDFs, manuais e fichas novos, linha de produto nova, unidades,
      e-commerce, área do cliente ou do representante. Releitura do cache de páginas. RAG e
      dashboard.
- [ ] Contratos públicos ganhos, por CNPJ, no PNCP (já coletado pelas licitações).
- [x] (20/10, `ganchos_financiamento`: só BNDES) Financiamento: dados abertos do BNDES por CNPJ.
- [ ] Financiamento fora do BNDES: Finep, BRDE, Embrapii, Fapergs e Fapesc não publicam operações
      por CNPJ em dados abertos; seria raspagem de resultados de editais em PDF.
- [ ] Certificação ou registro novo: ISO 9001, Inmetro, Anvisa, MAPA. RAG.
- [ ] Expositores de feiras do setor (Mercopar, Fimec, Febratex, Expointer, Fenatran). RAG.
- [ ] Notícias regionais pelo nome: Google News RSS, Pioneiro, NSC, Simecs, Acij.
- [ ] Lista anual de empresas exportadoras do MDIC.
- [ ] Reclamações de atendimento (Reclame Aqui, Google): bloqueiam automação, só manual.
- [ ] Instagram e LinkedIn da empresa ou do dono: só verificação manual.

Desenho proposto: cada fonte vira um asset no Dagster e grava numa tabela `ganchos` (empresa,
tipo, data, pacote, fato, link da evidência); a página Clientes mostra os ganchos recentes, o
funil ganha "com gancho nos últimos 90 dias" e a prioridade combina encaixe com recência do
gancho. Primeira leva sugerida: vagas, diferenças na RF e diferenças no site.

- [ ] Rotina do Claude pela API que lê os dados das pipelines implementadas, identifica
      possíveis ganchos e adiciona uma notificação nova no home.
- [ ] Pensar em mais ganchos além da lista acima. Levantados em 22/10/2026 (o que consultorias e
      vendas B2B costumam usar: eventos-gatilho, sinais de intenção e canais):
  - [x] Marca ou patente depositada no INPI (22/10, `ganchos_inpi`, dados abertos do INPI).
  - [ ] Sócio pessoa jurídica entrando (aquisição ou investimento): virar tipo próprio na fonte da
        Receita, hoje cai como sócio novo comum.
  - [ ] Licença ambiental de instalação ou ampliação: Fepam (RS), IMA (SC), IAT (PR).
  - [ ] Mudança regulatória do setor (reforma tributária, NR-1, regulamento europeu de
        desmatamento, imposto de carbono europeu): gancho por setor, não por empresa.
  - [ ] Prêmios e rankings regionais (Maiores e Melhores, prêmios da Fiergs e da Fiesc).
  - [ ] Época de orçamento (outubro e novembro): mais quando abordar do que gancho.
  - [ ] Sinais de intenção: conteúdo próprio (diagnóstico de maturidade de dados, estudo
        regional do setor) e ver quem interage.
  - Descartados pelo Augusto: implantação de ERP (casos de sucesso de Totvs, Sankhya etc.) e
    ferramentas detectadas no site. Canais (Sebraetec, Senai e federações, parceiros de ERP,
    contadores, associações) não fazem sentido como pipeline.
- [ ] Demo de RAG sobre o site da empresa: um assistente que responde sobre o conteúdo e os
      PDFs do próprio site, para mostrar o pacote de RAG com os dados dela.

## Decisões em aberto (do Augusto)

- [ ] Prospects: avaliação de chance por sinais com citação. Pode usar LLM para extrair os sinais?
      Os resultados das abordagens vão ser registrados no home para calibrar a nota?
- [ ] Outsourcers: token do GitHub (opcional) para ler os 722 candidatos de uma vez.

## Inteligência de licitações: depois do MVP

Cortes feitos no MVP (plano em `industry/licitacoes/PLANO_PRODUTO.md`) que serão ampliados.

- [ ] Prospects: recalibrar os cortes do MVP (share abaixo de 2% num nicho de mais de R$ 5 mi
      por ano) depois de ver a distribuição real; aumentar o alcance.
- [ ] Nichos: incluir saúde, limpeza, obra e compra de bens (o MVP cobre só serviços e TI).
- [ ] Quem perdeu: além do Compras.gov.br federal e do TCE-RS, ler as atas de sessão em PDF
      do PNCP (amostra, depois tudo) e os dados abertos de outros TCEs (SP, PR, MG...).
- [ ] Histórico: ampliar de 3 meses para 12 meses e depois para tudo desde 2023 (Lei 14.133).
- [ ] Participantes e lances do pregão federal (Dados Abertos do Compras.gov.br) para medir
      disputa por nicho.
- [ ] Hospedagem: a coleta completa roda no Postgres local, então depende desta máquina ligada.
- [ ] Custo de LLM por cliente (incluso na assinatura): orçamento e provedor pago quando as
      chaves grátis do Groq não derem conta.
- [ ] Assessorias de licitação como segundo tipo de cliente.
