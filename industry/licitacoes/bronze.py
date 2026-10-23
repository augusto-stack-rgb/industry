"""Camada BRONZE — normaliza as coletas (colunas nativas de cada fonte) para o
superset unificado de licitacoes.bronze e insere apenas os registros novos.

Entrada: um DataFrame por fonte (colunas nativas + coluna `fonte`).
Saída: linhas novas inseridas em licitacoes.bronze (dedup por row_hash).

`dados_originais` (jsonb) preserva a linha nativa inteira — nenhuma coluna dos
coletores se perde, mesmo as que não entram no superset.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date

import pandas as pd

from industry.licitacoes.config import (GRUPOS, PASSTHROUGH, BRONZE_COLS, JANELA_BRONZE_DIAS,
                                         FONTES_INTERNACIONAIS)
from industry.licitacoes import db
from industry.licitacoes.banestes import FONTE as FONTE_BANESTES
from industry.licitacoes.bcb import FONTE as FONTE_BCB
from industry.licitacoes.banrisul_total import FONTE as FONTE_BANRISUL
from industry.licitacoes.querido_diario import FONTE as FONTE_QUERIDO_DIARIO
from industry.licitacoes.bndes_inovacao import FONTE as FONTE_BNDES_INOVACAO
from industry.licitacoes.banco_mundial import FONTE as FONTE_BANCO_MUNDIAL
from industry.licitacoes.petronect import FONTE as FONTE_PETRONECT
from industry.licitacoes.pnud import FONTE as FONTE_PNUD
from industry.licitacoes.sebrae import FONTE as FONTE_SEBRAE
from industry.licitacoes.ungm import FONTE as FONTE_UNGM
from industry.licitacoes.config import FONTES_INOVACAO
from industry.licitacoes.prodoc import FONTE as FONTE_PRODOC

logger = logging.getLogger(__name__)

# Fontes cujo `id_nativo` é comprovadamente único (embute categoria/hash ou é a
# PK real do sistema de origem) — nelas, colisão de id_unificado após recoleta
# só pode significar "categoria/departamento diferente, mesmo numero" (bug real,
# comprovado em Banestes/BCB/Banrisul), então usam id_nativo como base e resolvem
# colisão por sufixo (#2, #3...) em vez de descarte. Ver DECISOES.md.
FONTES_ID_NATIVO_UNICO = {FONTE_BANESTES, FONTE_BCB, FONTE_BANRISUL,
                          # id = hash do trecho/título (querido_diario.py, bndes_inovacao.py)
                          FONTE_QUERIDO_DIARIO, FONTE_BNDES_INOVACAO,
                          # id do próprio portal; o número/referência se repete
                          # entre unidades (Sebrae) e versões do aviso (Banco Mundial)
                          FONTE_SEBRAE, FONTE_PETRONECT, FONTE_BANCO_MUNDIAL,
                          FONTE_PNUD, FONTE_UNGM,
                          # id = hash da url do PDF do edital (prodoc.py)
                          FONTE_PRODOC} | FONTES_INOVACAO   # portais de CPSI (cpsi_portais.py)

# Fontes que passaram a expor `numero_controle_pncp` DEPOIS de já terem linhas no
# bronze. O `base_id` prefere o controle, então adotá-lo agora reescreveria o
# id_unificado delas e a recoleta entraria inteira como "nova": medido em
# 17/09/2026, 716 das 1.670 linhas do ComprasNet voltariam duplicadas, com o
# mesmo id_nativo e id_unificado diferente (`comprasnet::2026-90001` viraria
# `comprasnet::2026-33657248000189-1-000001/2026`). O número de controle é DADO
# (é o que permite consultar o prazo da proposta, ver `pncp.identificadores_para_prazo`),
# não identidade — estas fontes continuam com a base de id que sempre tiveram.
FONTES_ID_SEM_CONTROLE = {"ComprasNet"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40]


def _primeiro(row: dict, candidatos: list[str]) -> str:
    lower = {str(k).lower(): k for k in row}   # p/ match case-insensitive
    for c in candidatos:
        k = c if c in row else lower.get(c.lower())
        if k is not None and str(row[k]).strip() not in ("", "nan", "None", "NaT"):
            return str(row[k]).strip()
    return ""


def _ano(canon: dict) -> str:
    for campo in ("data", "data_publicacao", "data_abertura"):
        d = db.to_date(canon.get(campo))
        if d:
            return d[:4]
    m = re.search(r"/(20\d{2})|(20\d{2})",
                  f"{canon.get('numero','')} {canon.get('titulo','')}")
    return (m.group(1) or m.group(2)) if m else ""


def marcar(canon: dict, row: dict) -> None:
    """Marcacoes para filtrar no painel, iguais para todas as fontes.

    ambito: "internacional" para organismos de fora (FONTES_INTERNACIONAIS),
    "nacional" para o resto. pessoa_fisica: "sim" quando o aviso aceita consultor
    individual sem CNPJ; o coletor diz isso em `pessoa_fisica` ou na `categoria`
    ("Pessoa física (consultor individual)").
    """
    canon["ambito"] = "internacional" if canon.get("fonte") in FONTES_INTERNACIONAIS else "nacional"
    pf = str(row.get("pessoa_fisica", "")).strip().lower()
    if pf not in ("sim", "nao"):
        pf = "sim" if str(canon.get("categoria", "")).lower().startswith("pessoa f") else "nao"
    canon["pessoa_fisica"] = pf


def normalizar_para_bronze(df_nativo: pd.DataFrame, fonte: str) -> pd.DataFrame:
    """Mapeia as colunas nativas de uma fonte para o superset canônico do bronze."""
    if df_nativo is None or df_nativo.empty:
        return pd.DataFrame(columns=BRONZE_COLS)
    df_nativo = df_nativo.fillna("")
    linhas = []
    for _, r in df_nativo.iterrows():
        row = dict(r)
        canon = {}
        for canonico, cands in GRUPOS.items():
            canon[canonico] = _primeiro(row, cands)
        for p in PASSTHROUGH:
            canon.setdefault(p, str(row.get(p, "")).strip())
        # `data` (a data que silver/gold/janela usam) cai para a data de
        # PUBLICAÇÃO quando a fonte não tem um campo "data" próprio.
        #
        # GRUPOS["data"] já lista "data_publicacao", mas `_primeiro` procura nas
        # chaves NATIVAS da linha — e a do Banrisul se chama "publishdate". O
        # resultado é que `data_publicacao` (canônico) era preenchido e `data`
        # ficava vazio: 5.433 linhas do Banrisul com data de publicação real no
        # banco e ignorada por todo o resto do pipeline, caindo no run_date.
        #
        # Só publicação. NÃO cair para abertura/sessão (BCB, BRB): essas datas
        # são POSTERIORES à publicação, então usá-las como `data` faria edital
        # antigo parecer recente e o seguraria na janela do mês. Sem publicação,
        # a regra certa já existe: run_date, a primeira vez que vimos o item
        # (DECISOES.md §9).
        if not canon.get("data"):
            canon["data"] = canon.get("data_publicacao", "")
        canon["fonte"] = fonte
        canon["id_nativo"] = str(row.get("id", "")).strip()
        if not canon.get("titulo"):
            canon["titulo"] = (f"{canon.get('tipo','')} {canon.get('numero','')}".strip()
                               or canon.get("objeto", "")[:120])
        canon["ano"] = canon.get("ano") or _ano(canon)
        marcar(canon, row)
        # linha nativa completa (sem vazios) -> nada se perde
        canon["dados_originais"] = json.dumps(
            {k: v for k, v in row.items() if str(v).strip() not in ("", "nan", "None")},
            ensure_ascii=False)
        linhas.append(canon)
    df = pd.DataFrame(linhas)
    for c in BRONZE_COLS:
        if c not in df.columns:
            df[c] = ""
    return df


def base_id(r) -> str:
    """Base do `id_unificado` de uma linha do bronze (antes do prefixo fonte::ano).

    Fora do `inserir_bronze` de propósito: esta regra define a identidade das
    linhas e um engano aqui reingere a fonte inteira sob ids novos — precisa ser
    testável sem banco.
    """
    if r.get("numero_controle_pncp") and r.get("fonte") not in FONTES_ID_SEM_CONTROLE:
        return r["numero_controle_pncp"]
    # Banestes/BCB/Banrisul reaproveitam "numero" entre categorias/departamentos
    # do próprio site — "025/2026" pode ser um Pregão Eletrônico, um Patrocínio
    # (Inexigibilidade) E uma renovação de licença de software, ao mesmo tempo;
    # no BCB, "Numero" reinicia por departamento ("1", "2"...). Usar "numero" cru
    # como id colide entre eles; o id_nativo de cada uma (`_id_banestes` em
    # banestes.py, `IdEdital` do BCB, `id` da API do Banrisul) já é único de
    # verdade. Outras fontes continuam como antes — não têm esse problema
    # comprovado, e trocar sem necessidade só gera reinserção cosmética de itens
    # ainda listados no site de origem.
    if r.get("fonte") in FONTES_ID_NATIVO_UNICO and r.get("id_nativo"):
        return r["id_nativo"]
    return r.get("numero") or r.get("id_nativo") or r["row_hash"][:10]


def inserir_bronze(dfs_por_fonte: list[pd.DataFrame], run_date) -> pd.DataFrame:
    """Concatena as fontes já normalizadas, gera id_unificado + row_hash, remove
    o que já existe no bronze (dedup) e insere. Retorna as linhas NOVAS."""
    dfs = [d for d in dfs_por_fonte if d is not None and not d.empty]
    if not dfs:
        logger.info("[bronze] nada a inserir")
        return pd.DataFrame(columns=BRONZE_COLS)
    df = pd.concat(dfs, ignore_index=True).fillna("")

    df["row_hash"] = df.apply(db.compute_row_hash, axis=1)


    # id_unificado estável e único: fonte::ano-<base>; # sufixo se colidir
    base_id_col = df.apply(base_id, axis=1)
    df["id_unificado"] = [
        f"{_slug(f)}::{a}-{b}".rstrip("-:")
        for f, a, b in zip(df["fonte"], df["ano"].astype(str), base_id_col.astype(str))
    ]

    # Sufixo (#2, #3...) p/ colisão DENTRO DO MESMO LOTE — necessário p/ TODAS as
    # fontes (id_unificado é PK do bronze: duas linhas do mesmo lote com o mesmo id
    # violam bronze_pkey na inserção). Ex.: mesmo "numero" em departamentos/
    # categorias diferentes coletados na mesma run (visto em Banrisul, BCB, TCE-RS).
    # Não aplica às fontes de FONTES_ID_NATIVO_UNICO: pra elas a colisão intra-lote
    # E contra o banco já é resolvida junto, abaixo (loop com `usados`).
    from collections import Counter
    visto = Counter()
    ids_lote = []
    for idu, fonte in zip(df["id_unificado"], df["fonte"]):
        if fonte in FONTES_ID_NATIVO_UNICO:
            ids_lote.append(idu)
            continue
        visto[idu] += 1
        ids_lote.append(idu if visto[idu] == 1 else f"{idu}#{visto[idu]}")
    df["id_unificado"] = ids_lote
    df["run_date"] = run_date

    # Linha que já existe e voltou com prazo ou "aceita pessoa física" que não
    # tinha: o dado entra (o bronze é first-seen, mas campo vazio não é dado).
    db.completar_bronze(df)

    # dedup interno + contra o que já está no banco
    df = df.drop_duplicates(subset=["row_hash"], keep="first")
    existentes = db.existing_bronze_hashes(db.get_engine())
    novos = df[~df["row_hash"].isin(existentes)].copy()
    n_ja_vistos = len(df) - len(novos)   # saíram no filtro de row_hash, logado abaixo
    # Também exclui id_unificado já presente no bronze. O PK é id_unificado, não
    # row_hash: uma licitação re-coletada com conteúdo alterado gera row_hash novo,
    # passa pelo filtro de hash acima e colidiria no bronze_pkey. Bronze é landing
    # imutável (first-seen wins), então descartar o id já visto é o comportamento
    # certo — PARA AS OUTRAS FONTES. Colisão de id ali quer dizer "mesmo processo,
    # recoletado" (numero/numero_controle_pncp não mudam quando o conteúdo muda).
    #
    # Banestes/BCB/Banrisul usam `id_nativo` como base (ver base_id acima, único de
    # verdade em cada uma) — foi o que consertou o bug real do "Pregão Eletrônico
    # 025/2026" do Banestes, que nunca entrava porque o `numero` colidia entre
    # categorias. Com essa base, colisão contra o BANCO significa "mesmo item
    # recoletado" e é descarte, igual às outras fontes: item do Banestes com texto
    # alterado gera id_nativo novo (o id embute md5 do texto) e entra sem colidir,
    # e BCB/Banrisul mantêm o id_nativo genuíno, que é a PK da origem. O sufixo
    # (#2, #3...) ficou só para colisão DENTRO do lote, onde ele evita violar o
    # bronze_pkey na inserção.
    ids_existentes = db.existing_ids("bronze")
    # Identidade estável das 3 fontes de id_nativo: sobrevive à troca do esquema
    # de id (a base já foi `numero` e virou `id_nativo`, então a linha antiga do
    # mesmo item pode estar guardada sob `banrisul::2026-0132/2026` enquanto a
    # coleta de hoje monta `banrisul::2026-354076`) e sobrevive a mudança de
    # conteúdo, que altera o row_hash. Sem isto, corrigir um parser reingere a
    # fonte inteira: medido em 17/09/2026, o fallback de `data` do Banrisul fez
    # 0 de 5.283 linhas casarem por row_hash, e 3.895 escapavam também do id.
    nativos_existentes = db.existing_native_ids(
        sorted({f for f in novos["fonte"].unique() if f in FONTES_ID_NATIVO_UNICO}))
    usados = set(ids_existentes)
    ids_resolvidos = []
    descartar = []
    n_renomeados = 0
    for idu, fonte, nat in zip(novos["id_unificado"], novos["fonte"],
                               novos.get("id_nativo", pd.Series([""] * len(novos),
                                                                index=novos.index))):
        if fonte not in FONTES_ID_NATIVO_UNICO:
            ids_resolvidos.append(idu)
            descartar.append(idu in ids_existentes)
            continue
        if idu in ids_existentes or (fonte, nat) in nativos_existentes:
            # Id já no bronze, e a base dele é o `id_nativo`: é o MESMO item
            # recoletado, não outro item com o mesmo numero. Descarta, como em
            # qualquer outra fonte. Sufixar aqui duplicava a linha toda vez que o
            # conteúdo mudasse — e "conteúdo mudou" inclui a gente passar a ler um
            # campo que antes ignorava. Medido em 17/09/2026, rodando a DAG
            # inteira: o fallback de `data` do Banrisul (que passou a ler
            # `publishdate`) mudou o row_hash de 5.466 linhas antigas, e elas
            # voltaram como 5.316 linhas novas com sufixo — 5.255 delas com gêmea
            # de mesmo id_nativo já no banco. A run normal insere de 8 a 63.
            # O sufixo continua abaixo, só para colisão DENTRO do lote.
            ids_resolvidos.append(idu)
            descartar.append(True)
            continue
        candidato = idu
        n = 1
        while candidato in usados:
            n += 1
            candidato = f"{idu}#{n}"
        if candidato != idu:
            n_renomeados += 1
        usados.add(candidato)
        ids_resolvidos.append(candidato)
        descartar.append(False)
    novos = novos.copy()
    novos["id_unificado"] = ids_resolvidos
    n_pre_id = len(novos)
    novos = novos[~pd.Series(descartar, index=novos.index)].copy()
    # A conta tem que fechar: coletado = já visto + id repetido + novos. Antes o
    # log só reportava os dois últimos, então "coletado 8927 | novos 52 |
    # descartados 522" deixava 8.353 linhas sem explicação e parecia perda
    # silenciosa — é só o dedup por row_hash contra o que já está no banco.
    logger.info(
        f"[bronze] coletado (após dedup interno): {len(df)} | "
        f"já no bronze (row_hash): {n_ja_vistos} | "
        f"descartados por id já existente: {n_pre_id - len(novos)} | "
        f"novos: {len(novos)} | "
        f"com sufixo por colisão (Banestes/BCB/Banrisul): {n_renomeados}"
    )

    if not novos.empty:
        db.insert_df(novos, "bronze", BRONZE_COLS)
    return novos


def _data_aparicao(row, hoje: date) -> date:
    """Data em que a licitação apareceu no portal.

    Usa os campos de publicação (`data`, `data_publicacao`) — nunca abertura/
    sessão, que são datas futuras de evento. Ignora datas no futuro (algumas
    fontes gravam a data da sessão em `data`). Sem data confiável de portal,
    cai no `run_date` (1ª vez que coletamos) — fallback definido com o usuário.
    """
    for c in ("data", "data_publicacao"):
        s = db.to_date(row.get(c))
        if s:
            d = date.fromisoformat(s)
            if d <= hoje:
                return d
    s = db.to_date(row.get("run_date"))
    return date.fromisoformat(s) if s else hoje


def filtrar_recentes(df: pd.DataFrame, dias: int = JANELA_BRONZE_DIAS) -> pd.DataFrame:
    """Corte GROSSO de recência no bronze: poda backlog antigo (por publicação, com
    fallback run_date) antes do download de anexos. Janela generosa (`JANELA_BRONZE_DIAS`)
    para não perder janelas de proposta longas — o corte FINO "acionável" (por
    dataEncerramentoProposta) é no gold (ver gold.filtrar_acionaveis e DECISOES.md §6).

    NÃO altera o bronze (histórico completo) — filtra só o que segue downstream.
    """
    if df is None or df.empty:
        return df
    hoje = date.today()
    limite = dias - 1

    def _aberta(r) -> bool:
        # prazo de proposta conhecido e futuro passa, publicada quando for: CPSI
        # fica aberto 30 dias ou mais (a SEFAZ-PI sumia por ter saído 3 semanas antes)
        d = db.to_date(r.get("data_encerramento_proposta"))
        return bool(d) and date.fromisoformat(d) >= hoje

    mask = df.apply(lambda r: _aberta(r) or 0 <= (hoje - _data_aparicao(r, hoje)).days <= limite,
                    axis=1)
    out = df[mask].copy()
    logger.info(
        f"[recencia] {len(df)} -> {len(out)} (janela {dias}d, hoje/ontem; "
        f"removidos {len(df) - len(out)} antigos)"
    )
    return out
