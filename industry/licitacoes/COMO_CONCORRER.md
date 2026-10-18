# Como concorrer

Levantamento de 18/10/2026. Duas partes: o que os editais brasileiros pedem na
habilitação, e o que é preciso para os avisos internacionais, com ou sem CNPJ.

## 1. Habilitação nos editais brasileiros

Amostra: as licitações do painel com relevância 40 ou mais, prazo aberto e edital
disponível. Foram 8; o edital do Banestes veio sem texto, então ficaram 7 (PNCP: prefeituras
de Santa Cruz do Capibaribe, Uberlândia, Goiana e Itajaí, Câmara de Marataízes, Banco
Central e Secretaria de Segurança Pública de um estado). Os PDFs do edital e dos anexos
foram lidos inteiros e as exigências marcadas por expressão regular.

| exigência | editais |
|---|---|
| habilitação jurídica (contrato social, ato constitutivo) | 7 de 7 |
| FGTS (CRF) | 6 de 7 |
| regularidade estadual e municipal | 6 de 7 |
| regularidade federal (certidão conjunta RFB e PGFN) | 5 de 7 |
| trabalhista (CNDT) | 5 de 7 |
| equipe técnica ou profissional qualificado | 5 de 7 |
| garantia de proposta ou de contrato | 5 de 7 |
| prova de conceito ou amostra | 5 de 7 |
| cadastro SICAF | 4 de 7 |
| visita técnica ou vistoria | 4 de 7 |
| certidão de falência e recuperação judicial | 4 de 7 |
| balanço patrimonial | 3 de 7 |
| índices contábeis (liquidez geral, solvência geral, liquidez corrente) | 3 de 7 |
| capital social ou patrimônio líquido mínimo (10% do valor estimado em Goiana e Marataízes) | 3 de 7 |
| consórcio permitido | 2 de 7 |
| exclusiva para ME e EPP | 1 de 7 |

Percentuais achados: patrimônio líquido de 10% do valor estimado (Goiana, Marataízes);
garantia contratual de 5% (Goiana).

**Limite da leitura.** As contagens são mínimas. Parte dos PDFs é escaneada e o
reconhecimento de texto erra ("licitagao", "somat6ria"); o atestado de capacidade técnica,
quase universal em TI, só foi detectado em 1 edital por isso (em Goiana aparece 23 vezes).
Em Santa Cruz do Capibaribe veio só o termo de referência, sem a parte de habilitação.

**O que isso significa para um time pequeno.** Todos pedem **CNPJ** (habilitação jurídica e
certidões são da empresa). Os impeditivos práticos são, nesta ordem: atestado de capacidade
técnica em serviço parecido (uma empresa nova não tem), patrimônio líquido de 10% do valor
(R$ 470 mil num edital de R$ 4,7 milhões), garantia contratual, e prova de conceito. A
regularidade fiscal e o SICAF são burocracia, não barreira.

## 2. Avisos internacionais

### Precisa de CNPJ?

**Não, para os avisos de consultor individual; sim, para os de empresa.**

| fonte | sem CNPJ (pessoa física) | com CNPJ (empresa) |
|---|---|---|
| Banco Mundial | "Individual Consultant Selection": manifestação de interesse com carta e CV assinado | CQS, QCBS e afins: firma, sozinha ou em consórcio |
| PNUD | "IC - Individual contractor" | RFP, RFQ, ITB, EOI: pedem certificado de registro da empresa e, em compras maiores, demonstrações financeiras |
| UNGM (agências da ONU) | "Call for individual consultants" | "Request for proposal" e afins |
| PRODOC (UNESCO, IICA nos ministérios) | consultor por produto, sempre pessoa física | não se aplica |

Os avisos de pessoa física são marcados na coleta (`pessoa_fisica = sim`, filtro "quem
pode concorrer" no home).

### Como aplicar, por fonte

- **Banco Mundial (projetos).** Quem contrata é o governo que toma o empréstimo, não o
  Banco. Manifestação de interesse por e-mail ao contato do aviso, com carta e CV datado e
  assinado. Não há cadastro prévio. O mais qualificado é chamado a negociar. Restrições
  comuns: não ser servidor público do país e não ter conflito de interesse.
- **PNUD.** Tudo pelo portal Quantum: cadastro de fornecedor (uma vez; não criar perfil
  duplicado), busca pelo número da negociação, inscrição na disputa para receber as
  alterações, perguntas pela mensageria do portal e envio antes do prazo, que é final.
  Proposta mandada por e-mail é desclassificada. Alguns avisos IC são só para nacionais do
  país do escritório.
- **UNGM.** Cadastro gratuito como "Individual Consultant": nome igual ao documento, um
  número oficial (o CPF serve), endereço e declaração de elegibilidade. O perfil fica
  visível depois de vinculado a pelo menos uma agência. Empresa tem os níveis 1 e 2
  opcionais, com certidão de constituição em inglês, referências e financeiro.
- **PRODOC.** Cadastro do CV no sistema do organismo (Roster da UNESCO, plataforma do IICA)
  dentro da janela do edital, que costuma ser curta (cerca de uma semana). Pede curso
  superior; o pagamento é por produto aprovado.

### Regras para a pessoa física no Brasil

- **Servidor público não pode** ser consultor em projeto de cooperação técnica (Decreto
  5.151/2004; exceção para professor universitário com horário compatível).
- **Imposto.** Pagamento de organismo internacional é rendimento do trabalho sem vínculo:
  carnê-leão mensal e declaração anual. Há isenção reconhecida pela Receita (Solução de
  Consulta 5.014/2019, com base no REsp 1.306.393 do STJ) para técnicos contratados no
  Brasil como consultores do PNUD; vale confirmar com um contador para cada organismo. A
  previdência é recolhida pelo próprio consultor.
- **MEI não serve** para consultoria em TI: o CNAE 6204-0/00 não tem ocupação permitida ao
  MEI, e exportar o serviço não muda isso. Para concorrer como empresa, o caminho é ME
  (Simples Nacional) ou sociedade limitada unipessoal.

### Fontes

- [UNGM, cadastro de consultor individual](https://www.ungm.org/Account/Registration/Individual)
- [UNGM, requisitos de cadastro](https://help.ungm.org/hc/en-us/articles/360012808340-What-are-the-requirements-for-registration-on-UNGM)
- [UNGM, categoria Individual Consultants](https://help.ungm.org/hc/en-us/categories/19701995835676-Individual-Consultants)
- [PNUD, aviso IC no UNGM (exemplo do processo no Quantum)](https://www.ungm.org/Public/Notice/308374)
- [Banco Mundial, aviso de consultor individual (exemplo, Paquistão)](https://download1.fbr.gov.pk/Tenders/202536832731563FinalREOI-AP&CMS.pdf)
- [Banco Mundial, seleção de consultor individual (exemplo, OECS)](https://oecs.int/en/work-with-us/procurements/current-tenders/oecs-tenders/consulting-services-for-an-ex-post-assessment-of-the-2020-round-of-the-population-and-housing-census)
- [PNUD, formulários de RFP com registro da empresa (Iraque)](https://www.iraq-businessnews.com/wp-content/uploads/2021/12/Returnable-bidding-forms.pdf)
- [Decreto 5.151/2004](https://www2.camara.leg.br/legin/fed/decret/2004/decreto-5151-22-julho-2004-533107-publicacaooriginal-16128-pe.html)
- [MDHC, diretrizes de seleção de consultoria PF 2026](https://www.gov.br/mdh/pt-br/navegue-por-temas/cooperacao-internacional/editais/editais-2026./2026_Diretrizes_selecao_consultoriasPF.pdf)
- [Isenção de IRPF para consultores da ONU contratados no Brasil](https://tributodevido.com.br/portal/isencao-de-irpf-para-consultores-da-unesco-e-onu-contratados-no-brasil/)
- [Carnê-leão para rendimentos de organismos internacionais](https://guiatributario.net/?p=7646)
- [Consultoria em TI não pode ser MEI](https://contabilidade.com/blog/consultoria-em-ti-pode-ser-mei-descubra-como-abrir-empresa/)
