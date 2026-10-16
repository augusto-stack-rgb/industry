"""Configuração do relatório semanal de notícias por setor (e-mail opcional).

Porte da DAG MARKETING_EnvioNoticias (bateleur-pipelines). Mudanças:
- só os setores ligados (Agro, Óleo e Gás, Automotivo, Tecnologia, Indústria);
- foco das notas e do texto: oportunidades de negócio e fatos que movem o mercado,
  para uma consultoria de ML Engineering, Data Management e Data Engineering;
- e-mail DESLIGADO por padrão (ver ENVIO_EMAIL_ATIVO); quando ligado, vai só para
  os dois destinatários fixos em DESTINATARIOS, com todos os setores;
- LLM pelo roteador de chaves (industry.shared.llm) e embedding local em CPU.
"""
import os

QUANTOS_DIAS_ATRAS = 7  # Número de dias atrás para filtrar as notícias Exemplo: 1 = hoje ; 2 = ontem e hoje; 3 = anteontem, ontem e hoje
NUMERO_DE_NOTICIAS = 5
NUMERO_DE_NOTICIAS_N = 15

# Scoring em lotes (task_classificador.py): cada lote recebe uma nota
# absoluta 0-100 por notícia, sem eliminação — evita estourar o contexto/
# limite de tokens ao classificar setores com muitas notícias.
TAMANHO_LOTE_RANKING = 60  # notícias por lote enviado ao LLM (era 30)

# Modelo da nota de relevância e da redação: o mesmo Qwen 3.8 27B que a origem
# rodava no Ollama, agora pela API (roteador de chaves, Groq/Cerebras), com o
# raciocínio desligado (era think=False).
MODELO_CLASSIFICACAO = os.getenv("NOTICIAS_ENVIO_MODEL", "qwen3.8-27b")
MODELO_REDACAO = MODELO_CLASSIFICACAO
TIMEOUT_CLASSIFICACAO_S = 300

# Notícia que o LLM não devolveu (lote que falhou ou linha fora do formato)
# volta para uma nova rodada só com as que faltam, em lotes menores. Antes ela
# ficava com nota 0 e nunca entrava no e-mail — 47 de 586 em Óleo e Gás no
# envio de 28/09/2026.
RODADAS_REPESCAGEM = 2
TAMANHO_LOTE_REPESCAGEM = 20

# Recência DENTRO do mesmo assunto. O LLM marca o assunto de cada notícia e
# os rótulos parecidos são unificados por similaridade de embedding (rótulos,
# não títulos: nos títulos "petróleo sobe" × "petróleo afunda no Irã" deu 0,41,
# abaixo de pares sem relação; nos rótulos, mesmo assunto ≥ 0,83 e assuntos
# diferentes ≤ 0,74 — calibrado em Óleo e Gás, 21-28/09/2026). Num mesmo assunto, a mais recente
# fica com a nota cheia e as anteriores perdem metade da nota a cada
# MEIA_VIDA_MESMO_ASSUNTO_DIAS de distância dela. Notícia sozinha no assunto
# não é penalizada, qualquer que seja a idade: o preço do gás de ontem vence o
# da semana passada, mas não passa na frente da vacina nova da semana passada.
MEIA_VIDA_MESMO_ASSUNTO_DIAS = 3
# Embedding dos rótulos de assunto: na origem, bge-m3 no Ollama (GPU). Aqui o
# mesmo modelo roda localmente em CPU (transformers) — são só rótulos curtos.
MODELO_EMBEDDING_ASSUNTO = "BAAI/bge-m3"
LIMIAR_MESMO_ASSUNTO = 0.82

# Repetição dentro do grupo: além da recência, a 2ª notícia de um grupo vale
# PENALIDADE_REPETICAO da nota, a 3ª o quadrado disso, e assim por diante.
# Sem isso, notícias do mesmo assunto no MESMO dia (distância zero) entravam
# todas — no teste de 29/09/2026, 3 versões da compra do Nubank ocupavam 3 das
# 5 vagas de BRASIL em Tecnologia.
PENALIDADE_REPETICAO = 0.5

# "Mesmo fato" decidido pelo LLM numa lista curta: as TAMANHO_LISTA_MESMO_FATO
# mais bem avaliadas de cada setor × geografia (é de onde saem as 5 do e-mail).
# Pega a mesma história com rótulos de assunto diferentes (Tivit 25/09 × 28/09)
# e em idiomas diferentes. Embedding de título não serve para isso: a mesma
# história deu de 0,61 a 0,88 e histórias diferentes até 0,78.
TAMANHO_LISTA_MESMO_FATO = 20

# Nota do FATO, não da versão. A notícia que representa o fato (a 1ª do
# componente, fator de repetição 1) recebe a média das notas de todas as
# versões — a nota do LLM para o mesmo título oscilou de 55 a 90 conforme o
# lote, e a média amortece — mais BONUS_POR_PORTAL por portal além do
# primeiro, até TETO_BONUS_COBERTURA. Sem isso a cobertura não pesava: as
# multas da ANP (7 matérias, 5 portais, 25/09/2026) ficaram fora do top 5.
NOTA_DO_FATO = "media"  # "media" | "max" | "propria" (comportamento anterior)
BONUS_POR_PORTAL = 5
TETO_BONUS_COBERTURA = 15

# Nota com o começo do texto (CARACTERES_LEAD) e uma régua de faixas no prompt,
# em vez de só o título. Validação (envios de 21/09, 28/09 e 05/10/2026, Óleo e
# Gás e Varejo, 3 repetições): top 5 repetido entre runs 0,44 contra 0,27 só
# com título; empates na nota de corte 2,2 contra 5,4 do ranking anterior;
# multas da ANP no top 5 nas 3 repetições (antes 1 de 3). ~15% mais lento.
# Um desempate final pelo LLM nas 20 melhores também foi testado e piorou tudo
# (tirava fatos com mais cobertura) — descartado.
NOTA_COM_LEAD = True
CARACTERES_LEAD = 300

# O relatório é gerado todo dia (noticias.relatorios_email, que o home pode
# exibir); o e-mail, se ligado, só sai neste dia da semana (0 = segunda).
DIA_ENVIO_EMAIL = 0

# E-mail DESLIGADO por padrão (precisa de SMTP no .env). Liga com
# NOTICIAS_ENVIO_EMAIL=1. Os destinatários da DAG de origem foram removidos.
ENVIO_EMAIL_ATIVO = os.getenv("NOTICIAS_ENVIO_EMAIL", "0") == "1"


def calcular_unidade_de_tempo(dias: int) -> str:
    """Retorna a unidade de tempo em português com base no número de dias."""
    if dias == 1:
        return "dia"
    if dias % 7 == 0:
        semanas = dias // 7
        return "semana" if semanas == 1 else f"{semanas} semanas"
    return f"{dias} dias"


UNIDADE_DE_TEMPO = calcular_unidade_de_tempo(QUANTOS_DIAS_ATRAS)

# Setores do relatório (os mesmos ligados na coleta e na classificação).
SETORES = [
    "Indústria",
    "Óleo e Gás",
    "Tecnologia",
    "Agro",
    "Automotivo",
]

# Destinatários fixos: cada um recebe o relatório de TODOS os setores. Não há
# consulta de quem acompanha qual setor.
DESTINATARIOS: list[str] = [
    "augustolinuxdocker@gmail.com",
    "augustomohr@outlook.com",
]

# Ângulo da consultoria em cada setor: entra no contexto da nota junto com os
# temas de mercado de Setores_Contexto (abaixo).
OPORTUNIDADES_CONSULTORIA = {
    "Agro": [
        "Agricultura de precisão, sensores, imagens de satélite e previsão de safra/produtividade.",
        "Rastreabilidade e certificação (EUDR, sanidade, origem) que exigem integração de dados da cadeia.",
        "Cooperativas e agroindústrias investindo em sistemas, automação de planta e gestão de dados.",
        "Agtechs, plataformas digitais de originação, crédito e comercialização de grãos.",
    ],
    "Óleo e Gás": [
        "Digitalização de ativos: gêmeos digitais, monitoramento de plataformas, dutos e refinarias.",
        "Manutenção preditiva, otimização de produção e de refino, previsão de demanda e de preço.",
        "Regulação ANP/ANEEL e de emissões que exige medição, reporte e auditoria de dados.",
        "Contratos e investimentos de operadoras e distribuidoras em automação, dados e IA.",
    ],
    "Automotivo": [
        "Veículos conectados e software-defined vehicles: dados de frota, telemetria e IA embarcada.",
        "Manufatura inteligente: visão computacional na linha, qualidade, manutenção preditiva.",
        "Cadeia de baterias, semicondutores e autopeças: planejamento de demanda e risco de suprimento.",
        "Novas fábricas e plataformas (EVs, híbridos) e programas como o MOVER.",
    ],
    "Tecnologia": [
        "IA generativa e aplicada: adoção por empresas, agentes, LLMs, MLOps e governança de modelos.",
        "Dados: plataformas de dados, data centers, cloud, engenharia e governança de dados.",
        "Regulação de IA, LGPD e soberania de dados que mudam como empresas tratam dados.",
        "Rodadas, aquisições e parcerias de empresas de dados/IA que sinalizam demanda de mercado.",
    ],
    "Indústria": [
        "Indústria 4.0: IoT industrial, automação, MES, integração OT/IT e eficiência de planta.",
        "Manutenção preditiva, controle de qualidade com visão computacional, otimização de processos.",
        "Novas plantas, expansões e programas (Nova Indústria Brasil, BNDES Mais Inovação).",
        "Cadeias de suprimento, S&OP e planejamento de produção diante de tarifas e nearshoring.",
    ],
}

# Temas de mercado por setor e geografia (herdados da origem). Só os setores de
# SETORES são usados; os demais ficam para religar.
Setores_Contexto = {
    "Agro": {
        "REGIONAL_SUL": [
            "Safras (soja, milho, trigo, arroz, canola) — andamento de plantio/colheita, produtividade, quebras, qualidade e impacto no preço local.",
            "Pecuária (boi, suíno, aves, leite) — oferta de animais/leite, qualidade, controle sanitário, barreiras comerciais, custo de produção, pastagens, importações e exportações (do RS), e repasses ao produtor/indústria.",
            "Cooperativas — resultados, políticas de recebimento, assistência, crises, recuperações judiciais, investimentos e distribuição de sobras.",
            "Agroindústrias — finalidade, capacidade de processamento, margens, paradas de planta e demanda por matéria-prima.",
            "Armazenagem e logística — disponibilidade de silo, filas para entrega do grão, frete regional, investimento e fluxo dos portos e gargalos em escoamento.",
            "Impactos climáticos — seca/estiagem/excesso de chuva/geada/granizo/ciclone/veranico, efeito dos eventos na produtividade e infraestrutura, custos e risco de crédito.",
            "Movimentações industriais regionais — novas plantas, ampliações, fusões e aquisições, fechamentos e mudanças de compras na região.",
            "Preços locais e basis — diferença porto x interior, prêmios/deságios e sinal para venda/armazenagem.",
            "Sanidade vegetal e animal — surtos, barreiras sanitárias e exigências que travam ou liberam mercados.",
            "Infraestrutura e obras (rodovias/portos) — interdições e melhorias que alteram custo e tempo de transporte.",
            "Custos e disponibilidade de insumos regionais — sementes, defensivos, fertilizantes e prestação de serviço (máquinas)/mão de obra.",
        ],
        "BRASIL": [
            "Plano Safra — volume, taxas, limites e linhas.",
            "Crédito rural — apetite dos bancos, exigências, renegociações e inadimplência do setor.",
            "Exportações — ritmo de embarques, competitividade, barreiras comerciais e gargalos que mexem com preço interno.",
            "Frigoríficos — escalas, habilitações, mercados destino e repasses no boi gordo.",
            "Tradings — prêmios, originação, logística e concorrência na compra do grão.",
            "Fertilizantes e insumos — preço, oferta/importação e riscos de ruptura de fornecimento.",
            "Câmbio e juros — custo financeiro e paridade de exportação/importação no agro.",
            "Seguro rural e Proagro — cobertura, subvenção, regras e efeitos no risco do produtor e do crédito.",
            "Regulação e política setorial (MAPA/ambiental) — normas, fiscalizações e exigências que mudam custo e acesso a mercado.",
            "Infraestrutura e fretes (rodovia/ferrovia/porto) — disponibilidade, preços e eficiência no escoamento nacional.",
            "Bioenergia (etanol de milho, biodiesel, biogás) — demanda por grãos/óleos e novas fontes de receita no campo.",
            "Sustentabilidade e rastreabilidade — exigências de mercado, conformidade e risco reputacional/comercial.",
            "Tecnologia e agtech — digitalização, crédito/seguro via dados e ganhos de eficiência no campo.",
        ],
        "GLOBAL": [
            "Demanda da China e EUA — importações, estoques e política comercial que puxam preços e prêmios.",
            "Acordos comerciais entre países ou blocos econômicos — ALADI, MERCOSUL x União Europeia/EFTA/Singapura/Canadá/Índia/SACU/Israel/Palestina/Egito e Brasil x China.",
            "Commodities agrícolas — tendência de Chicago, spreads e volatilidade que guiam hedge e comercialização.",
            "Tensões geopolíticas — conflitos/sanções que afetam energia, fertilizantes, câmbios importantes, fretes e rotas.",
            "Safras e estoques globais — oferta mundial (EUA/Am. do Sul/Mar Negro) definindo piso e teto de preço.",
            "Energia e fretes marítimos — petróleo e custo de transporte mexendo na competitividade de exportação.",
            "Política monetária (EUA/Europa) — juros e dólar influenciando fluxo para commodities e custo de capital.",
            "Clima global (El Niño/La Niña) — risco climático em grandes produtores e efeito em preços futuros.",
            "Barreiras e regulações ambientais — rastreabilidade e compliance condicionando acesso a mercados premium.",
            "Fertilizantes no mercado internacional — preço e disponibilidade de NPK ligados a gás, sanções e capacidade industrial.",
            "Cadeias do Mar Negro — trigo/milho e risco logístico com impacto direto em preços globais.",
        ],
    },
    "Óleo e Gás": {
        "REGIONAL_SUL": [
            "Polo Petroquímico de Triunfo — operações, expansões, paradas programadas, investimentos da Braskem e demais ocupantes, e impacto na cadeia química regional.",
            "Refinaria Alberto Pasqualini (REFAP) — capacidade de processamento, modernização, paradas técnicas, mix de produtos e abastecimento da Região Sul.",
            "Bacia de Pelotas e exploração offshore no Sul — rodadas de licitação, descobertas, atividade exploratória e potencial impacto econômico no RS/SC.",
            "Distribuidoras regionais e postos de combustíveis — movimentos de bandeiras, consolidação local, margens de revenda e disputa por pontos estratégicos.",
            "Gás natural canalizado (Sulgás, Compagas, SCGÁS) — concessões, tarifas, novas conexões industriais e expansão da malha de distribuição.",
            "Cadeia regional de fornecedores — empresas de bens e serviços para óleo e gás (caldeiraria, manutenção, engenharia) com contratos com Petrobras e operadoras.",
            "Biocombustíveis regionais (biodiesel, etanol, biogás) — usinas instaladas no Sul, capacidade de produção, leilões da ANP e integração com o agro local.",
            "Logística de combustíveis e portos do Sul (Rio Grande, Paranaguá, São Francisco do Sul) — terminais, capacidade de movimentação e gargalos.",
            "Indústrias intensivas em energia regionais — frigoríficos, cerâmicas, vidro, papel e celulose com sensibilidade a preço de gás e óleo combustível.",
            "Movimentos de M&A regionais no setor — aquisições de distribuidoras, postos, empresas de serviços e ativos de gás canalizado.",
            "Investimentos em infraestrutura energética — gasodutos, terminais de GNL, regaseificação e projetos de transição (hidrogênio verde) no Sul.",
        ],
        "BRASIL": [
            "Petrobras — resultados trimestrais, governança, política de dividendos, investimentos do plano estratégico e política de preços de combustíveis.",
            "Leilões da ANP — rodadas de partilha e concessão, blocos arrematados, bônus de assinatura e perfil dos vencedores.",
            "Pré-sal — produção, novos campos em desenvolvimento, FPSOs contratadas e curva de produção nacional.",
            "Refino e desinvestimentos — alienações de refinarias, novos operadores privados (Acelen, Refit) e impacto na concorrência e preços.",
            "Distribuição de combustíveis — Vibra, Raízen, Ipiranga, movimentos de mercado, importações e participação de bandeiras brancas.",
            "Gás natural — abertura do mercado (Nova Lei do Gás), tarifas, contratos de longo prazo e entrada de novos comercializadores.",
            "Transição energética e descarbonização — investimentos em renováveis, hidrogênio, biocombustíveis, captura de carbono e metas ESG das petroleiras.",
            "Regulação ANP e CNPE — mudanças normativas, política de royalties, conteúdo local e regras de comercialização.",
            "Movimentos de M&A no setor — aquisições de campos maduros, ativos de midstream, postos, distribuidoras e empresas de serviços.",
            "Royalties e participações governamentais — arrecadação para estados/municípios produtores e disputas sobre distribuição.",
            "Setor offshore e cadeia de suprimentos — estaleiros, naval, sondas, embarcações de apoio e contratos com operadoras.",
        ],
        "GLOBAL": [
            "OPEP+ — decisões de cortes/aumentos de produção, cumprimento de quotas e impacto direto no preço do barril.",
            "Brent e WTI — cotação, volatilidade, spread entre marcadores e curva futura como referência de preço.",
            "Tensões geopolíticas (Oriente Médio, Rússia/Ucrânia, Venezuela) — sanções, ataques a infraestrutura e prêmios de risco no petróleo.",
            "Big Oils (ExxonMobil, Shell, Chevron, BP, TotalEnergies, Equinor) — resultados, estratégia, fusões e desinvestimentos globais.",
            "GNL global — capacidade de liquefação, novos terminais, rotas de exportação (EUA, Catar, Austrália) e preços spot.",
            "Estoques globais (EIA, AIE) — relatórios semanais/mensais que orientam preço e expectativa de mercado.",
            "Transição energética global — políticas de descarbonização, IRA nos EUA, Green Deal europeu e impacto na demanda futura por fósseis.",
            "Investimentos em energia renovável — solar, eólica offshore, hidrogênio verde e como Big Oils estão se reposicionando.",
            "Movimentos de M&A internacionais — mega deals entre petroleiras (ex.: ExxonMobil-Pioneer, Chevron-Hess) e consolidação no shale americano.",
            "Sanções e embargos — restrições a Rússia, Irã, Venezuela e efeitos no comércio mundial de petróleo e derivados.",
            "Câmbio e política monetária americana — dólar forte/fraco e efeito em commodities energéticas dolarizadas.",
        ],
    },
    "Tecnologia": {
        "REGIONAL_SUL": [
            "Parques tecnológicos (Tecnopuc, Tecnosinos, Tecnovates, Sapiens Parque, ParqTec UFSM) — empresas instaladas, expansões, novos inquilinos e captação de investimentos.",
            "Startups e scale-ups do Sul — rodadas de investimento, valuations, contratações relevantes e expansão de operação.",
            "Hubs de inovação e aceleradoras regionais — programas de aceleração, corporate ventures e parcerias com universidades.",
            "Empresas de software e serviços de TI regionais (DBServer, Dell, GeneXus, ThoughtWorks Porto Alegre, Stefanini, etc.) — contratos, expansões e movimentos estratégicos.",
            "Movimentos de M&A regionais — aquisições de software houses locais, fintechs, healthtechs e agtechs por players nacionais ou internacionais.",
            "Investimentos em data centers no Sul — novos sites, capacidade instalada e atração por incentivos fiscais.",
            "Universidades e P&D (UFRGS, UFSC, PUCRS, Unisinos) — pesquisas aplicadas, spin-offs acadêmicos e parcerias com indústria.",
            "Indústria 4.0 e automação regional — investimentos da indústria local em digitalização, IoT industrial e robótica.",
            "Ecossistema de venture capital e angel investors do Sul — fundos regionais, gestoras emergentes e principais cheques.",
            "Eventos e marcos do setor (South Summit Brazil, Web Summit Rio, FIEE) — anúncios relevantes, deals e tendências apresentadas.",
        ],
        "BRASIL": [
            "Grandes rodadas de investimento — séries A, B, C, D em scale-ups brasileiras, valuations e investidores líderes.",
            "Unicórnios brasileiros — performance pós-aporte, IPOs, down rounds e situações de estresse financeiro.",
            "Consolidação de SaaS — movimentos de aquisição entre empresas de software de gestão, vertical SaaS e plataformas B2B.",
            "Big Techs no Brasil — investimentos locais de Google, Microsoft, AWS, Meta, Apple em data centers, escritórios e parcerias.",
            "Regulação digital — LGPD, Marco Civil da Internet, PL das Fake News, regulação de IA e impactos no setor.",
            "Inteligência artificial no Brasil — adoção corporativa, startups de IA generativa, casos de uso e investimentos.",
            "Cibersegurança — incidentes relevantes, vazamentos de dados, regulação ANPD e crescimento do mercado de segurança.",
            "Cloud computing e infraestrutura digital — adoção corporativa, contratos governamentais e expansão das nuvens públicas.",
            "Telecomunicações e 5G — leilões, expansão de cobertura, M&A entre teles (Vivo, Claro, TIM, Oi) e impacto em verticais.",
            "Movimentos de M&A no setor — aquisições estratégicas, take-privates, fusões entre players e consolidação setorial.",
            "IPOs e listagens — aberturas de capital de techs brasileiras na B3 e em bolsas estrangeiras (Nasdaq, NYSE).",
            "Edtechs, healthtechs, fintechs, agtechs, proptechs — movimentos verticais, principais deals e tendências de cada segmento.",
        ],
        "GLOBAL": [
            "Inteligência artificial generativa — OpenAI, Anthropic, Google DeepMind, xAI, Meta AI, Mistral: lançamentos de modelos, rodadas de investimento e parcerias estratégicas.",
            "Chips e semicondutores — TSMC, Nvidia, AMD, Intel, Samsung, capacidade de fabricação, demanda por GPUs e gargalos de oferta.",
            "Big Tech (Google/Alphabet, Microsoft, Apple, Meta, Amazon) — resultados, lançamentos, investimentos em IA e movimentos antitruste.",
            "Mega deals e M&A internacional — aquisições bilionárias (ex.: Microsoft-Activision, Broadcom-VMware, Cisco-Splunk) e impacto setorial.",
            "Regulação antitruste — DOJ, FTC, União Europeia (Digital Markets Act, Digital Services Act) e processos contra Big Techs.",
            "Tensões EUA-China em tecnologia — restrições de exportação de chips, sanções, banimento de apps e desacoplamento tecnológico.",
            "Computação quântica — avanços de IBM, Google, IonQ, PsiQuantum e impacto futuro em criptografia e processamento.",
            "Cibersegurança global — grandes ataques, ransomware, atores estatais e movimentos de M&A entre empresas de segurança.",
            "Cloud computing global — disputa entre AWS, Azure, Google Cloud, Oracle Cloud e expansão geográfica de data centers.",
            "IPOs e listagens internacionais — abertura de capital de techs globais, performance pós-IPO e janelas de mercado.",
            "Private equity e venture capital em tech — megafundos (Sequoia, a16z, Tiger Global, SoftBank), tendências de tese e dry powder.",
            "Tecnologias disruptivas emergentes — robótica humanoide, biotech computacional, fusão nuclear, novas baterias e space tech.",
        ],
    },
    "Saúde": {
        "REGIONAL_SUL": [
            "Resultado das Operadoras de Planos de Saúde — divulgação dos dados trimestrais pela ANS.",
            "Inflação Médica — dados sobre a alta do preço de materiais e medicamentos, OPMEs, dados gerais de variação do nível de preço médico-hospitalar.",
            "Orçamento Público de Saúde (SUS) — alterações nas diretrizes orçamentárias do governo.",
            "Atualizações Regulatórias da ANS — atualização dos procedimentos do rol taxativo, índice de reajuste de planos individuais e novas determinações de impacto relevante.",
            "Movimentos de Consolidação do Setor — operações de fusões e aquisições dos principais players nacionais (Rede D'Or, Hapvida, Amil, Dasa, Bradesco Saúde, Fleury, Mater Dei, Kora Saúde, Oncoclínicas).",
            "Operações em Situação Crítica — hospitais e/ou planos de saúde que anunciem intervenções ou alto nível de endividamento.",
            "Novas Estruturas — investimentos relevantes em aumento de leitos, novos hospitais, unidades de SADT, entre outros.",
            "Risco de Judicialização — comportamento dos níveis de passivo judicial de operadoras e hospitais.",
            "Novos Produtos — anúncio de novos medicamentos, novos procedimentos, quebra de patentes.",
            "Modelos de Remuneração Médica — modelos mais difundidos e tendências em curso (fee for service, fee for value, bundled payment, capitation).",
            "Tecnologia — digitalização das operações, avanço com relação aos principais sistemas de mercado (Tasy, MV, softwares de previsibilidade de sinistro, inteligência artificial) e iniciativas de healthtechs.",
            "PROADI-SUS — editais abertos para participação de empresas de consultoria atuarem em parceria com hospitais e outras entidades.",
        ],
        "BRASIL": [
            "Resultado das Operadoras de Planos de Saúde — divulgação dos dados trimestrais pela ANS.",
            "Inflação Médica — dados sobre a alta do preço de materiais e medicamentos, OPMEs, dados gerais de variação do nível de preço médico-hospitalar.",
            "Orçamento Público de Saúde (SUS) — alterações nas diretrizes orçamentárias do governo.",
            "Atualizações Regulatórias da ANS — atualização dos procedimentos do rol taxativo, índice de reajuste de planos individuais e novas determinações de impacto relevante.",
            "Movimentos de Consolidação do Setor — operações de fusões e aquisições dos principais players nacionais (Rede D'Or, Hapvida, Amil, Dasa, Bradesco Saúde, Fleury, Mater Dei, Kora Saúde, Oncoclínicas).",
            "Operações em Situação Crítica — hospitais e/ou planos de saúde que anunciem intervenções ou alto nível de endividamento.",
            "Novas Estruturas — investimentos relevantes em aumento de leitos, novos hospitais, unidades de SADT, entre outros.",
            "Risco de Judicialização — comportamento dos níveis de passivo judicial de operadoras e hospitais.",
            "Novos Produtos — anúncio de novos medicamentos, novos procedimentos, quebra de patentes.",
            "Modelos de Remuneração Médica — modelos mais difundidos e tendências em curso (fee for service, fee for value, bundled payment, capitation).",
            "Tecnologia — digitalização das operações, avanço com relação aos principais sistemas de mercado (Tasy, MV, softwares de previsibilidade de sinistro, inteligência artificial) e iniciativas de healthtechs.",
            "PROADI-SUS — editais abertos para participação de empresas de consultoria atuarem em parceria com hospitais e outras entidades.",
        ],
        "GLOBAL": [
            "Tendências de Saúde Global — principais doenças que acometem a população por região no mundo.",
            "Pesquisa e Desenvolvimento — novos medicamentos, procedimentos, aplicações de robótica em cirurgias.",
            "Sistemas de Saúde — situações críticas ou positivas a partir de alterações no perfil de consumo/prestação de serviço em sistemas de saúde de referência no mundo.",
            "Crises Sanitárias — alerta de doenças que possam desencadear uma pandemia global.",
        ],
    },
    "Automotivo": {
        "REGIONAL_SUL": [
            "Polo automotivo de Gravataí (GM) — produção, novos modelos, investimentos, demissões/contratações e impacto na cadeia regional.",
            "John Deere (Horizontina/RS) — produção de máquinas agrícolas, exportações, demanda do agro e investimentos na planta.",
            "Implementos rodoviários (Randon, Noma, Guerra, Librelato) — produção, vendas, exportação e demanda de transportadores.",
            "Carrocerias e ônibus (Marcopolo, Comil, Caio) — pedidos do mercado interno e exportação, especialmente para América Latina e África.",
            "Cadeia de autopeças regional — fornecedores tier 1 e 2 instalados no Sul, contratos com montadoras e movimentos de M&A.",
            "Concessionárias regionais — performance de vendas, consolidação de grupos (ex.: Grazziotin, Disnave, Carbel), e mix de marcas.",
            "Tratores e máquinas agrícolas (AGCO, CNH, Stara, Jacto) — produção no Sul, demanda do agro local e inovação em maquinário.",
            "Movimentos de M&A regionais — aquisições de concessionárias, autopeças e empresas de serviços automotivos.",
            "Mobilidade elétrica regional — postos de recarga, frotas elétricas em municípios e investimentos de montadoras.",
            "Logística e transporte regional — caminhões, frotas, demanda por veículos comerciais ligada ao agro e indústria do Sul.",
        ],
        "BRASIL": [
            "Anfavea — produção, vendas internas e exportações mensais de veículos, indicadores de mercado e estoques.",
            "Montadoras instaladas no Brasil (GM, VW, Stellantis, Toyota, Hyundai, Honda, Renault, Nissan, BYD, GWM, Caoa Chery) — investimentos, novos modelos e estratégias.",
            "Vendas internas (Fenabrave) — emplacamentos por categoria (leves, comerciais, pesados, motos), market share e tendências.",
            "Programas governamentais — Mover, Rota 2030, incentivos fiscais, IPI verde e impacto na competitividade.",
            "Veículos elétricos e híbridos no Brasil — entrada de marcas chinesas (BYD, GWM, Chery), infraestrutura de recarga e adoção.",
            "Produção industrial automotiva — utilização de capacidade, paradas técnicas, layoffs/contratações e suspensões.",
            "Cadeia de autopeças nacional (Sindipeças) — faturamento, exportações, dependência de importações e M&A no setor.",
            "Caminhões e veículos pesados — Volvo, Scania, Mercedes-Benz, MAN, Iveco, DAF: demanda do agro, logística e infraestrutura.",
            "Movimentos de M&A — aquisições de plantas, joint ventures, entrada de novos players e consolidação de concessionárias.",
            "Financiamento automotivo — bancos das montadoras, taxas, inadimplência, prazos e penetração de financiamento nas vendas.",
            "Exportações de veículos — destinos principais (Argentina, México, América Latina), acordos automotivos e câmbio.",
        ],
        "GLOBAL": [
            "Transição para veículos elétricos — penetração global, metas de banimento de combustão e investimentos das montadoras.",
            "Tesla — produção, entregas, lançamentos, performance financeira e movimentos estratégicos de Elon Musk.",
            "BYD e montadoras chinesas (Geely, NIO, Xpeng, Li Auto, Chery, GWM) — expansão global, exportações e disputa com ocidentais.",
            "Montadoras tradicionais (Toyota, VW, GM, Ford, Stellantis, Hyundai-Kia, Honda, Renault-Nissan) — estratégia EV, resultados e reestruturações.",
            "Cadeia global de baterias — produção de células, gigafactories, principais fornecedores (CATL, LG, Panasonic, BYD) e tecnologias.",
            "Lítio, níquel, cobalto e terras raras — mineração, preços, geopolítica de commodities críticas e segurança de suprimento.",
            "Tarifas e barreiras comerciais — tarifas dos EUA/UE sobre EVs chineses, retaliações e fragmentação do mercado global.",
            "M&A automotivo internacional — fusões, alianças, joint ventures (ex.: Honda-Nissan, Stellantis) e consolidação setorial.",
            "Veículos autônomos — Waymo, Cruise, Tesla FSD, Mobileye, regulação e progresso técnico.",
            "Software-defined vehicles — corrida por plataformas digitais, parcerias com Big Tech e nova arquitetura veicular.",
            "Cadeia de semicondutores automotivos — gargalos, parcerias com TSMC/Samsung e impacto na produção global.",
        ],
    },
    "M&A": {
        "REGIONAL_SUL": [
            "Aquisições de empresas no RS, SC e PR — deals envolvendo empresas da Região Sul como alvo ou comprador, com foco em tickets relevantes para middle market.",
            "Consolidação de mercados regionais — movimentos de roll-up em setores fragmentados do Sul (varejo, distribuição, serviços, saúde, educação).",
            "Entrada de players nacionais e internacionais via aquisição — grupos que estabelecem presença regional comprando empresas locais.",
            "Joint ventures regionais — parcerias estratégicas entre empresas do Sul e players nacionais ou estrangeiros, especialmente em agro, indústria e tech.",
            "Sucessão familiar e venda de empresas familiares — oportunidades de M&A em empresas tradicionais do Sul com transição geracional.",
            "Aquisições por fundos de private equity com tese regional — fundos com foco em Sul/middle market e empresas em portfólio.",
            "Desinvestimentos regionais — venda de unidades de negócio, plantas industriais ou subsidiárias localizadas no Sul.",
            "M&A em cooperativas — fusões entre cooperativas (agro, crédito, saúde) e aquisições de ativos por sistemas cooperativos.",
            "Reorganizações societárias e cisões — movimentos estruturais em grupos regionais que abrem oportunidades de assessoria.",
            "Recuperações judiciais e ativos estressados no Sul — oportunidades de aquisição de empresas em RJ, distressed M&A e turnaround.",
            "Investimentos greenfield versus aquisição — escolha estratégica de novos entrantes na região.",
        ],
        "BRASIL": [
            "Grandes aquisições no país — deals acima de R$ 1 bilhão, identificando comprador, alvo, múltiplos pagos e tese estratégica.",
            "Fusões entre líderes de mercado — combinações que reconfiguram setores (ex.: Hapvida-Notre Dame, Localiza-Unidas).",
            "Private equity no Brasil — captações de novos fundos, principais gestoras (Advent, Pátria, Vinci, Kinea, IG4, GP) e movimentos de portfólio.",
            "Venture capital — rodadas de investimento, novos fundos brasileiros, corporate ventures e teses emergentes.",
            "Desinvestimentos e spin-offs — empresas alienando ativos não-core, separações estruturais e listagens de subsidiárias.",
            "Aprovações e restrições do CADE — análise de operações, remédios estruturais/comportamentais e prazos.",
            "Tese de roll-up setorial — consolidações em saúde, educação, varejo de farmácias, pet, serviços B2B e tecnologia.",
            "Take-privates — fechamento de capital de empresas listadas via OPA, com participação de PE ou controladores.",
            "M&A em recuperação judicial — UPIs, leilões 60-A, aquisições de ativos estressados e oportunidades em distressed.",
            "Cross-border inbound — aquisições de empresas brasileiras por estrangeiros, especialmente em agro, energia, infraestrutura e tech.",
            "Cross-border outbound — multinacionais brasileiras adquirindo ativos no exterior (JBS, Marfrig, WEG, Embraer, etc.).",
            "Múltiplos de transação por setor — EV/EBITDA pagos por setor, tendências de valuation e benchmarks de mercado.",
            "Assessores de M&A — bancos de investimento e boutiques mais ativos (BTG, Itaú BBA, Bradesco BBI, JP Morgan, Bank of America, Riza, Vinci Compass).",
            "IPOs e follow-ons como saída de PE — janelas de IPO, performance pós-listagem e impacto na atividade de M&A.",
        ],
        "GLOBAL": [
            "Mega deals internacionais — operações acima de US$ 10 bilhões, com foco em rationale estratégico e impacto setorial.",
            "Consolidação global de setores — ondas de M&A em farmacêutico, semicondutores, energia, defesa, mídia e tecnologia.",
            "Aquisições estratégicas por Big Tech — movimentos de Microsoft, Google, Amazon, Meta, Apple e Nvidia, com escrutínio antitruste.",
            "Cross-border M&A — fluxos entre EUA, Europa, China e mercados emergentes, e impacto de tensões geopolíticas.",
            "Private equity global — megafundos (Blackstone, KKR, Apollo, Carlyle, Bain Capital, CVC), captações recordes e dry powder.",
            "Fundos soberanos — movimentos de PIF (Arábia Saudita), Mubadala, ADIA, Temasek, GIC, Norges Bank em ativos globais.",
            "Take-privates internacionais — fechamento de capital de grandes listadas via PE ou consórcios.",
            "Antitruste internacional — bloqueios e remédios impostos por DOJ, FTC, Comissão Europeia, CMA britânica e SAMR chinesa.",
            "M&A em transição energética — aquisições em renováveis, baterias, hidrogênio, captura de carbono e infraestrutura verde.",
            "M&A em IA e semicondutores — deals que reconfiguram a corrida tecnológica global e implicações de segurança nacional.",
            "Distressed M&A global — oportunidades em ciclos de aperto monetário, refinanciamentos forçados e vendas de portfólio.",
            "SPACs e De-SPACs — atividade de empresas-veículo, listagens reversas e performance pós-fusão.",
            "Activist investors — campanhas de Elliott, Starboard, Pershing Square, Trian forçando spin-offs, vendas e mudanças estratégicas.",
        ],
    },
    "Financial Services": {
        "REGIONAL_SUL": [
            "Banrisul — resultados trimestrais, estratégia, dividendos, possível privatização e movimentos de mercado.",
            "Cooperativas de crédito do Sul (Sicredi, Sicoob, Cresol, Unicred) — resultados, expansão de cooperativas singulares, fusões e participação de mercado.",
            "Fintechs regionais — rodadas de investimento, lançamentos de produto e movimentos de M&A locais.",
            "Crédito rural regional — performance da carteira agro dos bancos com forte atuação no Sul, inadimplência e renegociações.",
            "Bancos digitais com origem no Sul (Banco Topázio, Agibank, Banco Original) — estratégia, captação e expansão.",
            "Movimentos de consolidação bancária regional — aquisições de cooperativas, financeiras locais e correspondentes bancários.",
            "Crédito imobiliário e consignado regionais — performance no Sul, principais financiadores e cooperativas atuantes.",
            "Inadimplência regional — comportamento do crédito PJ e PF na Região Sul, com foco em agro, indústria e comércio.",
            "Empresas do Sul no mercado de capitais — emissões de debêntures, CRIs, CRAs e ofertas de ações de companhias com sede no RS, SC e PR.",
            "Rodadas de investimento em startups regionais — séries seed, A, B e late stage de scale-ups do Sul, com investidores e valuations.",
            "Family offices regionais — movimentações relevantes de patrimônio familiar, alocação em alternativos e investimentos diretos.",
            "Gestoras de recursos com origem no Sul — captação, performance e lançamento de novos fundos por asset managers regionais.",
            "Fundos de venture capital e private equity regionais — teses, captações e investimentos de fundos com pegada no Sul.",
            "IPOs e ofertas de empresas do Sul — listagens na B3 ou bolsas estrangeiras de companhias regionais.",
            "Crédito estruturado regional — operações de FIDC, FII, project finance e financiamento estruturado para empresas do Sul.",
            "Mercado segurador regional — performance de seguradoras com forte atuação no Sul, em especial seguro rural e patrimonial.",
            "Câmaras e associações regionais (FEBRABAN regional, OCERGS) — posicionamentos, dados setoriais e iniciativas conjuntas.",
            "Eventos e fóruns financeiros regionais — South Summit, eventos do BACEN no Sul, fóruns de investimento e network local.",
        ],
        "BRASIL": [
            "Decisões do Copom e taxa Selic — reuniões, ata, comunicados e direcionamento de política monetária do Banco Central.",
            "Grandes bancos (Itaú, Bradesco, Santander, Banco do Brasil, Caixa, BTG Pactual) — resultados trimestrais, ROE, carteira de crédito e estratégia.",
            "Fintechs e bancos digitais (Nubank, Inter, C6, PicPay, Will Bank) — performance, lucratividade, base de clientes e movimentos estratégicos.",
            "Open Finance e Pix — evolução da adoção, novos casos de uso (Pix Automático, Pix Garantido) e impacto competitivo.",
            "Regulação CMN/Bacen — mudanças normativas, capital regulatório, regras prudenciais e impactos na rentabilidade bancária.",
            "Crédito e inadimplência — evolução de carteiras (PF, PJ, agro, imobiliário), provisões e risco do sistema.",
            "M&A bancário — fusões entre bancos, aquisições de fintechs por bancos tradicionais e desinvestimentos.",
            "Consolidação de cooperativas — fusões de sistemas cooperativos, aquisições e ganho de escala (Sicredi, Sicoob, Unicred, Cresol).",
            "Mercado de capitais bancário — emissões de LCI, LCA, CRIs, CRAs, debêntures financeiras e captações no exterior.",
            "DREX e infraestrutura financeira — real digital, tokenização de ativos, blockchain bancário e iniciativas conjuntas do setor.",
            "Gestoras e bancos de investimento — XP, BTG, Itaú BBA, Bradesco BBI, Santander, JP Morgan, movimentos competitivos e ofertas.",
            "B3 e Ibovespa — performance do índice, fluxo estrangeiro, principais altas e quedas e indicadores de mercado.",
            "Regulação CVM — novas instruções, mudanças de regras de OPA, fundos, distribuição pública e fiscalização.",
            "Private equity e venture capital — captações, investimentos, exits e principais gestoras atuantes no país.",
            "Mercado de dívida e debêntures — emissões corporativas, debêntures incentivadas, CRIs, CRAs, FIDCs e CCBs.",
            "Reforma tributária e impactos no mercado financeiro — fim do JCP, tributação de fundos exclusivos, dividendos e offshore.",
            "Tesouro Direto e renda fixa — leilões do Tesouro, demanda por NTN-B, LFT e LTN, e curva de juros.",
            "IPOs e follow-ons na B3 — janela de mercado, ofertas em pipeline, performance pós-listagem e investidores estratégicos.",
            "Fundos imobiliários (FIIs) — performance, dividend yield, novas ofertas e tendências por segmento (logística, lajes, shoppings, papel).",
            "Investidores institucionais — movimentos de fundos de pensão, seguradoras, EFPC e family offices na alocação de ativos.",
            "Gestoras independentes — performance, captação e movimentos competitivos entre asset managers (XP, BTG Asset, Itaú Asset, JGP, SPX, Verde, Adam, Kapitalo).",
            "Inadimplência corporativa e recuperações judiciais — pedidos de RJ relevantes, reestruturações e impacto no crédito.",
            "Mercado segurador nacional — performance de seguradoras, sinistralidade, novos produtos e M&A no setor.",
        ],
        "GLOBAL": [
            "Federal Reserve (FED) — decisões de juros, sinalizações de Powell, balanço do FED e impacto em mercados emergentes.",
            "Bancos centrais globais — BCE, BoE, BoJ, PBoC: decisões de política monetária e divergências entre blocos.",
            "Crises bancárias — falências, intervenções (ex.: SVB, Credit Suisse, First Republic) e risco sistêmico.",
            "Big banks internacionais (JP Morgan, Bank of America, Citi, Goldman Sachs, Morgan Stanley, HSBC, UBS) — resultados e movimentos.",
            "M&A bancário global — fusões transfronteiriças, consolidação europeia e americana, e aquisições de gestoras.",
            "Regulação internacional — Basileia III/IV, stress tests da FED/BCE, regras de capital e liquidez globais.",
            "Bancos chineses e asiáticos — performance, exposição imobiliária, política de Pequim e impacto global.",
            "Private banking e wealth management global — movimentos de UBS, Morgan Stanley, JP Morgan e captação de patrimônio.",
            "Sanções financeiras — exclusão de SWIFT, congelamento de ativos e fragmentação do sistema financeiro global.",
            "Mercados acionários globais — S&P 500, Nasdaq, Dow Jones, Stoxx 600, Nikkei, CSI 300 e drivers de cada bolsa.",
            "Fusões e aquisições internacionais de grande porte — mega deals, racional estratégico e impacto em índices setoriais.",
            "Private equity global — movimentos de Blackstone, KKR, Apollo, Carlyle, Brookfield, Bain Capital, CVC e captações recordes.",
            "Fundos soberanos — alocações de PIF, Mubadala, ADIA, Temasek, GIC, Norges Bank em ativos globais e mercados emergentes.",
            "Criptoativos e ativos digitais institucionais — ETFs de Bitcoin/Ethereum, adoção institucional, regulação SEC/MiCA e tokenização.",
            "Mercado de bonds globais — Treasuries americanos, bunds alemães, JGBs japoneses, curvas de juros e spreads de crédito.",
            "Hedge funds globais — performance e estratégias de Citadel, Bridgewater, Millennium, Renaissance, Two Sigma, Point72.",
            "IPOs internacionais — listagens em NYSE, Nasdaq, LSE, Hong Kong, performance e janela global de ofertas.",
            "Activist investors globais — campanhas de Elliott, Starboard, Pershing Square, Trian e impacto em estruturas corporativas.",
            "Regulação financeira internacional — SEC, FINRA, ESMA, FSB, e harmonização global de regras.",
        ],
    },
    "Real Estate": {
        "REGIONAL_SUL": [
            "Lançamentos residenciais e comerciais no Sul — novos empreendimentos nas capitais (Porto Alegre, Florianópolis, Curitiba) e interior, VGV, tipologia e absorção de vendas.",
            "Incorporadoras regionais — resultados, novos lançamentos, bancos de terrenos e estratégia de expansão de players com foco no Sul (Cyrela, MRV, EZTec, Direcional, Bairro Novo).",
            "Mercado de escritórios (lajes corporativas) — vacância, absorção líquida, preços de locação e novos projetos AAA em Porto Alegre, Curitiba e Florianópolis.",
            "Logística e galpões industriais — demanda por condomínios logísticos no Sul, expansão de GLP, Prologis, Bresco e impacto do e-commerce.",
            "Shopping centers regionais — desempenho de vendas, vacância, expansões e novas inaugurações de ABL na Região Sul.",
            "Mercado de terrenos urbanos — movimentos de aquisição de glebas, operações consorciadas e valorização em áreas de expansão.",
            "Habitação de interesse social (HIS) e MCMV — produção regional, financiamentos CEF e impacto da política habitacional no mercado.",
            "Retomada pós-enchentes no RS — reconstrução, deslocamento de demanda habitacional e oportunidades de incorporação em regiões não afetadas.",
            "Mercado de locação residencial — preços de aluguel por capital, pressão de demanda e plataformas (QuintoAndar, Loft) na região.",
            "FIIs com ativos no Sul — FIIs de lajes, logística e shoppings com imóveis nas cidades do Sul e impacto nos rendimentos.",
        ],
        "BRASIL": [
            "Crédito imobiliário (SBPE e FGTS) — volume de concessões, taxas dos bancos, teto de financiamento e capacidade de compra das famílias.",
            "Selic e custo de financiamento — impacto dos juros sobre parcelas, demanda por crédito e competitividade do CRI frente ao SBPE.",
            "Grandes incorporadoras (MRV, Cyrela, EZTEC, PDG, Direcional, Trisul, Mitre, Even, Helbor) — resultados trimestrais, VSO, velocidade de vendas e banco de terrenos.",
            "MCMV e habitação popular — metas do programa, financiamentos CEF, déficit habitacional e pressão política sobre taxas e subsídios.",
            "Mercado de escritórios nas principais cidades — vacância, novos desenvolvimentos, conversão para residencial e demanda de co-working.",
            "Galpões logísticos e industrial — expansão de ABL, contratos BTS, demanda do e-commerce, farmacêutico e alimentos.",
            "Shoppings e varejo físico — ABL, vendas por m², vacância, expansões e fusões entre operadores (Multiplan, Iguatemi, Allos/BRMALLS).",
            "FIIs (Fundos de Investimento Imobiliário) — performance por segmento (papel, tijolo, híbrido), dividend yield, novas emissões e fluxo de investidores.",
            "CRI e dívida imobiliária — emissões no mercado de capitais, spreads, securitizadoras (True, Habitasec, RB Capital) e demanda de investidores.",
            "Hotéis e hospitalidade — ocupação, diárias, entrada de bandeiras internacionais e desenvolvimento de ativos.",
            "Proptechs e tecnologia imobiliária — startups do setor (Loft, QuintoAndar, Terraz, Housi), rodadas de investimento e modelos de negócio.",
            "Regularização fundiária e marco legal — atualizações na legislação (Lei do Distrato, Marco de Garantias), impacto em contratos e projetos.",
            "Real estate corporativo e sale & leaseback — transações de ativos ocupados por grandes empresas, precificação e demanda de fundos.",
        ],
        "GLOBAL": [
            "Mercado imobiliário americano — case shiller, home sales, housing starts, impacto do FED sobre mortgage rates e dinâmica de oferta.",
            "Crise imobiliária chinesa — Evergrande, Country Garden, liquidez de developers, política de Pequim e efeito sistêmico.",
            "Real estate europeu — ciclo de desvalorização de ativos, vacância de escritórios pós-pandemia e ajuste de portfolios de fundos.",
            "REITs globais — performance por segmento (residencial, logística, data centers, saúde, varejo) e comparação com FIIs brasileiros.",
            "Mercado de data centers — demanda explosiva por IA, novos desenvolvimentos, players (Equinix, Digital Realty, IronMountain) e localização.",
            "Build-to-rent e multifamily — crescimento do mercado institucional de locação residencial nos EUA e Europa e migração para o Brasil.",
            "Capital global em imobiliário — movimentos de fundos soberanos, PE (Blackstone, Brookfield, Pimco RE) e gestoras globais em ativos imobiliários.",
            "Impacto do trabalho remoto e híbrido — absorção de escritórios, conversão para residencial, movimentos de desocupação corporativa.",
            "Logística global e nearshoring — expansão de galpões pelo realinhamento de cadeias produtivas e oportunidade para mercados emergentes.",
            "Sustentabilidade e certificações (LEED, BREEAM, EDGE) — exigência de ESG em portfólios institucionais e precificação de imóveis verdes.",
        ],
    },
    "Economia": {
        "REGIONAL_SUL": [
            "PIB regional (RS, SC, PR) — desempenho econômico dos estados do Sul, contribuição setorial e comparação com média nacional.",
            "Inflação regional — IPCA das regiões metropolitanas de Porto Alegre e Curitiba, INPC regional e desvios em relação ao Brasil.",
            "Mercado de trabalho regional — CAGED por estado, desemprego regional (PNAD Contínua), rendimento médio e formalização.",
            "Atividade industrial no Sul — PIM-PF regional, utilização de capacidade instalada e indicadores da FIERGS, FIESC e FIEP.",
            "Comércio varejista e serviços regionais — PMC e PMS por estado, indicadores Fecomércio e desempenho sazonal.",
            "Arrecadação estadual — receita de ICMS dos estados do Sul, arrecadação federal regional e indicadores de atividade.",
            "Finanças públicas estaduais — endividamento de RS/SC/PR, Regime de Recuperação Fiscal, capacidade de investimento e relação com governo federal.",
            "Investimentos públicos estaduais — PPI estaduais, concessões, parcerias público-privadas e investimentos em infraestrutura.",
            "Demografia regional — envelhecimento populacional, migração inter-regional e impacto na força de trabalho do Sul.",
            "Renda e desigualdade regional — distribuição de renda, índice de Gini, pobreza e indicadores sociais nos estados do Sul.",
            "Custo de vida regional — preços de moradia, alimentação e serviços nas principais capitais e cidades do Sul.",
            "Eventos climáticos extremos e impacto econômico — enchentes, secas e estiagens com efeito sobre PIB e arrecadação regional.",
        ],
        "BRASIL": [
            "IPCA e indicadores de inflação — divulgações mensais do IBGE, núcleos de inflação, difusão e dinâmica de preços livres x administrados.",
            "IGP-M, IGP-DI e IPA — indicadores da FGV, inflação no atacado e impacto em contratos (aluguel, energia, planos de saúde).",
            "PIB brasileiro — divulgações trimestrais e anuais, contribuição setorial (agro, indústria, serviços), formação bruta de capital fixo e consumo das famílias.",
            "Mercado de trabalho — PNAD Contínua, CAGED, taxa de desemprego, rendimento real, informalidade e participação na força de trabalho.",
            "Política fiscal e arcabouço fiscal — meta de resultado primário, cumprimento do arcabouço, despesas obrigatórias e teto de gastos.",
            "Reforma tributária — implementação do IBS/CBS, calibragem de alíquotas, regimes especiais e impacto setorial.",
            "Dívida pública — DBGG, DLSP, perfil de vencimentos, custo da dívida, leilões do Tesouro e resultado nominal.",
            "Balança comercial — exportações, importações, saldo comercial, principais parceiros (China, EUA, Argentina, UE) e termos de troca.",
            "Conta corrente e investimento estrangeiro direto — fluxo de capitais, IED, investimento em carteira e financiamento externo.",
            "Câmbio e reservas internacionais — drivers do real, intervenções do BCB, posição em swaps cambiais e nível de reservas.",
            "Confiança do consumidor e empresário — ICC, ICEI, índices da FGV/CNI/Fecomércio e expectativas de mercado.",
            "Indústria, comércio e serviços — PIM-PF, PMC, PMS do IBGE e indicadores de atividade do BCB (IBC-Br).",
            "Crédito agregado — concessões, estoque, spread bancário, inadimplência e direcionamento por segmento.",
            "Risco-país e CDS Brasil — percepção de risco soberano, rating das agências (S&P, Moody's, Fitch) e prêmio sobre Treasuries.",
            "Política e impacto fiscal — eleições, transição governamental, reformas estruturais e sinalizações fiscais do governo.",
            "Boletim Focus e expectativas de mercado — projeções de IPCA, Selic, PIB, câmbio e revisões periódicas.",
            "Programas sociais e gastos governamentais — Bolsa Família, BPC, INSS, abono salarial e impacto fiscal.",
            "Salário mínimo — política de valorização, reajustes e efeito sobre Previdência, contas públicas e consumo.",
        ],
        "GLOBAL": [
            "Crescimento global — projeções do FMI (WEO), Banco Mundial e OCDE, e diferenciação entre economias avançadas e emergentes.",
            "Inflação global — CPI dos EUA, HICP da Eurozona, inflação no Reino Unido, Japão e principais emergentes.",
            "Mercado de trabalho americano — payroll (NFP), taxa de desemprego, JOLTS, salários e impacto na política do FED.",
            "Política monetária global — ciclo de juros do FED, BCE, BoE, BoJ, PBoC e divergências entre blocos.",
            "China — desaceleração econômica, crise imobiliária (Evergrande, Country Garden), estímulos fiscais/monetários e política industrial.",
            "Tensões comerciais e tarifas — disputas EUA-China, tarifas sobre EVs e aço, retaliações e fragmentação do comércio global.",
            "Desglobalização e nearshoring — realocação de cadeias produtivas, friend-shoring e oportunidades para Brasil e América Latina.",
            "Cadeias de suprimento globais — gargalos logísticos, custos de frete (Baltic Dry, Drewry), Canal de Suez, Canal do Panamá e estoques.",
            "Demografia global — envelhecimento populacional na Europa/Japão/China, bônus demográfico em emergentes e impacto fiscal.",
            "Crises de dívida soberana — países em estresse (Argentina, Egito, Paquistão, Sri Lanka), reestruturações e papel do FMI.",
            "Geopolítica e impacto econômico — conflitos (Ucrânia, Oriente Médio, Taiwan), sanções, choques energéticos e prêmios de risco.",
            "Commodities como termômetro econômico — petróleo, cobre (Dr. Copper), minério de ferro, ouro e grãos como sinalizadores de atividade.",
            "Eleições globais relevantes — pleitos nos EUA, Europa, América Latina e impacto em política econômica e mercados.",
            "Indicadores antecedentes globais — PMIs (manufatura/serviços), índices de surpresa econômica, ISM, Conference Board LEI.",
            "Política industrial e subsídios — IRA nos EUA, Chips Act, Green Deal europeu, Made in China 2025 e nova era de intervenção estatal.",
            "Risco climático e transição — eventos climáticos extremos, custos de transição, taxação de carbono (CBAM) e impacto macroeconômico.",
            "Mercados emergentes — fluxos de capital, vulnerabilidades externas, política monetária e diferencial de juros (carry trade).",
        ],
    },
}

# A origem não tinha contexto para Indústria (o setor não ia no e-mail).
Setores_Contexto["Indústria"] = {
    "REGIONAL_SUL": [
        "Polos industriais do Sul (metalmecânico, químico, celulose, alimentos, calçados, móveis) — investimentos, expansões, fechamentos e demissões.",
        "Novas plantas e ampliações no RS, SC e PR — capacidade, empregos, incentivos fiscais e fornecedores locais.",
        "Reconstrução pós-enchentes no RS — retomada de fábricas, logística e cadeia de suprimentos regional.",
        "Energia e custo industrial regional — tarifas, gás natural e infraestrutura para a indústria.",
    ],
    "BRASIL": [
        "Produção industrial (PIM-PF), utilização de capacidade (CNI) e confiança da indústria.",
        "Política industrial — Nova Indústria Brasil, BNDES, depreciação acelerada, conteúdo local.",
        "Siderurgia, metalurgia, química, papel e celulose, mineração — preços, margens e investimentos.",
        "Bens de capital e máquinas (Abimaq) — encomendas, investimento produtivo e modernização.",
        "Comércio exterior de manufaturados — tarifas, antidumping, competitividade e câmbio.",
    ],
    "GLOBAL": [
        "Cadeias globais de suprimento — tarifas EUA/China, nearshoring e friend-shoring.",
        "Siderurgia e metais — excesso de capacidade chinês, preços do aço e do minério.",
        "Automação e robótica industrial — adoção, fornecedores globais e Indústria 4.0.",
        "Política industrial nos EUA, Europa e China (IRA, Chips Act, Made in China 2025).",
    ],
}
