"""Configuração da pipeline de licitações (medallion + score de relevância).

Pipeline: coleta (bronze) -> processamento (silver) -> filtro de palavras
excluídas no objeto (gold) -> score dos 3 filtros (gold_filtros) -> vertical.

Foco do score: contratação de SERVIÇOS de Machine Learning Engineering/MLOps,
AI Engineering, Data Science, Forecasting e Research Operations (pesquisa
operacional / ciência de dados industrial). Portado do repositório
bateleur-pipelines, onde o foco era consultoria financeira/estratégica (M&A,
business consulting, serviços financeiros, risco de crédito) — os prompts,
temas, keywords e verticais abaixo foram reescritos para o novo foco.

Destino: Postgres (INDUSTRY_PG_URL), schema `licitacoes` (DDL em sql/init.sql;
gold_filtros/prazos_proposta/verticais são criadas pelo db.py).
"""
import os
from datetime import datetime
from pathlib import Path

# ── Pipeline ─────────────────────────────────────────────────────────────────
PIPELINE_ID = "licitacoes"
DAG_ID = PIPELINE_ID                         # nome legado usado em logs/arquivos
DEST_DIR = Path("/tmp/licitacoes")          # dir temporário local dos coletores

# ── Banco (medallion) ─────────────────────────────────────────────────────────
from industry.shared.connections import LICITACOES_PG  # noqa: E402

PG_CONN = LICITACOES_PG
SCHEMA = "licitacoes"
TB_BRONZE = "bronze"
TB_SILVER = "silver"
TB_GOLD = "gold"
TB_GOLD_FILTROS = "gold_filtros"
# Prazos de proposta do PNCP (abertura/encerramento), persistidos no
# filtrar_acionaveis. Ver DECISOES.md §6/§7.
TB_PRAZOS = "prazos_proposta"
# Vertical (categoria de negócio) do objeto, classificada por LLM no asset
# `licitacoes_verticais`. Lida pelo home (CRM pessoal).
TB_VERTICAIS = "verticais"
# Campos de detalhe (critério, ME/EPP, SRP, anexos, valor do edital) dos itens do
# gold_filtros, preenchidos pelo asset `licitacoes_detalhes` (detalhes.py).
TB_DETALHES = "detalhes"

# ── Anexos (gravados em $INDUSTRY_DATA_DIR/licitacoes/<run_id>/...) ──────────
ANEXOS_PREFIX = "anexos"          # <dag_id>/anexos/<fonte>/<arquivo>
ANEXOS_TEXTO_PREFIX = "anexos_texto"  # texto extraído (<md5>.txt)

# ── Coleta ────────────────────────────────────────────────────────────────────
REQUEST_TIMEOUT = 30
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
# Recorte RECENTE (check semanal do que é novo) — apenas o ano corrente.
# Todas as fontes limitam a coleta por aqui; a dedup por row_hash no bronze
# garante que só entram os inéditos. Dinâmico: rola sozinho na virada do ano.
_ANO_HOJE = datetime.now().year
ANOS_ALVO = {str(_ANO_HOJE)}

# ── Superset de colunas do BRONZE (licitacoes.bronze) ────────────────────
# Colunas conceitualmente unificadas dos coletores; o restante da linha nativa
# vai em `dados_originais` (jsonb). Espelha o DDL de licitacoes.bronze.
BRONZE_COLS = [
    "id_unificado", "fonte", "id_nativo", "numero", "numero_controle_pncp", "processo",
    "tipo", "modalidade_cod", "tipo_documento", "categoria",
    "titulo", "objeto", "observacao", "situacao", "tem_resultado",
    "data", "data_abertura", "data_publicacao", "data_sessao", "data_encerramento_proposta",
    "data_inicio_vigencia", "data_fim_vigencia", "ano",
    "valor", "valor_estimado", "valor_contratado", "valor_homologado",
    "contratado", "contratado_cnpj", "orgao", "orgao_cnpj", "unidade",
    "comprador", "banco", "responsavel",
    "uf", "municipio", "local_abertura",
    "link", "site_projeto", "documentos", "arquivos_baixados",
    "edital_url", "edital_nome", "qtd_documentos",
    "criterio_julgamento", "modo_disputa", "tipo_julgamento",
    "fonte_rotulo", "filtros_usados", "data_coleta",
    "dados_originais", "row_hash", "run_date",
    # marcacoes para filtrar (bronze.marcar): nacional/internacional e se aceita
    # pessoa fisica (consultor individual, sem CNPJ): "sim" ou "nao"
    "ambito", "pessoa_fisica",
]

# Colunas do SILVER e GOLD (imagem enviada pelo usuário).
GOLD_COLS = ["id_unificado", "fonte", "tipo", "objeto", "situacao",
             "data", "valor", "link", "contratado", "numero"]
# silver = as mesmas + se casa com os temas (gold.filtrar_tema; lido pelo funil do home)
SILVER_COLS = GOLD_COLS + ["no_tema"]

# ── Unificação conceitual (canônico -> candidatos nativos, em ordem de pref.) ──
# Usado no bronze para mapear colunas com nomes diferentes por fonte.
GRUPOS = {
    "numero": ["numero", "Numero", "Número", "numero_certame", "IdEdital",
               "issuancenumber", "Identificação da licitação"],
    "numero_controle_pncp": ["numero_controle_pncp"],
    "processo": ["nr_processo", "numero_processo", "processo", "processnumber"],
    "tipo": ["tipo", "NomeTipo", "nometipo", "modalidade", "Modalidade",
             "categoria", "biddingtypename", "combinedbiddingtype"],
    "modalidade_cod": ["modalidade_cod", "IdTipo", "idtipo"],
    "tipo_documento": ["tipo_documento"],
    "categoria": ["categoria"],
    "titulo": ["titulo", "titulo_cabecalho", "Titulo_Cabecalho", "Projeto", "projeto"],
    "objeto": ["objeto", "Objeto", "conteudo", "Descrição", "descricao_item",
               "texto_completo", "Texto", "description", "descricao", "Projeto", "projeto"],
    "observacao": ["observacao", "Complemento", "complemento", "atividade", "Ed", "ed"],
    "situacao": ["situacao", "Situação da licitação - PTP", "fase", "fase_atual",
                 "status", "Status", "biddingtype", "resultado", "Resultado"],
    "tem_resultado": ["tem_resultado"],
    "data": ["data", "Data", "data_publicacao"],
    "data_abertura": ["DataAbertura", "dataabertura", "Data de abertura da licitação",
                      "abertura", "startdate"],
    "data_publicacao": ["data_publicacao", "Data de publicação do edital",
                        "data_publicacao_dou", "publicada_em", "publishdate"],
    "data_sessao": ["data_sessao", "inicio_sessao", "Inicio_Sessao",
                    "previsao_inicio_lances", "realizacao"],
    "data_encerramento_proposta": ["data_encerramento_proposta", "dataEncerramentoProposta"],
    "data_inicio_vigencia": ["data_inicio_vigencia", "dt_inicio_vigencia", "DataInicio",
                             "datainicio", "inicio"],
    "data_fim_vigencia": ["data_fim_vigencia", "dt_final_vigencia", "DataFim",
                          "datafim", "fim"],
    "ano": ["ano", "Ano"],
    "valor": ["valor", "Valor"],
    "valor_estimado": ["valor_estimado_txt", "Valor estimado da licitação"],
    "valor_contratado": ["Valor contratado"],
    "valor_homologado": ["valor_homologado", "valor_homologado_vencedor", "valor_licitacao"],
    "contratado": ["vencedor", "fornecedor", "Razão social do fornecedor",
                   "nomeRazaoSocialFornecedor", "contratado", "contratada"],
    "contratado_cnpj": ["contratado_cnpj", "vencedor_cnpj", "ni_fornecedor",
                        "CNPJ ou CPF do fornecedor"],
    "orgao": ["orgao", "orgao_nome", "NomeDepartamento", "nomedepartamento", "tradeoffice"],
    "orgao_cnpj": ["orgao_cnpj"],
    "unidade": ["unidade", "unidade_nome"],
    "comprador": ["comprador"],
    "banco": ["banco"],
    "responsavel": ["Responsavel", "responsavel"],
    "uf": ["uf", "UF de abertura da licitação"],
    "municipio": ["municipio", "Município de abertura"],
    "local_abertura": ["Local de Abertura da Licitação", "pais"],
    "link": ["link", "Link", "link_detalhe", "Link_PDF", "link_pdf"],
    "site_projeto": ["site_projeto", "pagina_b3"],
    "documentos": ["documentos", "anexos", "pdfs", "Arquivos"],
    "arquivos_baixados": ["arquivos_baixados", "editais_baixados", "edital_arquivo_local",
                          "pdf_arquivo", "editais_pdf_baixados"],
    "edital_url": ["edital_url"],
    "edital_nome": ["edital_nome"],
    "criterio_julgamento": ["criterio_julgamento", "Criterio_Julgamento",
                            "Critério de Julgamento", "judgmenttype"],
    "modo_disputa": ["modo_disputa"],
    "tipo_julgamento": ["tipo_julgamento"],
}
PASSTHROUGH = ["fonte", "filtros_usados", "data_coleta", "fonte_rotulo"]

# ── ID estável por fonte (dedup "só novos") ───────────────────────────────────
# Mesmo que outros campos mudem entre coletas, o ID não muda -> não duplica.
# Fontes sem entrada aqui usam hash do conteúdo (fallback).
SOURCE_STABLE_ID = {
    "PNCP": ["numero_controle_pncp"],
    "BRB - Banco de Brasília": ["id"],
    "Banrisul": ["issuancenumber"],
    "BCB - Banco Central do Brasil": ["IdEdital", "idedital"],
    "B3": ["Projeto", "Data"],
    "Banestes - Editais e Avisos": ["id"],   # já é hash de conteúdo estável (fix aplicado)
    "ComprasNet": ["id"],
    "Banrisul - TCE-RS (LicitaCon dados abertos)": ["id"],
    "BDMG - Banco de Desenvolvimento de Minas Gerais": ["id"],
    "Banco da Amazônia - Licitações": ["id"],
    "CAIXA SICVE - Sistema de Compras Eletronicas": ["id"],
    # título do registro ("Pregão Eletrônico 028/2026"): único no portal
    "BNDES - Banco Nacional de Desenvolvimento Econômico e Social": ["id"],
    # fontes gerais (não bancárias) — ver pncp_abertas.py, querido_diario.py,
    # bndes_inovacao.py
    "Querido Diário - Diários oficiais municipais": ["id"],
    "BNDES - Chamadas públicas de inovação (CPSI)": ["id"],
    # fora do PNCP (sebrae.py, petronect.py, banco_mundial.py, pnud.py, ungm.py)
    "Sebrae - Canal do Fornecedor": ["id"],
    "Petronect - Petrobras e subsidiárias": ["id"],
    "Banco Mundial - Avisos de aquisição": ["id"],
    "PNUD - Avisos de aquisição": ["id"],
    "UNGM - Agências da ONU": ["id"],
    # portais de CPSI e desafios de inovação (cpsi_portais.py)
    "GovTech Place - Desafios de órgãos públicos": ["id"],
    "Enap - Desafios (gov.br)": ["id"],
    "CNJ - Contratação Pública de Solução Inovadora": ["id"],
    "Goiás GovTech - Editais CPSI": ["id"],
    "Copasa HUB - Editais CPSI": ["id"],
    "Desafio Rio - Prefeitura do Rio": ["id"],
    "CatalisaGov (Sebrae) - Consultas públicas": ["id"],
}

# ══════════════════════════════════════════════════════════════════════════════
# FILTROS DE PALAVRAS EXCLUÍDAS (objeto; anexo opcional)
# ══════════════════════════════════════════════════════════════════════════════
# Revista em 19/10/2026. A lista herdada da origem (consultoria financeira, ~240
# termos) eliminava 110 das 183 licitações no tema, inclusive "videomonitoramento
# com inteligência artificial" (por "Placas"), "manutenção preditiva" (por
# "Engenharia"/"Obra") e "integração de dados" (por "Licenciamento"). Agora o
# filtro de TEMA vem antes (gold.filtrar_tema) e esta lista só tira o que nunca é
# serviço de ML, IA, dados ou TI: obra e manutenção predial, mão de obra
# terceirizada, bens de consumo e mobiliário, alimentação, saúde clínica,
# veículos e combustível, viagens, eventos, ativos e seguros. Termos ambíguos
# (Licença, Segurança, Engenharia, Produto, Evento, Placa, Transporte...) saíram.
# E nunca elimina objeto com termo forte do núcleo (TEMAS): ver gold._objeto_sujo.
PALAVRAS_EXCLUIR = [
    # obra e manutenção predial
    "Execução de Obra", "Execução de Obras", "Mão de Obra", "Reforma", "Reformas", "Pavimentação", "Recapeamento", "Pintura",
    "Alvenaria", "Climatização", "Ar Condicionado", "Ar-condicionado", "Elevador",
    "Elevadores", "Iluminação Pública", "Fotovoltaico", "Fotovoltaica",
    # mão de obra terceirizada e serviços gerais
    "Limpeza", "Conservação Predial", "Jardinagem", "Roçada", "Poda", "Podas",
    "Dedetização", "Desratização", "Vigilância Armada", "Vigilância Desarmada",
    "Vigilância Patrimonial", "Serviço de Portaria", "Recepcionista", "Recepcionistas",
    "Copeiragem", "Lavanderia", "Lavagem",
    # bens de consumo, mobiliário e material
    "Mobiliário", "Mobiliários", "Cadeira", "Cadeiras", "Armário", "Armários",
    "Gaveteiro", "Gaveteiros", "Biombo", "Biombos", "Estofado", "Estofados",
    "Material de Expediente", "Material de Limpeza", "Material Escolar", "Uniforme",
    "Uniformes", "Fardamento", "Brinde", "Brindes", "Bebedouro", "Bebedouros",
    "Pneu", "Pneus", "Bobina", "Bobinas",
    # hardware (o tema inclui software e serviço de TI, não compra de máquina)
    "Microcomputador", "Microcomputadores", "Computadores", "Notebook", "Notebooks",
    "Desktop", "Desktops", "Impressora", "Impressoras", "Toner", "Toners", "Cartucho",
    "Cartuchos", "Nobreak", "Nobreaks", "Monitores", "Periféricos",
    # alimentação
    "Gêneros Alimentícios", "Alimentício", "Alimentícios", "Merenda", "Café",
    "Refeição", "Refeições", "Bufê", "Buffet", "Coffee Break",
    # saúde clínica e farmácia
    "Medicamento", "Medicamentos", "Odontológico", "Odontológicos", "Odontológica",
    "Vacinação", "Exames Laboratoriais", "Órtese", "Prótese",
    # veículos, combustível e viagens
    "Combustível", "Combustíveis", "Locação de Veículos", "Aquisição de Veículos",
    "Passagem Aérea", "Passagens Aéreas", "Agenciamento de Viagens",
    # ativos, seguros e publicidade
    "Leilão", "Alienação", "Arrendamento", "Desestatização", "Concessão de Uso",
    "Lotérica", "Lotéricas", "Apólice", "Patrocínio", "Publicidade", "Clipping",
]

# Filtro pelo TEXTO DOS ANEXOS (PDF inteiro do edital). Desligado: com o texto
# completo do edital, termos genéricos da lista ("Segurança", "Participação",
# "Acesso", "Licença"...) aparecem em praticamente todo edital, e o filtro
# removeria quase tudo que tem anexo. Desligado, os anexos nem são baixados.
FILTRAR_ANEXO = os.getenv("LICITACOES_FILTRAR_ANEXO", "0") == "1"

# ══════════════════════════════════════════════════════════════════════════════
# SCORE — 3 filtros (keyword + zero-shot + LLM) -> gold_filtros
# ══════════════════════════════════════════════════════════════════════════════
# Temas (keyword + zero-shot). Keywords em texto normalizado (sem acento,
# minúsculo) e casadas por SUBSTRING — por isso nada de siglas curtas ("ia",
# "rag", "ml", "pcp", "ocr"), que casariam dentro de outras palavras.
#
# Vocabulário: além dos nomes "de mercado" (ML Engineering, AI Engineering, Data
# Science, Forecasting, Research Operations), os editais e vagas brasileiros usam
# equivalentes que precisam estar aqui — Research Operations quase nunca aparece
# com esse nome: no Brasil é "pesquisa operacional", "otimização", "ciência de
# dados industrial" (Industrial Data Scientist), "supply chain"/"cadeia de
# suprimentos", "planejamento e controle da produção", "S&OP", "roteirização".
# Data analytics/BI fica FORA de propósito (dashboards, painéis, Power BI, data
# warehouse, relatórios) — não é serviço-alvo.
TEMAS = {
    "ml_engineering": {
        "label": "desenvolvimento, implantação e operação de modelos de aprendizado de máquina (MLOps)",
        "kw": ["aprendizado de maquina", "machine learning", "mlops", "modelos de aprendizado",
               "treinamento de modelos", "implantacao de modelos", "esteira de modelos",
               "ciclo de vida de modelos", "monitoramento de modelos", "deep learning",
               "aprendizado profundo", "redes neurais", "engenharia de machine learning",
               "engenharia de aprendizado de maquina", "modelos de ml"],
    },
    "ai_engineering": {
        "label": "desenvolvimento de soluções de inteligência artificial, IA generativa ou modelos de linguagem",
        "kw": ["inteligencia artificial", "ia generativa", "llm", "modelos de linguagem",
               "modelo de linguagem", "agentes de ia", "agente de ia", "agentes inteligentes",
               "assistente virtual inteligente", "processamento de linguagem natural", "nlp",
               "visao computacional", "reconhecimento de imagens", "busca semantica",
               "geracao aumentada por recuperacao", "retrieval augmented", "ai engineering",
               "solucao de ia", "solucoes de ia", "chatbot com ia"],
    },
    "data_science": {
        "label": "ciência de dados e modelagem estatística preditiva",
        "kw": ["ciencia de dados", "cientista de dados", "cientistas de dados", "data science",
               "mineracao de dados", "modelagem estatistica", "modelos estatisticos",
               "modelos preditivos", "modelo preditivo", "analise preditiva",
               "modelagem preditiva", "deteccao de fraude", "deteccao de anomalias",
               "modelos de propensao", "estatistica aplicada", "econometria"],
    },
    "forecasting": {
        "label": "previsão de demanda ou de séries temporais",
        "kw": ["previsao de demanda", "projecao de demanda", "planejamento de demanda",
               "series temporais", "serie temporal", "modelos de previsao", "modelo de previsao",
               "previsao de carga", "previsao de consumo", "previsao de vazao",
               "previsao de vazoes", "previsao de geracao", "previsao hidrologica",
               "previsao de arrecadacao", "forecast"],
    },
    "data_engineering": {
        "label": "engenharia de dados, gestão de dados mestres (MDM) ou governança de dados",
        "kw": ["engenharia de dados", "pipeline de dados", "pipelines de dados",
               "integracao de dados", "plataforma de dados", "arquitetura de dados",
               "data lake", "lakehouse", "data warehouse", "armazem de dados",
               "dados mestres", "gestao de dados mestres", "master data", "mdm",
               "governanca de dados", "qualidade de dados", "catalogo de dados",
               "gestao de metadados", "linhagem de dados", "gestao de dados",
               "interoperabilidade de dados", "data mesh", "data engineering"],
    },
    "research_operations": {
        "label": "pesquisa operacional, otimização matemática ou ciência de dados industrial",
        "kw": ["pesquisa operacional", "research operations", "operations research",
               "otimizacao matematica", "otimizacao combinatoria", "programacao linear",
               "programacao inteira", "programacao matematica", "modelagem matematica",
               "simulacao de eventos discretos", "roteirizacao", "otimizacao de rotas",
               "planejamento e controle da producao", "programacao da producao",
               "sequenciamento da producao", "s&op", "supply chain", "cadeia de suprimentos",
               "otimizacao de estoques", "dimensionamento de estoques", "otimizacao logistica",
               "manutencao preditiva", "gemeo digital", "digital twin", "industria 4.0",
               "ciencia de dados industrial", "industrial data science",
               "controle estatistico de processo"],
    },
}

# Zero-shot (mDeBERTa). Na origem rodava num worker com GPU (RTX 5090); aqui roda
# em CPU — o volume diário do gold é pequeno (dezenas de objetos curtos), e o
# modelo base (~280M parâmetros) cabe folgado em 16 GB de RAM.
ZS_MODEL = "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
ZS_THRESHOLD = 0.62
ZS_HYPOTHESIS = "Este edital de licitação trata de {}."
ZS_BATCH = 8
# Corte do texto no zero-shot. Em CPU o custo cresce com o tamanho: com 1500
# caracteres, 255 objetos x 11 rótulos levaram >20 min (15/10/2026). 700
# caracteres cobrem o objeto; o resto (informação complementar, trecho de
# diário) fica para o LLM.
ZS_MAX_CHARS = 700
# Desliga o zero-shot (ex.: máquina sem RAM livre). O score renormaliza sobre
# keyword + LLM — ver score.combinar_sinais.
ZS_ATIVO = os.getenv("LICITACOES_ZEROSHOT", "1") == "1"
# Labels ADICIONAIS: sub-temas que entram no s_zs (máximo) sem virar coluna.
ZS_LABELS_EXTRA = [
    "visão computacional",
    "processamento de linguagem natural",
    "manutenção preditiva",
    "otimização da cadeia de suprimentos e de estoques",
    "detecção de fraudes com aprendizado de máquina",
]

# LLM (gpt-oss-120b via roteador de chaves: Groq + Cerebras) — áreas A–E do prestador
LLM_MODEL = os.getenv("LICITACOES_LLM_MODEL", "openai/gpt-oss-120b")
LLM_BATCH = 15
LLM_MAX_DESC = 320
AREA_COL = {
    "A": "llm_ml_engineering",
    "B": "llm_ai_engineering",
    "C": "llm_data_science",
    "D": "llm_forecasting",
    "E": "llm_research_operations",
    "F": "llm_data_engineering",
}
LLM_SYS = (
    "Você avalia licitações públicas brasileiras para um PRESTADOR DE SERVIÇOS TÉCNICOS "
    "especializado em: (A) Machine Learning Engineering e MLOps — desenvolver, treinar, "
    "implantar, monitorar e manter modelos de aprendizado de máquina em produção; "
    "(B) AI Engineering — soluções sob medida com inteligência artificial: IA generativa, "
    "LLMs, agentes de IA, RAG/busca semântica, processamento de linguagem natural, visão "
    "computacional, assistentes virtuais inteligentes; (C) Data Science — modelagem "
    "estatística e preditiva, mineração de dados, modelos de classificação/propensão/risco, "
    "detecção de fraudes e anomalias; (D) Forecasting — previsão de demanda, séries "
    "temporais, previsão de carga, consumo, vazões, geração de energia, arrecadação; "
    "(F) Data Engineering, "
    "MDM e Governança de dados — pipelines e integração de dados, plataformas de dados, data "
    "lake/lakehouse/data warehouse (construção), gestão de dados mestres (MDM), qualidade, "
    "catálogo, metadados e governança de dados. "
    "Para CADA licitação, dê uma NOTA de 0 a 100 de quanto o OBJETO é a CONTRATAÇÃO DE "
    "SERVIÇOS nessas áreas que esse prestador executaria como CONTRATADO (100 = claramente "
    "é; 0 = claramente não é; use a faixa toda para o meio-termo). Seja rigoroso. "
    "Notas ALTAS: desenvolvimento, implantação ou sustentação de modelos de ML ou de "
    "soluções de IA; serviços de ciência de dados; modelos ou sistemas de previsão; "
    "engenharia de dados, MDM ou governança de dados; "
    "MLOps; solução de IA com "
    "serviços técnicos especializados de desenvolvimento e manutenção. "
    "Notas MÉDIAS (30-60): assinatura/aquisição de plataforma de IA ou ML que inclua "
    "implantação, customização, integração ou treinamento de modelos; fábrica de software "
    "com escopo explícito de IA ou ciência de dados; P&D com modelagem quantitativa central; "
    "contratação pública de solução inovadora (CPSI) de base tecnológica; "
    "desenvolvimento de software sob medida. "
    "Notas BAIXAS: data analytics e BI (dashboards, painéis, relatórios gerenciais, Power BI, "
    "visualização de dados — sem construção de pipelines ou plataforma de dados); sites, "
    "portais e aplicativos comuns sem IA; "
    "sustentação/suporte de sistemas prontos; infraestrutura de TI, nuvem, hardware, licenças de software "
    "prontas; terceirização de mão de obra ou postos de trabalho; cursos, capacitação e "
    "eventos (mesmo sobre IA); consultoria de gestão, financeira, jurídica ou contábil; "
    "avaliação de pessoas, RH, assessment de perfil, pesquisa de opinião e auditoria, "
    "mesmo que mencionem dados; "
    "ASSINATURA OU LICENÇA DE SOFTWARE/SaaS PRONTO (inclusive de IA ou governança de "
    "dados) sem desenvolvimento sob medida tem relevância no máximo 30; "
    "MANUTENÇÃO de equipamentos, data center ou infraestrutura física (mesmo chamada de "
    "preditiva) NÃO é ML; "
    "TERCEIRIZAÇÃO DE OPERAÇÃO (atendimento, call center, central de relacionamento, "
    "digitação, postos de trabalho), mesmo com recursos de IA, tem relevância no máximo 40; "
    "obras, materiais, equipamentos, limpeza, vigilância, transporte; leilões, concessões "
    "e alienação de ativos. "
    "SEGUNDA NOTA — VIABILIDADE 0-100: independente do tema, quanto um TIME PEQUENO (1 a 3 "
    "pessoas, consultoria enxuta, trabalho remoto, sem fábrica de software nem equipe de "
    "campo) consegue disputar e ENTREGAR este objeto. Use o objeto, o valor estimado e a "
    "modalidade. ZERO A 20: objeto que NÃO é serviço técnico de dados, IA ou software "
    "(compra de material, equipamento, hardware ou licenças; obras; infraestrutura, "
    "conectividade ou data center; suporte on-site ou 24x7; recadastramento ou trabalho "
    "de campo; terceirização de postos, atendimento ou call center; cursos e treinamentos; "
    "adesão a programas, associações, certificações ou inscrições; assinatura de SaaS "
    "pronto). "
    "ALTA (70-100): projeto ou serviço técnico de escopo delimitado (modelo, "
    "estudo, prova de conceito, consultoria, implantação pontual), valor até ~R$ 500 mil, "
    "entrega remota. MÉDIA (40-69): escopo maior porém modular, valor até ~R$ 2 milhões, ou "
    "viável com um parceiro. BAIXA (0-39): sistema grande ou fábrica de software, "
    "sustentação 24x7, alocação de muitos profissionais ou postos de trabalho, implantação "
    "presencial em muitas unidades, fornecimento de hardware/licenças em volume, valor acima "
    "de ~R$ 5 milhões, ou objeto que não é serviço técnico. Sem valor informado, julgue pelo "
    "escopo. Responda SOMENTE JSON: {\"resultados\":[{\"id\":N,\"score\":0-100,"
    "\"areas\":[\"A\"...],\"motivo\":\"≤12 palavras\",\"viabilidade\":0-100,"
    "\"motivo_viabilidade\":\"≤12 palavras\"}]} — um objeto por licitação, mesma ordem/id; "
    "os motivos SEMPRE em português do Brasil; "
    "\"areas\" lista as letras (A, B, C, D, F) que se aplicam."
)

# Pesos do score combinado: LLM domina (70%), keyword e zero-shot 15% cada.
W_KW, W_ZS, W_LLM = 0.15, 0.15, 0.7
# Com nota do LLM, ele domina: keyword e zero-shot dão ~1,0 para qualquer texto
# com "IA"/"dados" (inclusive compra de workstation ou manutenção de data center)
# e, com 30% somados, levantavam acima do corte itens que o LLM rejeitou
# (calibração de 15/10/2026, amostra de 10 por faixa de score). Os pesos acima
# valem quando o LLM não avaliou (texto curto ou falha).
W_KW_COM_LLM, W_ZS_COM_LLM, W_LLM_COM_LLM = 0.05, 0.05, 0.9

# SCORE DE VIABILIDADE (time pequeno, 1-3 pessoas) — mesmo desenho do score de
# relevância: média ponderada de sinais, renormalizada sobre os disponíveis.
#   s_viab_llm   : nota 0-100 do LLM ÷ 100 (régua em LLM_SYS);
#   s_viab_valor : faixa do valor estimado (VIAB_FAIXAS_VALOR); ausente sem valor.
W_VIAB_LLM, W_VIAB_VALOR = 0.6, 0.4   # valor pesava 0,25; o LLM é leniente com porte
# O valor só DERRUBA a viabilidade (projeto grande), nunca a sobe: na calibração
# de 15/10, valor baixo levava suporte de TI contínuo de 30 para 58 e um curso de
# R$ 37 mil de 20 para 52. score_viabilidade = min(LLM, mistura com o valor).
VIAB_TETO_NAO_TECNICO = 0.2   # até aqui = "não é serviço técnico": vale o LLM
# (teto do valor em R$, nota) — valor até o teto recebe a nota; acima do último, 0.05.
VIAB_FAIXAS_VALOR = [(300_000, 1.0), (1_000_000, 0.75), (3_000_000, 0.45),
                     (10_000_000, 0.2)]
SCORE_THRESHOLD = 0.5
# Corte de exibição sugerido para o home (lista "relevantes").
SCORE_EXIBICAO_MIN = 0.35

# Colunas da tabela gold_filtros.
GOLD_FILTROS_COLS = GOLD_COLS + [
    f"tag_kw_{t}" for t in TEMAS
] + [
    f"tag_kw_{t}_termos" for t in TEMAS
] + [
    f"tag_zs_{t}" for t in TEMAS
] + [
    "tags", "tag_relevante", "tag_metodo",
    "llm_relevante", "llm_areas", "llm_motivo", "llm_modelo",
] + list(AREA_COL.values()) + [
    "s_kw", "s_zs", "s_llm", "score_combinado", "n_sinais_usados",
    "usou_llm", "relevante_combinado",
    "llm_viabilidade", "llm_viabilidade_motivo", "s_viab_llm", "s_viab_valor",
    "score_viabilidade",
    "run_date",
]

# ── Recência ──────────────────────────────────────────────────────────────────
# Corte FINO (no gold): fallback quando não há prazo de proposta.
# 2 = hoje + ontem. Fontes sem data de publicação usam run_date. Ver DECISOES.md §3/§6.
RECENCIA_DIAS = 2

# Corte GROSSO (no bronze): poda backlog antigo por data de publicação, mas
# generoso o bastante para não perder janelas de proposta longas.
# Coleta só olha no máximo RETRO_DIAS para trás (decisão de 15/10/2026: depois
# da primeira carga, a coleta é diária e incremental; não há carga retroativa).
RETRO_DIAS = 7
JANELA_BRONZE_DIAS = RETRO_DIAS

# ── Busca do PNCP por TERMO (além dos órgãos-alvo por CNPJ) ──────────────────
# Na origem o PNCP era consultado só pelos CNPJs dos bancos. Serviços de ML/IA
# são contratados por qualquer órgão (DETRAN, tribunais, SESC, prefeituras), então
# o coletor também busca editais ABERTOS (recebendo proposta) por estes termos.
BUSCA_TERMOS_PNCP = [
    "inteligência artificial", "IA generativa", "aprendizado de máquina",
    "solução inovadora", "engenharia de dados", "fábrica de software",
    "machine learning", "MLOps", "ciência de dados", "cientista de dados",
    "modelos preditivos", "análise preditiva", "previsão de demanda",
    "séries temporais", "pesquisa operacional", "otimização matemática",
    "roteirização", "visão computacional", "processamento de linguagem natural",
    "modelos de linguagem", "manutenção preditiva", "gêmeo digital",
]
MAX_PAGINAS_TERMO = 4          # 50 itens/página, ordenado por data desc


# ── Verticais (classificação do objeto por LLM) ───────────────────────────────
# Cota: o TPD da ORGANIZAÇÃO Groq (tokens/dia) é compartilhado por todas as
# chaves da conta e com o score da mesma run — daí o teto por execução.
VERTICAIS_LIMITE = 300      # máx. de licitações classificadas por execução
VERTICAIS_LOTE = 10         # objetos por chamada; o prompt de sistema domina o
                            # custo quando é 1 por chamada
VERTICAIS_PARALELISMO = 4   # threads por chave (o roteador troca/bloqueia chaves)

# Taxonomia. `nucleo` = as 6 áreas de serviço do prestador (mesmas A–F do score).
# `cor` fica para o home exibir.
VERTICAIS: list[dict] = [
    {"id": "ml_engineering",      "nome": "Machine Learning Engineering e MLOps",
     "nucleo": True,  "cor": "#2f6f8f",
     "desc": "Desenvolver, treinar, implantar, monitorar e sustentar modelos de "
             "aprendizado de máquina em produção; esteiras de modelos, MLOps."},
    {"id": "ai_engineering",      "nome": "AI Engineering e IA generativa",
     "nucleo": True,  "cor": "#4a5fa8",
     "desc": "Soluções sob medida com IA: LLMs, agentes, RAG/busca semântica, "
             "PLN, visão computacional, assistentes virtuais inteligentes."},
    {"id": "data_science",        "nome": "Data Science e modelagem preditiva",
     "nucleo": True,  "cor": "#3d7a5a",
     "desc": "Modelagem estatística e preditiva, mineração de dados, modelos de "
             "propensão/risco/classificação, detecção de fraudes e anomalias."},
    {"id": "forecasting",         "nome": "Forecasting e séries temporais",
     "nucleo": True,  "cor": "#7a6a2f",
     "desc": "Previsão de demanda, carga, consumo, vazões, geração de energia, "
             "arrecadação; modelos de séries temporais e projeções."},
    {"id": "data_engineering",    "nome": "Data Engineering, MDM e governança de dados",
     "nucleo": True,  "cor": "#5a4a8a",
     "desc": "Pipelines e integração de dados, plataformas de dados, data lake/"
             "lakehouse/data warehouse (construção), MDM, qualidade, catálogo e governança."},
    {"id": "research_operations", "nome": "Pesquisa operacional e ciência de dados industrial",
     "nucleo": True,  "cor": "#8a4f3a",
     "desc": "Otimização matemática, simulação, roteirização, planejamento da "
             "produção, S&OP, estoques e cadeia de suprimentos, manutenção preditiva."},

    {"id": "dados_bi",            "nome": "Data analytics e BI",
     "nucleo": False, "cor": "#57534e",
     "desc": "Dashboards, painéis, Power BI, relatórios gerenciais, visualização — sem "
             "construção de pipelines ou plataforma de dados."},
    {"id": "plataformas_licencas", "nome": "Licenças, SaaS e plataformas prontas",
     "nucleo": False, "cor": "#6b625c",
     "desc": "Assinatura ou licença de software/plataforma (inclusive de IA) "
             "sem serviço relevante de desenvolvimento ou modelagem."},
    {"id": "ti_sistemas",         "nome": "Desenvolvimento de sistemas e TI",
     "nucleo": False, "cor": "#78716c",
     "desc": "Sistemas, sites, aplicativos, fábrica de software, infraestrutura, "
             "nuvem, sustentação, telecom — sem IA/ML como atividade-fim."},
    {"id": "consultoria_gestao",  "nome": "Consultoria de gestão, financeira e jurídica",
     "nucleo": False, "cor": "#8a8279",
     "desc": "Consultoria estratégica, de processos, financeira, contábil, "
             "auditoria, jurídica, pesquisa de mercado."},
    {"id": "capacitacao_eventos", "nome": "Capacitação, cursos e eventos",
     "nucleo": False, "cor": "#9c9289",
     "desc": "Cursos, treinamentos de pessoas, palestras, eventos — mesmo sobre IA."},
    {"id": "administrativo",      "nome": "Apoio administrativo e terceirização",
     "nucleo": False, "cor": "#a8a29e",
     "desc": "Mão de obra terceirizada, limpeza, vigilância, recepção, arquivo, "
             "transporte."},
    {"id": "engenharia_bens",     "nome": "Obras, facilities, bens e equipamentos",
     "nucleo": False, "cor": "#b5aca2",
     "desc": "Obra, reforma, manutenção predial, instalações; aquisição de "
             "material, mobiliário, equipamentos, veículos."},
    {"id": "ativos_concessoes",   "nome": "Leilão, concessão e alienação de ativos",
     "nucleo": False, "cor": "#bdb5ab",
     "desc": "Leilão, concessão, PPP, desestatização, arrendamento — o vencedor "
             "OPERA o ativo, não presta serviço técnico."},
    {"id": "outros",              "nome": "Outros",
     "nucleo": False, "cor": "#c4bdb5",
     "desc": "Não se encaixa em nenhuma das anteriores, ou objeto vago demais "
             "para classificar (ex.: 'prorrogação de prazo contratual')."},
]

VERTICAL_POR_ID = {v["id"]: v for v in VERTICAIS}
VERTICAIS_NUCLEO = [v["id"] for v in VERTICAIS if v["nucleo"]]


def vertical_nome(vid: str) -> str:
    return VERTICAL_POR_ID.get(vid, VERTICAL_POR_ID["outros"])["nome"]


# ══════════════════════════════════════════════════════════════════════════════
# FONTES GERAIS (não bancárias)
# ══════════════════════════════════════════════════════════════════════════════
# Termos de TI que, junto com as keywords dos TEMAS, decidem o que entra das
# fontes amplas (varredura do PNCP e diários oficiais). Casados por substring no
# texto normalizado (sem acento, minúsculo). O LLM separa depois o que é serviço
# de ML/dados do que é TI comum.
TERMOS_TI = [
    "tecnologia da informacao", "solucao de tecnologia", "solucao tecnologica",
    "desenvolvimento de software", "desenvolvimento de sistema", "fabrica de software",
    "software sob demanda", "sistema de informacao", "sistemas de informacao",
    "plataforma digital", "plataforma tecnologica", "transformacao digital",
    "engenharia de dados", "integracao de dados", "governanca de dados", "gestao de dados",
    "plataforma de dados", "lago de dados", "data lake", "banco de dados", "big data",
    "analise de dados", "computacao em nuvem", "automacao de processos", "rpa",
    "chatbot", "assistente virtual", "atendimento virtual", "algoritmo",
    "solucao inovadora", "solucoes inovadoras", "lei complementar 182", "cpsi",
    "internet das coisas", "iot", "sensoriamento remoto", "geoprocessamento",
    # analytics e BI (entram no núcleo do funil desde 19/10/2026)
    "business intelligence", "power bi", "analytics", "dashboard", "dashboards",
    "painel de indicadores", "paineis de indicadores", "paineis gerenciais",
    "visualizacao de dados", "inteligencia de dados", "inteligencia analitica",
]

# PNCP — todas as contratações de qualquer órgão (federal, estadual, municipal),
# filtradas localmente por TEMAS + TERMOS_TI. Dois modos (pncp_abertas.py):
#   - diário: publicadas nos últimos PNCP_PUBLICACAO_DIAS dias
#     (/contratacoes/publicacao; ~6,4 mil/dia nas modalidades abaixo, ~130 páginas);
#   - completo: TODAS com proposta aberta (/contratacoes/proposta; ~28 mil itens,
#     ~80 min). NÃO roda sozinho (a coleta não volta além de RETRO_DIAS); fica
#     disponível em collect_pncp_abertas(completo=True) para uma carga manual.
PNCP_PUBLICACAO_DIAS = 2           # hoje + ontem (a dedup do bronze cobre sobreposição)
# O endpoint de publicação exige modalidade. Ficam de fora as sem disputa ou sem
# serviço: 1 e 13 (leilão) e 9 (inexigibilidade — contratação direta já decidida).
# 2 diálogo competitivo, 3 concurso, 4/5 concorrência, 6/7 pregão, 8 dispensa,
# 10 manifestação de interesse, 11 pré-qualificação, 12 credenciamento.
PNCP_MODALIDADES = [2, 3, 4, 5, 6, 7, 8, 10, 11, 12]
PNCP_PRIMEIRA_COLETA_DIAS = 7      # primeira execução: publicadas nos últimos 7 dias
PNCP_ABERTAS_DIAS_A_FRENTE = 365   # modo completo: dataFinal = hoje + N
PNCP_ABERTAS_MAX_PAGINAS = 800     # teto de segurança (50 itens/página)
# Limite medido em 15/10/2026: rajadas de 5-15 requisições dão 429 (HTML, sem
# Retry-After) por ~20 s; 1 requisição a cada ~3 s passa sem bloqueio. Com isso o
# modo diário leva ~14 min (2 dias x ~130 páginas). Os coletores do PNCP têm a tag
# "pncp" e não rodam ao mesmo tempo (assets.py).
PNCP_ABERTAS_PAUSA_S = 3.0
PNCP_ESPERA_429_S = 30.0

# Querido Diário (Open Knowledge Brasil) — busca textual em diários oficiais
# municipais. Pega municípios que ainda não publicam no PNCP e sinais antes do
# edital (portaria de equipe de planejamento, ETP). Ruidoso: só ficam trechos com
# marcador de contratação E termo do tema no mesmo trecho (querido_diario.py).
QUERIDO_DIARIO_API = "https://api.queridodiario.org.br/gazettes"
QUERIDO_DIARIO_DIAS = 7            # janela de publicação consultada por execução
QUERIDO_DIARIO_TERMOS = [
    "inteligência artificial", "aprendizado de máquina", "machine learning",
    "ciência de dados", "cientista de dados", "análise preditiva", "modelos preditivos",
    # sem "roteirização": nos diários aparece mais em coleta de lixo e audiovisual
    "previsão de demanda", "pesquisa operacional", "otimização de rotas", "visão computacional",
    "processamento de linguagem natural", "manutenção preditiva", "gêmeo digital",
    "engenharia de dados", "solução inovadora", "fábrica de software",
]

# BNDES — chamadas públicas de inovação (CPSI, LC 182/2021): página própria,
# fora do licitacoes.json que o coletor bndes.py lê.
BNDES_INOVACAO_URL = ("https://www.bndes.gov.br/wps/portal/site/home/transparencia/"
                      "licitacoes-contratos/licitacoes/chamadas-publicas-inovacao2")

# ══════════════════════════════════════════════════════════════════════════════
# FONTES FORA DO PNCP (out/2026): Sistema S, estatal 13.303 e organismos
# internacionais que aceitam empresa (e, no PNUD/ONU, pessoa física) do Brasil.
# ══════════════════════════════════════════════════════════════════════════════
# Sebrae — Canal do Fornecedor (SCF): processos de TODOS os Sebrae estaduais e do
# nacional, que seguem regulamento próprio e não publicam no PNCP. Um POST no
# formulário de filtro devolve tudo "Em Andamento" (~325 em 17/10/2026).
SEBRAE_SCF_URL = "https://scf3.sebrae.com.br/PortalCf/Licitacoes"
# Petrobras/Transpetro/PBio — Petronect: oportunidades com proposta aberta,
# inteiras num JSON (serviço OData do SAP usado pela própria página pública).
PETRONECT_LISTA_URL = ("https://www.petronect.com.br/sap/opu/odata/SAP/"
                       "YPCON_GET_XML_SRV/getXMLSet('01')?$format=json")
PETRONECT_ANEXO_URL = ("https://www.petronect.com.br/sap/opu/odata/SAP/"
                       "YPCON_PUB_ATTACHMENT_DOWNLOAD_SRV/attachmentSet('{id}')/$value")
PETRONECT_PAGINA = ("https://www.petronect.com.br/irj/go/km/docs/pccshrcontent/"
                    "Site%20Content%20(Legacy)/Portal2018/pt/lista_licitacoes_publicadas_ft.html")
# Banco Mundial — avisos de aquisição dos projetos financiados (qualquer país
# membro pode concorrer, o Brasil incluso). Busca textual por termo.
BANCO_MUNDIAL_API = "https://search.worldbank.org/api/v2/procnotices"
BANCO_MUNDIAL_TIPOS = {"Invitation for Bids", "Request for Expression of Interest",
                       "Invitation for Prequalification", "Request for Proposals"}
BANCO_MUNDIAL_TERMOS = ["machine learning", "artificial intelligence", "data science",
                        "data engineering", "predictive", "forecasting",
                        "operations research", "computer vision", "data platform",
                        "data governance", "big data", "natural language processing"]
# PNUD — todos os avisos abertos numa página. "IC - Individual contractor" é
# contratação de PESSOA FÍSICA (consultor individual), sem CNPJ.
PNUD_URL = "https://procurement-notices.undp.org/"
# UNGM — portal de compras das agências da ONU (OMS, UNICEF, OIT, FAO...). Busca
# por termo no título, só avisos ativos. O PNUD fica de fora (coletor próprio).
UNGM_URL = "https://www.ungm.org/Public/Notice"
UNGM_TERMOS = ["data", "machine learning", "artificial intelligence", "AI ",
               "forecasting", "predictive", "analytics platform", "statistical model",
               "digital", "software"]
UNGM_MAX_PAGINAS = 4                 # 15 avisos por página, mais recentes primeiro
# Consultor individual (pessoa fisica) em projetos de cooperacao internacional
# executados com ministerios (UNESCO, IICA, PNUD, OEI, OPAS, FAO): os editais
# saem nas paginas dos ministerios no gov.br, um PDF por edital. Ano corrente e
# anterior; o coletor le cada PDF para tirar objeto e prazo.
PRODOC_PAGINAS = [
    ("MDS", "https://www.gov.br/mds/pt-br/acesso-a-informacao/internacional/editais-em-selecao/{ano}"),
    ("MDH", "https://www.gov.br/mdh/pt-br/navegue-por-temas/cooperacao-internacional"),
]
PRODOC_MAX_PDFS = 60                 # por execucao, dos mais novos para os mais antigos

# Ambito de cada fonte (o resto e nacional). Nome = valor de `fonte` no bronze.
FONTES_INTERNACIONAIS = {
    "Banco Mundial - Avisos de aquisição", "PNUD - Avisos de aquisição",
    "UNGM - Agências da ONU",
    "PRODOC - Consultor individual em projetos de cooperação internacional",
}

# Termos de tema para textos em inglês, espanhol e francês (fontes internacionais).
# Mesma regra do termos.py: palavra inteira, sem acento, minúsculo. "AI" em
# maiúsculas é tratado à parte (termos.termos_exterior), porque "ai" minúsculo
# casa com o francês "j'ai".
TERMOS_EXTERIOR = [
    # inglês
    "machine learning", "deep learning", "artificial intelligence", "generative ai",
    "large language model", "large language models", "llm", "llms", "nlp",
    "natural language processing", "computer vision", "data science", "data scientist",
    "data engineering", "data engineer", "data pipeline", "data pipelines",
    "data platform", "data governance", "data management", "master data", "big data",
    "data lake", "data warehouse", "mlops", "predictive model", "predictive models",
    "predictive modelling", "predictive modeling", "predictive analytics",
    "forecasting", "forecast model", "time series", "operations research",
    "optimization model", "mathematical optimization", "statistical modelling",
    "statistical modeling", "econometric", "remote sensing", "geospatial analysis",
    "digital twin", "chatbot", "recommender system", "anomaly detection",
    # espanhol
    "aprendizaje automatico", "inteligencia artificial", "ciencia de datos",
    "cientifico de datos", "ingenieria de datos", "gobernanza de datos",
    "modelos predictivos", "modelo predictivo", "pronostico", "pronosticos",
    "investigacion de operaciones", "vision por computador", "plataforma de datos",
    # francês
    "intelligence artificielle", "apprentissage automatique", "science des donnees",
    "gouvernance des donnees", "modele predictif", "modeles predictifs",
]

# ── Detalhes (detalhes.py) ────────────────────────────────────────────────────
# 3 consultas ao PNCP por item (detalhe, itens, arquivos) com a pausa do rate
# limit: ~10 s por item. 80 itens = ~13 min. O resto vai nas próximas execuções.
DETALHES_LIMITE = 80
DETALHES_MAX_SEGUNDOS = 1200
DETALHES_VALOR_LIMITE = 25          # editais baixados por execução para achar o valor
DETALHES_MAX_PDF_MB = 25
DETALHES_PAGINAS_PDF = 40           # o valor costuma estar no começo ou no TR

# ── Portais de CPSI e desafios de inovação (cpsi_portais.py) ─────────────────
# Notícias e desafios publicados nos últimos N dias (os que ainda estão abertos
# ou sem prazo conhecido; os encerrados ficam de fora na coleta).
CPSI_PORTAIS_DIAS = 60
# Fontes que passam direto pelo filtro de tema (gold.filtrar_tema): desafio de
# inovação raramente usa os termos de tema, e o LLM decide a relevância.
FONTES_INOVACAO = {
    "GovTech Place - Desafios de órgãos públicos",
    "Enap - Desafios (gov.br)",
    "CNJ - Contratação Pública de Solução Inovadora",
    "Goiás GovTech - Editais CPSI",
    "Copasa HUB - Editais CPSI",
    "Desafio Rio - Prefeitura do Rio",
    "CatalisaGov (Sebrae) - Consultas públicas",
    "BNDES - Chamadas públicas de inovação (CPSI)",
}

# PNCP — CPSI com proposta aberta, sem janela de publicação (pncp_cpsi.py).
PNCP_CPSI_TERMOS = ["solução inovadora", "CPSI", "Lei Complementar 182",
                    "marco legal das startups", "contrato público para solução inovadora"]
