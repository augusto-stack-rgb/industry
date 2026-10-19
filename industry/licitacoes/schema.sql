-- DDL das camadas base da pipeline de licitações (idempotente).
-- gold_filtros, prazos_proposta e verticais são criadas pelo db.py (ensure_*).
-- Aplicado pelo asset licitacoes_bronze a cada execução.
CREATE SCHEMA IF NOT EXISTS licitacoes;

CREATE TABLE IF NOT EXISTS licitacoes.bronze (
    id_unificado text PRIMARY KEY,
    fonte text,
    id_nativo text,
    numero text,
    numero_controle_pncp text,
    processo text,
    tipo text,
    modalidade_cod text,
    tipo_documento text,
    categoria text,
    titulo text,
    objeto text,
    observacao text,
    situacao text,
    tem_resultado text,
    data text,
    data_abertura text,
    data_publicacao text,
    data_sessao text,
    data_encerramento_proposta text,
    data_inicio_vigencia text,
    data_fim_vigencia text,
    ano text,
    valor text,
    valor_estimado text,
    valor_contratado text,
    valor_homologado text,
    contratado text,
    contratado_cnpj text,
    orgao text,
    orgao_cnpj text,
    unidade text,
    comprador text,
    banco text,
    responsavel text,
    uf text,
    municipio text,
    local_abertura text,
    link text,
    site_projeto text,
    documentos text,
    arquivos_baixados text,
    edital_url text,
    edital_nome text,
    qtd_documentos integer,
    criterio_julgamento text,
    modo_disputa text,
    tipo_julgamento text,
    fonte_rotulo text,
    filtros_usados text,
    data_coleta text,
    dados_originais jsonb,
    row_hash text,
    run_date date
);
CREATE INDEX IF NOT EXISTS bronze_row_hash_idx ON licitacoes.bronze (row_hash);
CREATE INDEX IF NOT EXISTS bronze_fonte_nativo_idx ON licitacoes.bronze (fonte, id_nativo);

-- Marcacoes para filtrar (bronze.marcar): ambito nacional/internacional e se o
-- aviso aceita pessoa fisica (consultor individual, sem CNPJ): 'sim' ou 'nao'.
ALTER TABLE licitacoes.bronze ADD COLUMN IF NOT EXISTS ambito text;
ALTER TABLE licitacoes.bronze ADD COLUMN IF NOT EXISTS pessoa_fisica text;
UPDATE licitacoes.bronze
   SET ambito = CASE WHEN fonte IN ('Banco Mundial - Avisos de aquisição', 'PNUD - Avisos de aquisição',
                                    'UNGM - Agências da ONU',
                                    'PRODOC - Consultor individual em projetos de cooperação internacional')
                     THEN 'internacional' ELSE 'nacional' END,
       pessoa_fisica = CASE WHEN categoria ILIKE 'pessoa f%'
                              OR tipo ILIKE '%Individual Consultant Selection%' THEN 'sim' ELSE 'nao' END
 WHERE ambito IS NULL OR pessoa_fisica IS NULL;
CREATE INDEX IF NOT EXISTS bronze_marcacoes_idx ON licitacoes.bronze (ambito, pessoa_fisica);

CREATE TABLE IF NOT EXISTS licitacoes.silver (
    id_unificado text PRIMARY KEY,
    fonte text,
    tipo text,
    objeto text,
    situacao text,
    data date,
    valor numeric(18,2),
    link text,
    contratado text,
    numero text
);

CREATE TABLE IF NOT EXISTS licitacoes.gold (
    id_unificado text PRIMARY KEY,
    fonte text,
    tipo text,
    objeto text,
    situacao text,
    data date,
    valor numeric(18,2),
    link text,
    contratado text,
    numero text
);

-- Detalhes dos itens pontuados (detalhes.py): o que as listagens não trazem.
CREATE TABLE IF NOT EXISTS licitacoes.detalhes (
    id_unificado text PRIMARY KEY,
    modalidade text,                 -- normalizada (db.normalizar_modalidade)
    amparo_legal text,
    instrumento text,                -- edital, aviso de contratação direta...
    srp boolean,                     -- sistema de registro de preços
    modo_disputa text,
    criterio_julgamento text,        -- menor preço, técnica e preço...
    beneficio_me_epp text,           -- exclusiva, cota reservada, não se aplica
    orcamento_sigiloso boolean,
    valor_estimado numeric(18,2),
    valor_origem text,               -- listagem | pncp | edital
    situacao text,
    data_abertura_proposta timestamp,
    data_encerramento_proposta timestamp,
    link_sistema_origem text,        -- onde a disputa acontece
    link_edital text,
    anexos jsonb,                    -- [{titulo, tipo, url}]
    qtd_itens int,
    origem text,                     -- bronze | pncp
    atualizado_em timestamptz DEFAULT now()
);

-- Divisao da nota tecnica entre historico e proposta (nota_tecnica.py)
CREATE TABLE IF NOT EXISTS licitacoes.nota_tecnica (
    id_unificado text PRIMARY KEY,
    tem_nota_tecnica boolean,        -- false: menor preco/maior desconto; null: nao sabido
    peso_historico integer,          -- % dos pontos tecnicos em experiencia, atestados, equipe...
    peso_proposta integer,           -- % dos pontos tecnicos na proposta (solucao, metodologia...)
    metodo text,
    evidencia text,                  -- criterios e pontos lidos no edital
    atualizado_em timestamptz DEFAULT now()
);
