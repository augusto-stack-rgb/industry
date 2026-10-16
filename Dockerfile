# Imagem única das pipelines (webserver e daemon do Dagster usam a mesma).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home \
    HF_HOME=/opt/hf_cache

# torch só para CPU: a máquina não tem GPU CUDA (RX 580) e as rodas CUDA do PyPI
# somam vários GB. O zero-shot (mDeBERTa) roda em CPU; LLMs vão para a Groq.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

WORKDIR /opt/codigos/industry
COPY pyproject.toml README.md ./
COPY industry ./industry
RUN pip install -e ".[ml]" dagster-postgres

COPY docker/dagster.yaml docker/workspace.yaml /opt/dagster/
RUN mkdir -p $DAGSTER_HOME && cp /opt/dagster/dagster.yaml $DAGSTER_HOME/dagster.yaml
