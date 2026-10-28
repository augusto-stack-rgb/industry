-- Universo de compras públicas para inteligência de mercado (plano em
-- industry/licitacoes/PLANO_PRODUTO.md). Fica no Postgres LOCAL (MERCADO_PG_URL), não
-- no Supabase: o volume não cabe no plano grátis.
--
-- contratacao e contrato: API de consulta do PNCP, Brasil inteiro.
-- item e resultado: Dados Abertos do Compras.gov.br, que espelham o PNCP só para o que
-- passa pelo Compras.gov.br (~20-30% das contratações, viés federal; medido em 27/10/2026).
-- Cada tabela guarda os campos usados nas análises e a resposta inteira em `dados`.

CREATE SCHEMA IF NOT EXISTS mercado;

CREATE TABLE IF NOT EXISTS mercado.contratacao (
    numero_controle_pncp text PRIMARY KEY,   -- 00394452000103-1-000123/2026
    ano integer,
    sequencial integer,
    numero_compra text,
    processo text,
    orgao_cnpj text,
    orgao_nome text,
    esfera text,                -- F federal, E estadual, M municipal, D distrital, N não informada
    poder text,                 -- E executivo, L legislativo, J judiciário, N não informado
    unidade_codigo text,
    unidade_nome text,
    uf text,
    municipio text,
    codigo_ibge text,
    modalidade_id integer,
    modalidade_nome text,
    modo_disputa_id integer,
    modo_disputa_nome text,
    instrumento_nome text,
    amparo_legal_codigo integer,
    amparo_legal_nome text,
    srp boolean,
    objeto text,
    informacao_complementar text,
    situacao_id integer,
    situacao_nome text,
    valor_estimado numeric(18,2),
    valor_homologado numeric(18,2),
    data_publicacao timestamp,
    data_abertura_proposta timestamp,
    data_encerramento_proposta timestamp,
    data_atualizacao timestamp,
    sistema_origem text,        -- usuarioNome: Compras.gov.br, BLL, Portal de Compras Públicas...
    link_sistema_origem text,
    dados jsonb,
    coletado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contratacao_publicacao_idx ON mercado.contratacao (data_publicacao);
CREATE INDEX IF NOT EXISTS contratacao_ibge_idx ON mercado.contratacao (codigo_ibge);
CREATE INDEX IF NOT EXISTS contratacao_orgao_idx ON mercado.contratacao (orgao_cnpj);

CREATE TABLE IF NOT EXISTS mercado.contrato (
    numero_controle_pncp text PRIMARY KEY,   -- do contrato: 13880257000127-2-000239/2026
    numero_controle_compra text,             -- liga em contratacao.numero_controle_pncp
    numero_controle_ata text,
    ano integer,
    sequencial integer,
    tipo_contrato text,
    categoria_id integer,
    categoria text,             -- Serviços, Informática (TIC), Compras, Obras...
    orgao_cnpj text,
    orgao_nome text,
    esfera text,
    poder text,
    unidade_codigo text,
    unidade_nome text,
    uf text,
    municipio text,
    codigo_ibge text,
    fornecedor_ni text,         -- CNPJ ou CPF do contratado
    fornecedor_nome text,
    fornecedor_tipo_pessoa text,
    subcontratado_ni text,
    subcontratado_nome text,
    objeto text,
    valor_inicial numeric(18,2),
    valor_global numeric(18,2),
    valor_acumulado numeric(18,2),
    data_assinatura date,
    vigencia_inicio date,
    vigencia_fim date,
    data_publicacao timestamp,
    data_atualizacao timestamp,
    sistema_origem text,
    dados jsonb,
    coletado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contrato_fornecedor_idx ON mercado.contrato (fornecedor_ni);
CREATE INDEX IF NOT EXISTS contrato_compra_idx ON mercado.contrato (numero_controle_compra);
CREATE INDEX IF NOT EXISTS contrato_publicacao_idx ON mercado.contrato (data_publicacao);

CREATE TABLE IF NOT EXISTS mercado.item (
    id_compra_item text PRIMARY KEY,
    numero_controle_compra text,
    numero_item integer,
    descricao text,
    material_ou_servico text,   -- M ou S
    categoria_nome text,
    cod_item_catalogo text,     -- CATMAT/CATSER
    codigo_classe text,
    codigo_grupo text,
    codigo_pdm text,
    nome_pdm text,
    codigo_ncm text,
    unidade_medida text,
    quantidade numeric,
    valor_unitario_estimado numeric(18,4),
    valor_total numeric(18,2),
    criterio_julgamento text,
    situacao text,
    tipo_beneficio text,
    tem_resultado boolean,
    orgao_cnpj text,
    data_inclusao timestamp,
    data_atualizacao timestamp,
    dados jsonb,
    coletado_em timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS item_compra_idx ON mercado.item (numero_controle_compra);
CREATE INDEX IF NOT EXISTS item_catalogo_idx ON mercado.item (cod_item_catalogo);

CREATE TABLE IF NOT EXISTS mercado.resultado (
    id_compra_item text,
    sequencial_resultado integer,
    numero_controle_compra text,
    numero_item integer,
    fornecedor_ni text,
    fornecedor_nome text,
    tipo_pessoa text,
    porte_id integer,
    porte_nome text,            -- ME, EPP, Demais...
    natureza_juridica text,
    ordem_classificacao_srp integer,
    quantidade numeric,
    valor_unitario numeric(18,4),
    valor_total numeric(18,2),
    percentual_desconto numeric,
    situacao text,              -- Informado, Cancelado...
    beneficio_meepp boolean,
    uf text,
    orgao_cnpj text,
    data_resultado timestamp,
    data_atualizacao timestamp,
    dados jsonb,
    coletado_em timestamptz DEFAULT now(),
    PRIMARY KEY (id_compra_item, sequencial_resultado)
);
CREATE INDEX IF NOT EXISTS resultado_fornecedor_idx ON mercado.resultado (fornecedor_ni);
CREATE INDEX IF NOT EXISTS resultado_compra_idx ON mercado.resultado (numero_controle_compra);

-- Uma linha por (fonte, dia, chave) coletado: a coleta é resumível e não repete dia pronto.
CREATE TABLE IF NOT EXISTS mercado.coleta_log (
    fonte text,
    dia date,
    chave text DEFAULT '',      -- modalidade, quando a fonte é paginada por ela
    linhas integer,
    paginas integer,
    status text,                -- ok, erro
    erro text,
    terminado_em timestamptz DEFAULT now(),
    PRIMARY KEY (fonte, dia, chave)
);
