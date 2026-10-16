-- DDL base das pipelines de notícias (idempotente). As demais tabelas
-- (noticias_classificadas, mna_*, monitor.coleta_noticias) são criadas pelo
-- próprio código. Aplicado pelo asset noticias_coleta a cada execução.
CREATE SCHEMA IF NOT EXISTS noticias;
CREATE SCHEMA IF NOT EXISTS monitor;

CREATE TABLE IF NOT EXISTS noticias.noticias_raw (
    titulo      text,
    link        text,
    data        text,
    texto       text,
    fonte       text,
    setor       text,
    data_coleta date
);
CREATE INDEX IF NOT EXISTS noticias_raw_coleta_idx ON noticias.noticias_raw (data_coleta);
CREATE INDEX IF NOT EXISTS noticias_raw_fonte_link_idx ON noticias.noticias_raw (fonte, link);

CREATE TABLE IF NOT EXISTS noticias.noticias (
    titulo text,
    link   text,
    data   text,
    texto  text,
    setor  text,
    fonte  text
);
CREATE INDEX IF NOT EXISTS noticias_titulo_idx ON noticias.noticias (titulo);

-- Relatório por setor (industry/noticias/envio). noticias_temp é refeita a cada
-- rodada; noticias_email guarda o histórico das notas (auditoria); relatorios_email
-- guarda o HTML gerado, que o home pode exibir.
CREATE TABLE IF NOT EXISTS noticias.noticias_temp (
    titulo  text,
    link    text,
    data    timestamp,
    texto   text,
    setor   text,
    fonte   text,
    ranking bigint,
    tag     text
);

CREATE TABLE IF NOT EXISTS noticias.noticias_email (
    titulo          text,
    link            text,
    data            timestamp,
    texto           text,
    setor           text,
    fonte           text,
    ranking         bigint,
    tag             text,
    run_id          text,
    gerado_em       timestamp,
    ranking_llm     double precision,
    assunto         text,
    componente      text,
    fator_recencia  double precision,
    fator_repeticao double precision,
    bonus_cobertura double precision
);

CREATE TABLE IF NOT EXISTS noticias.relatorios_email (
    id                 serial PRIMARY KEY,
    gerado_em          timestamptz DEFAULT now(),
    setor              text,
    layout             text,
    unidade_de_tempo   text,
    data_display       text,
    quantos_dias_atras integer,
    paragrafos         jsonb,
    noticias           jsonb,
    html_content       text
);
