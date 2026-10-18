# TODO

Lista interna. Só anotações: nada aqui está em andamento.

## Licitações: o que é preciso para concorrer

- [ ] Juntar uma amostra de editais abertos e relevantes (score alto) e baixar os documentos:
      edital, termo de referência e anexos. No PNCP saem pela API de arquivos da contratação.
- [ ] Ler os requisitos de habilitação desses editais e resumir o que se repete, em geral:
      - habilitação jurídica (contrato social, CNPJ, procuração);
      - regularidade fiscal e trabalhista (certidões federais, estaduais, municipais, FGTS, CNDT);
      - qualificação técnica (atestados de capacidade técnica, equipe mínima, certificações);
      - qualificação econômico-financeira (balanço, índices, patrimônio líquido ou capital mínimo,
        certidão de falência);
      - cadastros (SICAF, cadastro estadual ou municipal) e enquadramento ME/EPP;
      - garantia de proposta ou de contrato, visita técnica, amostra ou prova de conceito.
- [ ] Devolver ao Augusto o que precisa ser providenciado (o que já existe, o que falta,
      o que é impeditivo para um time pequeno, como atestados e capital mínimo).
- [ ] Avaliar extrair esses requisitos automaticamente (LLM sobre o texto do edital) para uma
      tabela `licitacoes.requisitos` e mostrar no home.

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
- [ ] Pesquisar como aplicar para essas licitações estrangeiras (cadastro, documentos, idioma,
      representação local, moeda e pagamento). Parcial: UNGM pede cadastro gratuito de
      fornecedor; Banco Mundial recebe manifestação de interesse por e-mail ao contato do aviso;
      PNUD "IC" e ONU "individual consultants" aceitam pessoa física. Falta o resto.
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

- [ ] Duplicata entre fontes: a mesma chamada do BNDES vem pelo `bndes` e pelo `bndes_inovacao`.
- [ ] LLM inconsistente em alguns casos (análise de perfil do Banestes oscila de 20 a 56; viabilidade
      alta em itens irrelevantes). Revisar com nova amostra após alguns dias de coleta.
- [ ] Coletores com Selenium (B3, Banco da Amazônia, CAIXA) só rodam com o Docker ligado.
- [ ] BDMG respondendo 403 (bloqueio do site).
- [ ] Registrar as execuções de licitações em `pipelines`/`pipeline_runs` para aparecerem no home.
- [ ] Testar a aba de licitações logado (branch `licitacoes` do home) e decidir o merge com a main.
- [ ] Notícias: a pipeline de M&A ficou sem pool desde que o setor M&A foi desligado.
- [ ] Cerebras sem crédito (402) e duas chaves Groq inválidas no `.env`.
