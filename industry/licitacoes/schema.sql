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
