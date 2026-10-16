"""
Configurações do agente de filtro de setor.
Tudo hardcoded — trocar valores aqui.
"""

# -----------------------------------------------------------------------------
# Setores
# -----------------------------------------------------------------------------
# Setores ligados. Economia, Saúde, M&A, Varejo, Financial Services e Real
# Estate foram desligados (a coleta deles também — ver coleta/config.py
# SETORES_ATIVOS); as descrições ficam em SECTOR_DESCRIPTIONS_DESLIGADOS para
# religar sem reescrever.
VALID_SECTORS: list[str] = [
    "Indústria",
    "Óleo e Gás",
    "Tecnologia",
    "Agro",
    "Automotivo",
]

CONTEXTO = (
    "As notícias abastecem uma consultoria de Machine Learning Engineering, Data "
    "Management e Data Engineering que atende empresas de cinco setores: Indústria, "
    "Óleo e Gás, Tecnologia, Agro e Automotivo. Interessam dois tipos de notícia:\n"
    "1) OPORTUNIDADES DE NEGÓCIO — empresa do setor investindo, expandindo, abrindo "
    "planta/operação, comprando ou fundindo-se com outra (integração de sistemas e "
    "dados), digitalizando, automatizando, adotando IA, sensores/IoT, rastreabilidade "
    "ou nuvem; regulação nova que obriga a coletar, integrar ou reportar dados.\n"
    "2) FATOS QUE MOVEM O MERCADO desses setores — preços e oferta de commodities, "
    "demanda, produção, comércio exterior, grandes contratos, resultados de empresas "
    "relevantes, política setorial, choques de custo e geopolítica com efeito direto.\n\n"
    "UMA NOTÍCIA PODE PERTENCER A MAIS DE UM SETOR. Ex.: 'John Deere lança "
    "plataforma de IA para colheita' → Agro, Tecnologia. Atribua TODOS os setores "
    "que façam sentido.\n\n"
    "CRITÉRIO DE LIXO — use 'Lixo' quando a notícia não tiver ligação plausível com "
    "nenhum dos 5 setores. Exemplos do que É Lixo:\n"
    "- Cerimônias e eventos protocolares de governo sem impacto econômico.\n"
    "- Crimes, polícia, esportes, celebridades, entretenimento, religião.\n"
    "- Política puramente eleitoral/partidária.\n"
    "- Bancos, crédito ao consumidor, mercado financeiro, varejo, saúde, imobiliário "
    "e macroeconomia geral (inflação, juros, PIB) SEM efeito direto e nomeado sobre "
    "os 5 setores.\n\n"
    "Exemplos do que NÃO é Lixo (classifique no setor adequado):\n"
    "- Juros ou câmbio citados como causa de mudança em investimento industrial, "
    "safra, preço de combustível ou venda de veículos.\n"
    "- Programas de governo para indústria, agro, energia ou veículos.\n"
    "- Geopolítica que afete petróleo, grãos, metais ou cadeias de suprimento.\n\n"
    "Regra de ouro: se a notícia tem ligação plausível e direta com um dos 5 "
    "setores, classifique no setor."
)

# Descrições resumidas de cada setor ligado, para orientar a classificação.
SECTOR_DESCRIPTIONS: dict[str, str] = {
    "Indústria": (
        "Produção industrial, siderurgia, metalurgia, química, papel e celulose, mineração, "
        "bens de capital e política industrial (Nova Indústria Brasil, PIM-PF, CNI). Inclui "
        "Indústria 4.0, automação, IoT industrial, manutenção preditiva, eficiência de planta, "
        "cadeias de suprimento, tarifas sobre manufaturados e nearshoring/reshoring."
    ),
    "Agro": (
        "Safras (soja, milho, trigo, arroz), pecuária, cooperativas, agroindústrias e "
        "logística; Plano Safra, crédito rural, exportações, bioenergia, fertilizantes e "
        "insumos. Inclui agricultura de precisão, agtechs, rastreabilidade, monitoramento "
        "por satélite/sensores e previsão de safra. Globalmente, demanda de China e EUA, "
        "cotações em Chicago, estoques mundiais e acordos comerciais."
    ),
    "Óleo e Gás": (
        "Exploração, produção, refino, distribuição e infraestrutura de petróleo, gás e "
        "biocombustíveis (Petrobras, ANP, distribuidoras, mercado de gás). Inclui setor "
        "elétrico e transição energética, digitalização de ativos, gêmeos digitais e "
        "monitoramento de operações. Globalmente, OPEP+, Brent/WTI, GNL, big oils e "
        "geopolítica energética."
    ),
    "Tecnologia": (
        "Startups, scale-ups, Big Techs, IA generativa e aplicada, dados e engenharia de "
        "dados, cloud, data centers, semicondutores, cibersegurança, regulação digital "
        "(LGPD, regulação de IA, antitruste) e M&A no setor. Inclui a disputa tecnológica "
        "EUA-China e o impacto da IA nos demais setores."
    ),
    "Automotivo": (
        "Produção, vendas, exportações e financiamento da cadeia automotiva — montadoras, "
        "autopeças, implementos e concessionárias. Inclui veículos elétricos e baterias, "
        "veículos conectados, software-defined vehicles, manufatura inteligente, tarifas "
        "sobre EVs e a cadeia global de semicondutores automotivos."
    ),
}

# Setores desligados — não entram no prompt. Mantidos para religar.
SECTOR_DESCRIPTIONS_DESLIGADOS: dict[str, str] = {
    'Varejo': (
        'Monitora vendas ao varejo (PMC), grandes redes (Magazine Luiza, Americanas, '
        'Carrefour, GPA), e-commerce (Mercado Livre, Amazon, Shopee), atacarejo (Assaí, '
        'Atacadão) e shoppings centers. Inclui crédito ao consumidor, confiança do consumidor'
        ' e recuperações judiciais no setor. Regionalmente, cobre redes do Sul e índices '
        'Fecomércio. Globalmente, acompanha Walmart, Amazon e tendências de consumo.'
    ),
    'Saúde': (
        'Abrange operadoras de planos, hospitais e laboratórios; regulação ANS, inflação '
        "médica, orçamento do SUS, judicialização e M&A (Rede D'Or, Hapvida, Dasa, Bradesco "
        'Saúde). Inclui novos medicamentos, healthtechs e modelos de remuneração médica. '
        'Globalmente, monitora crises sanitárias, P&D e tendências de sistemas de saúde.'
    ),
    'Real Estate': (
        'Abrange incorporação, lançamentos residenciais e comerciais, crédito imobiliário '
        '(SBPE, FGTS, MCMV) e mercados de lajes corporativas, galpões logísticos e shoppings.'
        ' No Brasil, monitora grandes incorporadoras (MRV, Cyrela, EZTec, Direcional), FIIs, '
        'CRIs e proptechs. Regionalmente, cobre o mercado do Sul, reconstrução pós-enchentes '
        'no RS e empreendimentos nas capitais. Globalmente, acompanha REITs, crise '
        'imobiliária chinesa, mercado americano e capital institucional em real estate.'
    ),
    'M&A': (
        'Monitora fusões, aquisições, desinvestimentos, private equity e venture capital — do'
        ' middle market regional a mega deals globais. Inclui aprovações do CADE, valuations '
        'por setor, roll-ups, take-privates, distressed M&A e IPOs como saída de PE. '
        'Globalmente, acompanha megafundos (Blackstone, KKR, Apollo) e activist investors.'
    ),
    'Financial Services': (
        'Abrange bancos (Itaú, Bradesco, Santander, BB, Caixa, BTG e fintechs como Nubank e '
        'Inter), crédito bancário, mercado de capitais (B3, debêntures, FIIs), câmbio, '
        'gestoras e regulação (CVM, BCB, Copom). Inclui cooperativas de crédito (Sicredi, '
        'Sicoob) e operações bancárias regionais. Globalmente, monitora grandes bancos, '
        'crises bancárias, política do FED e criptoativos institucionais.'
    ),
    'Economia': (
        'Monitora indicadores macroeconômicos — PIB, inflação (IPCA, IGP, CPI), mercado de '
        'trabalho (CAGED, PNAD), política fiscal (arcabouço, reforma tributária), dívida '
        'pública, balança comercial e câmbio. Abrange a atividade dos estados do Sul (RS, SC,'
        ' PR) e tendências globais de crescimento, política monetária, tensões comerciais e '
        'geopolítica econômica.'
    ),
}

# -----------------------------------------------------------------------------
# Banco — INDUSTRY_PG_URL (ver .env.example)
# -----------------------------------------------------------------------------
TABELA_ORIGEM = "noticias.noticias"
TABELA_DESTINO = "noticias.noticias_classificadas"

# -----------------------------------------------------------------------------
# LLM — via roteador de chaves (industry.shared.llm; chaves no .env). Substitui o
# Ollama local da origem pelo MESMO modelo (Qwen 3.8 27B, que a Groq e a Cerebras
# servem): os prompts desta pipeline foram afinados para ele. Raciocínio desligado, como o
# think=False da origem — em tarefa de formato fixo ele só gasta tokens.
# -----------------------------------------------------------------------------
import os  # noqa: E402

LLM_MODEL = os.getenv("NOTICIAS_CLASSIFICACAO_MODEL", "qwen3.8-27b")
LLM_REASONING_EFFORT = "none"
# 80 linhas "ID# setor1,setor2" cabem com folga.
LLM_MAX_TOKENS = 4096
TIMEOUT_S = 120

# -----------------------------------------------------------------------------
# Lotes
# -----------------------------------------------------------------------------
BATCH_SIZE = 80  # era 40

# Título que o LLM não devolveu (lote que falhou ou linha fora do formato)
# volta numa nova rodada só com os que faltam, em lotes menores. Antes ele só
# era tentado de novo no run seguinte, e se falhasse de novo saía da janela
# (hoje + ontem) sem setor — e sem setor a notícia nunca chega ao e-mail.
RODADAS_REPESCAGEM = 2
BATCH_SIZE_REPESCAGEM = 20
