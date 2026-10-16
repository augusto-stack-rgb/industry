"""Lógica da pipeline de operações de M&A (assets em industry.noticias.assets).

Cada função pública é chamada por uma ``@task`` do ``dag.py``. Todos os imports
pesados (pandas, torch/transformers, groq, sqlalchemy, shared.*) são feitos
DENTRO dos corpos das funções — o parse do DAG pelo scheduler não deve carregar
esses módulos.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Caminhos FIXOS sob $INDUSTRY_DATA_DIR (industry.shared.storage) — cada rodada
# sobrescreve com o relatório mais recente.
HTML_KEYS = {
    "days:90": "noticias_mna/operacoes_mna_3meses.html",
    "ytd": "noticias_mna/operacoes_mna_ytd.html",
}

# Idem, PDF (impresso do HTML pelo Chrome headless do container selenium).
PDF_KEYS = {
    "days:90": "noticias_mna/operacoes_mna_3meses.pdf",
    "ytd": "noticias_mna/operacoes_mna_ytd.pdf",
}

# Mesmo relatório, filtrado só para operações da região "Brasil" (report_html.
# REGIAO_BRASIL). Só PDF; o HTML é o único de HTML_KEYS.
PDF_KEYS_BRASIL = {
    "days:90": "noticias_mna/operacoes_mna_3meses_brasil.pdf",
    "ytd": "noticias_mna/operacoes_mna_ytd_brasil.pdf",
}


def setup() -> None:
    """Cria as tabelas de destino no schema noticias (idempotente)."""
    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

    from industry.noticias.mna import db

    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        db.garantir_tabelas(engine)
    finally:
        engine.dispose()


def extrair() -> dict:
    """Roda a extração completa e persiste no Postgres. Resumível e idempotente.

    Fluxo: carrega o pool de M&A classificado → remove os links já processados
    (log) → gate keyword+zero-shot + dedup de título → extrai via Groq artigo a
    artigo → REVISA cada operação (validação "é M&A de verdade?" + dedup
    semântico contra as operações dos últimos ``DEDUP_LLM_DIAS`` dias) →
    persiste incrementalmente (append + marca no log) → consolida a tabela.

    Se o limite diário de tokens da Groq for atingido, para e persiste o parcial
    (a rodada seguinte retoma de onde parou). Quando o limite estoura no meio da
    revisão de um artigo, o link NÃO é marcado como processado — o artigo inteiro
    é refeito na próxima rodada, em vez de entrar pela metade.

    RASTREIO EM BASE (não só no log do Airflow): toda rodada grava uma linha em
    ``noticias.mna_execucoes`` (status, contadores, artigo em que parou) e
    reescreve ``noticias.mna_pendencias`` com os artigos que ficaram de fora por
    esgotamento das chaves Groq. Artigos cuja extração falhou levam
    ``motivo_falha`` no log e voltam ao pool (até ``MAX_TENTATIVAS_ARTIGO``).

    Returns:
        Dicionário de estatísticas (serializável em XCom).
    """
    import pendulum

    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

    from industry.noticias.mna import config as C
    from industry.noticias.mna import db
    from industry.noticias.mna.pipeline import (
        ChavesInvalidas,
        GroqExtractor,
        LimiteDiario,
        revisar_operacoes,
        selecionar_para_extracao,
    )

    iniciado_em = pendulum.now("America/Sao_Paulo")
    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    stats: dict = {"pool_total": 0, "pool_novos": 0, "selecionados": 0,
                   "processados": 0, "ops_extraidas": 0, "ops_rejeitadas_validacao": 0,
                   "ops_rejeitadas_dedup": 0, "ops_mescladas": 0, "artigos_pendentes": 0,
                   "artigos_com_falha": 0, "tpd_atingido": False,
                   "total_consolidado": 0, "status": "success"}
    motivo_exec: str | None = None
    link_interrompido: str | None = None
    pendencias: list[dict] = []
    try:
        pool = db.carregar_pool(engine)
        stats["pool_total"] = int(len(pool))
        if pool.empty:
            log.info("[mna] pool vazio (nenhuma notícia em setor %s).", C.SETORES_MNA)
            return stats

        processados = db.links_processados(engine)
        pool = pool[~pool["link"].astype(str).isin(processados)].reset_index(drop=True)
        stats["pool_novos"] = int(len(pool))
        log.info("[mna] pool=%d, já processados=%d, novos=%d",
                 stats["pool_total"], len(processados), stats["pool_novos"])
        if pool.empty:
            log.info("[mna] nada novo a extrair.")
            return stats
        if C.MNA_LIMIT:
            pool = pool.head(C.MNA_LIMIT)

        selected = selecionar_para_extracao(pool)
        stats["selecionados"] = int(len(selected))
        if selected.empty:
            log.info("[mna] nenhum candidato passou no gate zero-shot.")
            # Marca os avaliados como processados p/ não reavaliar o gate toda rodada.
            for link in pool["link"].astype(str):
                db.registrar_processado(engine, link, 0)
            return stats

        # Marca como processados(0) os artigos que o gate AVALIOU e descartou
        # (reprovados no zero-shot + duplicatas de título). São determinísticos:
        # não devem ser reextraídos nem re-avaliados toda rodada. Feito antes da
        # extração porque o gate já rodou sobre todo o pool — independe de TPD.
        selecionados = set(selected["link"].astype(str))
        descartados = [ln for ln in pool["link"].astype(str) if ln not in selecionados]
        for link in descartados:
            db.registrar_processado(engine, link, 0)
        log.info("[mna] %d artigos avaliados e descartados pelo gate (marcados processados=0)",
                 len(descartados))

        extractor = GroqExtractor()
        # Base de comparação do dedup semântico: o que já está na tabela nos
        # últimos N dias + o que for aprovado nesta rodada (senão duas notícias do
        # mesmo deal na mesma rodada passariam as duas).
        recentes = (db.operacoes_recentes(engine, C.DEDUP_LLM_DIAS, C.DEDUP_LLM_MAX_RECENTES)
                    if C.DEDUP_LLM_OPERACOES else [])
        # Índice completo (sem filtro de data) para o blocking por nome: deal longo
        # é noticiado por meses e escapa da janela de DEDUP_LLM_DIAS.
        indice = (db.carregar_indice_operacoes(engine) if C.DEDUP_LLM_OPERACOES else [])
        log.info("[mna] dedup semântico: %d operações dos últimos %d dias + índice "
                 "de %d operações para blocking por nome",
                 len(recentes), C.DEDUP_LLM_DIAS, len(indice))

        linhas = list(selected.iterrows())
        n_proc = n_ops = n_rej_val = n_rej_dup = n_falhas = n_mescladas = 0
        for i, (_, row) in enumerate(linhas):
            link = str(row.get("link", ""))
            titulo = str(row.get("titulo", ""))
            fonte = str(row.get("fonte", ""))
            data = str(row.get("data", ""))
            texto = str(row.get("texto", ""))
            try:
                ops, falha_extracao = extractor.extract(
                    titulo=titulo, fonte=fonte, data=data, texto=texto)
                for op in ops:
                    op["titulo_artigo"] = row.get("titulo")
                    op["link_artigo"] = link
                    op["data_artigo"] = data[:10]
                    op["fonte_artigo"] = row.get("fonte")
                    op["zs_score"] = float(row.get("zs_score_ma", 0.0))
                aprovadas, rejeitadas, falhas_revisao = revisar_operacoes(
                    extractor, ops, titulo=titulo, fonte=fonte, data=data,
                    texto=texto, recentes=recentes, indice=indice,
                )
            except LimiteDiario as exc:
                # Chaves Groq esgotadas: o artigo atual e os seguintes ficam
                # PENDENTES e vão para a base (não só para o log do Airflow).
                stats["tpd_atingido"] = True
                stats["status"] = "partial_tpd"
                motivo_exec = f"chaves Groq esgotadas (TPD): {exc}"
                link_interrompido = link
                pendencias = [{
                    "link_artigo": str(r.get("link", "")),
                    "titulo_artigo": r.get("titulo"),
                    "motivo": "chaves Groq esgotadas (TPD) — não processado nesta rodada",
                } for _, r in linhas[i:]]
                log.warning("[mna] limite diário de tokens após %d artigos — %d artigos "
                            "PENDENTES (artigo atual será refeito). %s",
                            n_proc, len(pendencias), exc)
                break
            except ChavesInvalidas as exc:
                # Sem chave válida esperar não resolve (ao contrário do TPD): registra
                # e RELANÇA, para a task falhar e disparar o alerta — alguém precisa
                # renovar as Connections. Sem isso a DAG ficaria verde produzindo
                # rodadas vazias.
                stats["status"] = "erro_chaves"
                motivo_exec = f"nenhuma chave Groq válida: {exc}"
                link_interrompido = link
                pendencias = [{
                    "link_artigo": str(r.get("link", "")),
                    "titulo_artigo": r.get("titulo"),
                    "motivo": "nenhuma chave Groq válida — renovar as chaves GROQ_API_KEY_* no .env",
                } for _, r in linhas[i:]]
                log.error("[mna] nenhuma chave Groq válida após %d artigos — %d artigos "
                          "PENDENTES. Renove as Connections groq_api_*. %s",
                          n_proc, len(pendencias), exc)
                stats["processados"] = n_proc
                stats["ops_extraidas"] = n_ops
                raise

            if falha_extracao:
                # Nada foi gravado: registra o motivo e devolve o artigo ao pool
                # (até MAX_TENTATIVAS_ARTIGO) em vez de gravar "0 operações".
                n_falhas += 1
                db.registrar_processado(engine, link, 0, motivo_falha=falha_extracao)
                log.warning("[mna] extração NÃO rodou para %s: %s", link, falha_extracao)
                continue

            ids_aprovadas = db.append_operacoes(engine, aprovadas)
            rej_ids = db.append_rejeitadas(engine, rejeitadas)
            # Duplicata não some: atualiza a operação cadastrada (status mais
            # avançado, valor ausente) e entra no histórico do deal.
            for rej, rej_id in zip(rejeitadas, rej_ids):
                ref = rej.get("ref_existente")
                if rej.get("etapa") != "dedup" or ref is None:
                    continue
                alt = db.mesclar_duplicata(engine, int(ref), rej, origem="dedup",
                                           rej_id=rej_id)
                if alt is None:
                    log.warning("[mna] duplicata de %s não mesclada: operação %s não "
                                "existe mais.", link, ref)
                elif alt:
                    n_mescladas += 1
                    log.info("[mna] operação %s atualizada pela duplicata: %s",
                             ref, ", ".join(alt))
            # Revisão degradada não retenta (as operações já entraram), mas fica
            # registrada — a rodada não pode dar a impressão de revisão completa.
            motivo_revisao = "; ".join(dict.fromkeys(falhas_revisao))[:500] or None
            if motivo_revisao:
                n_falhas += 1
                log.warning("[mna] revisão DEGRADADA em %s: %s", link, motivo_revisao)
            db.registrar_processado(engine, link, len(aprovadas),
                                    motivo_revisao=motivo_revisao)
            # Aprovadas desta rodada viram base de comparação para os próximos
            # artigos, com o op_id real — a duplicata delas também é mesclada.
            for op_id, op in zip(ids_aprovadas, aprovadas):
                entrada = {
                    "ref": op_id,
                    "empresa_adquirente": op.get("empresa_adquirente"),
                    "empresa_alvo": op.get("empresa_alvo"),
                    "valor": op.get("valor"),
                    "moeda": op.get("moeda"),
                    "data_artigo": op.get("data_artigo"),
                }
                recentes.append(entrada)
                # Também no índice: o blocking por nome tem prioridade sobre o teto,
                # então uma duplicata do mesmo deal na mesma rodada é sempre vista.
                indice.append(entrada)
            n_proc += 1
            n_ops += len(aprovadas)
            n_rej_val += sum(1 for r in rejeitadas if r.get("etapa") == "validacao")
            n_rej_dup += sum(1 for r in rejeitadas if r.get("etapa") == "dedup")
            if n_proc % C.PERSIST_EVERY == 0:
                log.info("[mna] %d/%d artigos processados (%d operações aprovadas, "
                         "%d rejeitadas)", n_proc, len(selected), n_ops,
                         n_rej_val + n_rej_dup)

        stats["processados"] = n_proc
        stats["ops_extraidas"] = n_ops
        stats["ops_rejeitadas_validacao"] = n_rej_val
        stats["ops_rejeitadas_dedup"] = n_rej_dup
        stats["ops_mescladas"] = n_mescladas
        stats["artigos_com_falha"] = n_falhas
        stats["artigos_pendentes"] = len(pendencias)
        stats["total_consolidado"] = db.consolidar_tabela(engine)
        log.info("[mna] concluído: %d artigos, %d operações novas (%d rejeitadas na "
                 "validação, %d como duplicata, %d delas atualizando a operação "
                 "cadastrada), %d com falha de LLM, %d pendentes, %d na tabela "
                 "consolidada.",
                 n_proc, n_ops, n_rej_val, n_rej_dup, n_mescladas, n_falhas,
                 len(pendencias), stats["total_consolidado"])
        return stats
    except Exception as exc:
        # Preserva o que um handler mais específico (ex.: ChavesInvalidas) já
        # registrou — aqui só cobre a exceção que ninguém classificou.
        if stats["status"] == "success":
            stats["status"] = "erro"
        if motivo_exec is None:
            motivo_exec = f"{type(exc).__name__}: {str(exc)[:500]}"
        raise
    finally:
        # Registro em BASE do desfecho da rodada — sempre, inclusive em erro/TPD.
        # Envolvido em try próprio: falhar ao auditar não pode mascarar o
        # resultado (nem a exceção original) da extração.
        stats["artigos_pendentes"] = len(pendencias)
        try:
            exec_id = db.registrar_execucao(
                engine, stats, iniciado_em=iniciado_em, motivo=motivo_exec,
                link_interrompido=link_interrompido)
            db.sincronizar_pendencias(engine, exec_id, pendencias)
        except Exception:
            log.exception("[mna] falha ao registrar a execução/pendências na base.")
        engine.dispose()


def _imprimir_pdf(html: str) -> bytes:
    """Imprime o HTML em PDF via Chrome headless remoto (container ``selenium``,
    configurado em SELENIUM_URL)."""
    import base64

    from selenium import webdriver
    from selenium.webdriver.common.print_page_options import PrintOptions

    from industry.shared.connections import get_selenium_command_executor

    options = webdriver.ChromeOptions()
    for option in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--log-level=1"):
        options.add_argument(option)
    driver = webdriver.Remote(get_selenium_command_executor("/wd/hub"), options=options)
    try:
        driver.get("about:blank")
        # document.write via argumento — evita o limite de tamanho de navegação
        # em data: URLs.
        driver.execute_script(
            "document.open(); document.write(arguments[0]); document.close();", html
        )
        print_options = PrintOptions()
        print_options.background = True  # mantém as cores de fundo dos cards
        return base64.b64decode(driver.print_page(print_options))
    finally:
        driver.quit()


def publicar_html() -> dict:
    """Gera os 2 reports (HTML + PDF impresso via Chrome headless) de
    noticias.mna_operations e grava em $INDUSTRY_DATA_DIR/noticias_mna/ (caminhos
    fixos, sobrescritos a cada rodada).

    Por modo saem DOIS PDFs — o completo (``PDF_KEYS``) e o filtrado na região
    "Brasil" (``PDF_KEYS_BRASIL``).

    Se o Chrome remoto (selenium-chrome) estiver fora do ar, o HTML ainda é
    publicado normalmente — só o PDF daquele modo é pulado (logado como erro).

    Returns:
        Dicionário com o tamanho (bytes) de cada HTML/PDF publicado.
    """
    import pandas as pd

    from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine
    from industry.shared import storage

    from industry.noticias.mna import config as C
    from industry.noticias.mna import report_html as R

    engine = get_sqlalchemy_engine(NOTICIAS_PG)
    try:
        df = pd.read_sql(f"SELECT * FROM {C.TABELA_OPERACOES}", engine)
    finally:
        engine.dispose()

    if df.empty:
        log.warning("[mna] mna_operations vazio — nada a publicar.")
        return {"publicados": 0}

    resultado: dict = {}
    resultado_pdf: dict = {}
    for modo, key in HTML_KEYS.items():
        R.ULTIMO_TRI_MODO = modo
        dados = R.carregar_dataframe(df)
        if dados.empty:
            continue
        asof = dados["_data"].max()
        html_str = R.render_html(R.construir_contexto(dados, asof))
        html = html_str.encode("utf-8")
        storage.write_fixed(key, html)
        resultado[modo] = len(html)
        log.info("[mna] HTML publicado: %s (%d bytes)", storage.path_of(key), len(html))

        pdf_key = PDF_KEYS.get(modo)
        if pdf_key is None:
            continue
        try:
            pdf = _imprimir_pdf(html_str)
        except Exception:
            log.exception("[mna] falha ao imprimir PDF (modo=%s) — selenium-chrome fora do ar?", modo)
            continue
        storage.write_fixed(pdf_key, pdf)
        resultado_pdf[modo] = len(pdf)
        log.info("[mna] PDF publicado: %s (%d bytes)", storage.path_of(pdf_key), len(pdf))

        # Variante só Brasil do MESMO relatório (segundo anexo do email semanal).
        # Falha aqui não derruba o PDF completo, que já foi publicado acima.
        pdf_key_br = PDF_KEYS_BRASIL.get(modo)
        if pdf_key_br is None:
            continue
        try:
            dados_br = R.filtrar_regiao(dados, R.REGIAO_BRASIL)
            if dados_br.empty:
                log.warning("[mna] nenhuma operação da região %s no modo %s — "
                            "PDF filtrado não publicado.", R.REGIAO_BRASIL, modo)
                continue
            # Mesma asof do painel completo: janelas diferentes tornariam as duas
            # versões incomparáveis.
            html_br = R.render_html(
                R.construir_contexto(dados_br, asof, escopo_regiao=R.REGIAO_BRASIL))
            pdf_br = _imprimir_pdf(html_br)
        except Exception:
            log.exception("[mna] falha ao gerar/imprimir o PDF da região %s (modo=%s).",
                          R.REGIAO_BRASIL, modo)
            continue
        storage.write_fixed(pdf_key_br, pdf_br)
        resultado_pdf[f"{modo}:brasil"] = len(pdf_br)
        log.info("[mna] PDF (região %s) publicado: %s (%d bytes)",
                 R.REGIAO_BRASIL, storage.path_of(pdf_key_br), len(pdf_br))
    return {"publicados": len(resultado), "bytes": resultado, "pdf_bytes": resultado_pdf}
