"""Nota de relevância, geografia e assunto das notícias de cada setor.

Porte do ``task_classificador.py`` da DAG MARKETING_EnvioNoticias. A nota agora
mede o interesse para uma consultoria de ML Engineering, Data Management e Data
Engineering: oportunidades de negócio e fatos que movem o mercado do setor.
"""
import pandas as pd
import re
import logging
import unicodedata


from sqlalchemy import text

from industry.noticias.envio.config import (
    BONUS_POR_PORTAL,
    CARACTERES_LEAD,
    LIMIAR_MESMO_ASSUNTO,
    MEIA_VIDA_MESMO_ASSUNTO_DIAS,
    MODELO_EMBEDDING_ASSUNTO,
    MODELO_CLASSIFICACAO,
    NOTA_COM_LEAD,
    NOTA_DO_FATO,
    OPORTUNIDADES_CONSULTORIA,
    PENALIDADE_REPETICAO,
    QUANTOS_DIAS_ATRAS,
    RODADAS_REPESCAGEM,
    SETORES,
    Setores_Contexto,
    TAMANHO_LISTA_MESMO_FATO,
    TAMANHO_LOTE_RANKING,
    TAMANHO_LOTE_REPESCAGEM,
    TETO_BONUS_COBERTURA,
    TIMEOUT_CLASSIFICACAO_S,
)
from industry.shared import embeddings, llm, storage
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

# A resposta é casada pelo ID da notícia, não pelo título. Casar pelo título
# exigia que o LLM devolvesse o texto idêntico, e ele normaliza espaço duplo,
# aspas e caixa — a notícia ficava sem nota sem erro nenhum.
PATTERN_SCORE = (
    r"^\s*(\d+)\s*#\s*(\d{1,3})\s*#\s*(REGIONAL_SUL|BRASIL|GLOBAL)\s*#\s*(.+?)\s*$"
)


COLUNAS_AUDITORIA_EMAIL = [
    "run_id", "gerado_em", "ranking_llm", "assunto", "componente",
    "fator_recencia", "fator_repeticao", "bonus_cobertura",
]


def gerar_contexto(setor):
    texto = "\nOPORTUNIDADES PARA A CONSULTORIA:\n"
    for t in OPORTUNIDADES_CONSULTORIA.get(setor, []):
        texto += f"- {t}\n"
    texto += "\nTEMAS QUE MOVEM O MERCADO DO SETOR:\n"
    for nivel, temas in Setores_Contexto.get(setor, {}).items():
        texto += f"\n{nivel}:\n"
        for t in temas:
            texto += f"- {t}\n"
    return texto


def _novo_cliente():
    # Era Ollama local (think=False); agora o mesmo modelo pelo roteador de chaves.
    return llm.cliente(
        MODELO_CLASSIFICACAO,
        timeout=TIMEOUT_CLASSIFICACAO_S,
        temperature=0,
        reasoning_effort="none",
        max_tokens=4096,
    )


def _scorear_lote(client, setor, lote):
    """Pede nota, geografia e assunto de cada notícia do lote.

    ``lote`` é uma lista de (id, titulo) ou, com NOTA_COM_LEAD, de
    (id, titulo, lead). Retorna {id: (score, tag, assunto)}
    só com os IDs do lote que voltaram no formato — o que faltar é repescado
    por quem chama.

    O score (0-100) é absoluto — comparável entre lotes e chamadas
    diferentes, ao contrário de um ranking ordinal relativo ao lote.
    """
    system_msg = "Responda SOMENTE no formato solicitado. Qualquer texto fora do formato é proibido."
    if NOTA_COM_LEAD:
        linhas = "\n".join(
            f"{item[0]}# {item[1]}" + (f" — {item[2]}" if len(item) > 2 and item[2] else "")
            for item in lote
        )
        descricao_lista = "ID# título — início do texto"
        regua = """
RÉGUA DA NOTA (use a faixa inteira, não concentre tudo em 85-90):
- 90-100: OPORTUNIDADE CLARA — empresa nomeada do setor anunciando investimento, projeto
  ou contratação em dados, IA, digitalização ou automação; nova planta, expansão ou fusão
  de empresa nomeada que exige integrar sistemas e dados; regulação que obriga o setor
  inteiro a coletar, integrar ou reportar dados; ou fato que muda o mercado do setor inteiro.
- 70-89: fato relevante de empresa grande do setor (resultado, expansão, contrato,
  investimento), indicador setorial importante, mudança de preço/demanda/oferta com efeito
  amplo, tecnologia nova chegando ao setor.
- 40-69: notícia do setor com impacto limitado, opinião ou análise, indicador secundário.
- 0-39: pouco ou nenhum interesse para a consultoria nem para o mercado do setor.
"""
    else:
        linhas = "\n".join(f"{item[0]}# {item[1]}" for item in lote)
        descricao_lista = "ID# título"
        regua = ""
    human_msg = f"""
Você vai classificar notícias para uma consultoria de Machine Learning Engineering, Data
Management e Data Engineering que atende empresas do setor "{setor}". Ela procura
OPORTUNIDADES DE NEGÓCIO (empresas que vão precisar de engenharia de dados, gestão de dados
ou modelos de ML) e acompanha os FATOS QUE MOVEM O MERCADO dos seus clientes.

Contexto do setor:
{gerar_contexto(setor)}

Tarefa:
Para cada notícia, dê:
1. Uma NOTA de relevância de 0 a 100: quanto a notícia é uma oportunidade de negócio para a
   consultoria ou um fato importante que afeta o mercado do setor "{setor}".
2. Uma TAG obrigatória de geografia:
    REGIONAL_SUL (Sul do Brasil)
    BRASIL
    GLOBAL
3. O ASSUNTO: de 2 a 6 palavras, em português, dizendo QUAL fato ou indicador a notícia trata
   (ex.: "preço do petróleo", "terminal de GNL em Rio Grande", "leilão de energia", "vacina contra dengue").
   - Notícias sobre o mesmo fato ou o mesmo indicador recebem o MESMO assunto, mesmo em idiomas diferentes.
   - Em operações, contratos e investimentos, nomeie as empresas envolvidas
     (ex.: "compra da BravOcean pela CoreMarine", "FID da TotalEnergies na Nigéria") — nunca só a categoria ("M&A", "investimento").

REGRAS OBRIGATÓRIAS:
- Use EXATAMENTE o formato abaixo, uma linha por notícia, começando pelo ID recebido.
- Não repita o título.
- Não adicione comentários.
- Não explique nada.
- A nota é um número inteiro de 0 a 100.
{regua}
FORMATO EXATO:
ID# NOTA # TAG # ASSUNTO

As notícias são ({descricao_lista}):
{linhas}
"""
    resposta = client.create(
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": human_msg},
        ],
    )
    texto_resposta = resposta.choices[0].message.content.strip()

    ids_validos = {item[0] for item in lote}
    resultado = {}
    for linha in texto_resposta.split("\n"):
        match = re.match(PATTERN_SCORE, linha)
        if not match:
            continue
        noticia_id = int(match.group(1))
        score = int(match.group(2))
        if noticia_id not in ids_validos or not 0 <= score <= 100:
            continue
        resultado[noticia_id] = (score, match.group(3), match.group(4).strip())
    return resultado


def _dividir_em_lotes(itens, tamanho):
    return [itens[i : i + tamanho] for i in range(0, len(itens), tamanho)]


def _rodada(client, setor, itens, tamanho, rotulo):
    resultado = {}
    for lote in _dividir_em_lotes(itens, tamanho):
        try:
            resultado.update(_scorear_lote(client, setor, lote))
        except Exception as e:
            logging.error(
                f"[MonitoramentoEnvio] {rotulo}: lote de {len(lote)} de '{setor}' falhou — "
                f"as notícias dele voltam na repescagem: {e}"
            )
    return resultado


def _classificar_setor(client, setor, itens):
    """Nota, geografia e assunto de cada notícia do setor.

    ``itens`` é uma lista de (id, titulo). Uma passada em lotes de
    TAMANHO_LOTE_RANKING e depois até RODADAS_REPESCAGEM passadas só com o que
    faltou, em lotes menores (lote menor falha menos e isola a notícia que
    confunde o modelo). Retorna {id: (score, tag, assunto)}.
    """
    resultado = _rodada(client, setor, itens, TAMANHO_LOTE_RANKING, "1ª passada")

    for rodada in range(1, RODADAS_REPESCAGEM + 1):
        faltando = [item for item in itens if item[0] not in resultado]
        if not faltando:
            break
        logging.warning(
            f"[MonitoramentoEnvio] '{setor}': {len(faltando)} de {len(itens)} sem nota — "
            f"repescagem {rodada}/{RODADAS_REPESCAGEM}"
        )
        resultado.update(
            _rodada(client, setor, faltando, TAMANHO_LOTE_REPESCAGEM, f"repescagem {rodada}")
        )

    faltando = [item[1] for item in itens if item[0] not in resultado]
    if faltando:
        # Continua registrado, mas agora com a contagem e exemplos: é o número
        # que precisa ser olhado quando o e-mail parecer incompleto.
        logging.error(
            f"[MonitoramentoEnvio] '{setor}': {len(faltando)} de {len(itens)} notícias "
            f"ficaram SEM NOTA após a repescagem e não entram no e-mail. "
            f"Exemplos: {faltando[:5]}"
        )
    else:
        logging.info(f"[MonitoramentoEnvio] '{setor}': {len(itens)} notícias com nota.")
    return resultado


def _chave_assunto(rotulo):
    """Normalização mínima: caixa, acento e espaços."""
    sem_acento = unicodedata.normalize("NFKD", str(rotulo)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def _unificar_assuntos(client, setor, rotulos, frequencia):
    """Agrupa rótulos de assunto que tratam da mesma coisa.

    Cada lote rotulou os assuntos sem ver os outros, então o mesmo tema chega
    escrito de jeitos diferentes ("preço do petróleo", "cotação do Brent").
    Os rótulos viram vetores (MODELO_EMBEDDING_ASSUNTO) e são agrupados do
    mais frequente ao menos: cada um entra no grupo cujo centro está a pelo
    menos LIMIAR_MESMO_ASSUNTO de similaridade, ou abre um grupo novo.
    Comparar com o centro, e não com o vizinho mais parecido, evita a
    corrente A~B~C que juntaria assuntos distantes.

    Uma chamada ao LLM com a lista inteira foi testada antes e descartada: com
    ~450 rótulos ele ou copiava cada um (quase nada agrupado) ou jogava 212 em
    "exploração de petróleo".

    Retorna {rotulo: grupo}, sendo o grupo o rótulo que abriu o grupo. Se o
    embedding falhar, cada rótulo normalizado é o próprio grupo — a recência
    fica mais conservadora (só pega rótulos idênticos), nunca mais agressiva.
    """
    import numpy as np

    fallback = {r: _chave_assunto(r) for r in rotulos}
    if len(rotulos) < 2:
        return fallback
    try:
        vetores = np.array(embeddings.embed(list(rotulos), MODELO_EMBEDDING_ASSUNTO), dtype=float)
    except Exception as e:
        logging.error(
            f"[MonitoramentoEnvio] '{setor}': embedding dos assuntos falhou — usando os rótulos "
            f"crus (a recência só compara assuntos idênticos): {e}"
        )
        return fallback
    vetores /= np.linalg.norm(vetores, axis=1, keepdims=True)

    ordem = sorted(range(len(rotulos)), key=lambda i: (-frequencia.get(rotulos[i], 0), rotulos[i]))
    centros, somas, nomes = [], [], []
    grupos = {}
    for i in ordem:
        v = vetores[i]
        if centros:
            sims = np.array(centros) @ v
            j = int(sims.argmax())
            if sims[j] >= LIMIAR_MESMO_ASSUNTO:
                somas[j] = somas[j] + v
                centros[j] = somas[j] / np.linalg.norm(somas[j])
                grupos[rotulos[i]] = nomes[j]
                continue
        centros.append(v.copy())
        somas.append(v.copy())
        nomes.append(_chave_assunto(rotulos[i]))
        grupos[rotulos[i]] = nomes[-1]

    logging.info(
        f"[MonitoramentoEnvio] '{setor}': {len(rotulos)} assuntos em {len(nomes)} grupos."
    )
    return grupos


def _fator_recencia(df, grupo):
    """0,5 ** (dias até a mais recente do grupo / MEIA_VIDA_MESMO_ASSUNTO_DIAS)."""
    mais_recente = df.groupby(grupo)["data"].transform("max")
    dias = (mais_recente - df["data"]).dt.total_seconds() / 86400
    return 0.5 ** (dias.fillna(0) / MEIA_VIDA_MESMO_ASSUNTO_DIAS)


def _grupos_mesmo_fato(client, setor, df):
    """Agrupa as notícias da lista curta que relatam o MESMO fato.

    Para cada geografia, as TAMANHO_LISTA_MESMO_FATO de maior nota (já com a
    recência por assunto) vão numa chamada ao LLM, que devolve um número de
    grupo por notícia. Lista curta é o que ele faz bem: agrupar os ~450
    rótulos de um setor de uma vez falhou. Retorna {id: chave_do_grupo} só
    para os IDs da lista curta; se a chamada falhar, a geografia fica sem
    grupos de fato (vale só o assunto) — nada é descartado.

    ``df`` precisa de ``titulo``, ``tag`` e ``ranking_rec``; o índice é o ID.
    """
    grupos = {}
    for tag, sub in df.groupby("tag"):
        curta = sub.sort_values("ranking_rec", ascending=False).head(TAMANHO_LISTA_MESMO_FATO)
        if len(curta) < 2:
            continue
        linhas = "\n".join(f"{i}# {t}" for i, t in curta["titulo"].items())
        human_msg = f"""
Abaixo estão títulos de notícias do setor "{setor}" (ID# título).
Agrupe os títulos que relatam o MESMO fato — a mesma notícia, ainda que por veículos ou idiomas diferentes,
ou desdobramentos imediatos do mesmo acontecimento (ex.: "empresa X compra Y" e "ações de X caem após compra de Y").
NÃO junte fatos diferentes só por envolverem a mesma empresa ou o mesmo tema
(ex.: dois investimentos diferentes da mesma empresa são fatos diferentes).

Para cada título, devolva o número do grupo. Títulos do mesmo fato recebem o mesmo número; título sem par recebe um número só dele.

FORMATO EXATO, uma linha por título, sem comentários:
ID# NUMERO_DO_GRUPO

Títulos:
{linhas}
"""
        try:
            resposta = client.create(
                messages=[
                    {"role": "system", "content": "Responda SOMENTE no formato solicitado."},
                    {"role": "user", "content": human_msg},
                ],
            )
        except Exception as e:
            logging.error(
                f"[MonitoramentoEnvio] '{setor}'/{tag}: agrupamento de mesmo fato falhou — "
                f"vale só o assunto: {e}"
            )
            continue
        ids_validos = set(curta.index)
        for linha in resposta.choices[0].message.content.strip().split("\n"):
            m = re.match(r"^\s*(\d+)\s*#\s*(\d+)\s*$", linha)
            if m and int(m.group(1)) in ids_validos:
                grupos[int(m.group(1))] = f"fato:{tag}:{m.group(2)}"
    return grupos


def _componentes(df, grupos_fato):
    """Junta grupo de assunto e grupo de mesmo fato: duas notícias ficam no
    mesmo componente se compartilham um ou outro (union-find)."""
    pai = {}

    def raiz(x):
        pai.setdefault(x, x)
        while pai[x] != x:
            pai[x] = pai[pai[x]]
            x = pai[x]
        return x

    def unir(a, b):
        pai[raiz(a)] = raiz(b)

    for i, grupo in df["grupo_assunto"].items():
        unir(("n", i), ("a", grupo))
    for i, grupo in grupos_fato.items():
        unir(("n", i), ("f", grupo))
    return pd.Series({i: str(raiz(("n", i))) for i in df.index}, name="componente")


def _portal(link):
    m = re.match(r"^https?://(?:www\.)?([^/]+)", str(link or ""))
    return m.group(1).lower() if m else ""


def _nota_final(df):
    """Nota final = nota do LLM × recência × repetição, dentro do componente.

    - Recência: a mais recente do componente tem fator 1; as anteriores perdem
      metade a cada MEIA_VIDA_MESMO_ASSUNTO_DIAS de distância dela.
    - Repetição: ordenadas pela nota com recência (empate: a mais recente
      primeiro), a 1ª mantém, a 2ª vale PENALIDADE_REPETICAO, a 3ª o quadrado.
    - A 1ª (a que representa o fato) usa a nota do FATO (NOTA_DO_FATO: média
      ou máximo das versões) e ganha BONUS_POR_PORTAL por portal além do
      primeiro, até TETO_BONUS_COBERTURA. Com "propria", nem nota do fato nem
      bônus — o comportamento anterior.
    Notícia sozinha no componente mantém a nota, qualquer que seja a idade.
    ``df`` precisa de ``data``, ``ranking_llm``, ``componente`` e ``link``.
    Retorna (fator_recencia, fator_repeticao, bonus_cobertura, nota_final).
    """
    fator_rec = _fator_recencia(df, "componente")
    com_rec = df["ranking_llm"] * fator_rec
    ordem = (
        df.assign(_r=com_rec)
        .sort_values(["_r", "data"], ascending=[False, False])
        .groupby("componente")
        .cumcount()
    ).reindex(df.index)
    fator_rep = PENALIDADE_REPETICAO ** ordem
    final = com_rec * fator_rep
    bonus = pd.Series(0.0, index=df.index)

    if NOTA_DO_FATO in ("media", "max"):
        grupo = df.groupby("componente")
        nota_fato = grupo["ranking_llm"].transform("mean" if NOTA_DO_FATO == "media" else "max")
        portais = df["link"].map(_portal).groupby(df["componente"]).transform("nunique")
        bonus = ((portais - 1) * BONUS_POR_PORTAL).clip(lower=0, upper=TETO_BONUS_COBERTURA)
        rep = ordem == 0
        final[rep] = nota_fato[rep] * fator_rec[rep] + bonus[rep]
        bonus[~rep] = 0.0

    return fator_rec.round(4), fator_rep.round(4), bonus, final.round()


def _ranquear_setor(client, setor, df_setor):
    """Nota, geografia, assunto, grupos e nota final das notícias de um setor.

    ``df_setor`` precisa de ``titulo``, ``data``, ``link`` e ``texto``; o
    índice é o ID. Retorna um DataFrame só com as notícias que receberam nota,
    com as colunas de auditoria — ou None se nenhuma recebeu.
    """
    if NOTA_COM_LEAD:
        itens = [
            (int(i), r.titulo.strip(), re.sub(r"\s+", " ", str(r.texto or ""))[:CARACTERES_LEAD])
            for i, r in df_setor.iterrows()
            if isinstance(r.titulo, str) and r.titulo.strip()
        ]
    else:
        itens = [
            (int(i), t.strip())
            for i, t in df_setor["titulo"].items()
            if isinstance(t, str) and t.strip()
        ]
    if not itens:
        return None

    resultado = _classificar_setor(client, setor, itens)
    if not resultado:
        return None

    ids = list(resultado)
    sub = df_setor.loc[ids, ["titulo", "data", "link"]].copy()
    sub["ranking_llm"] = [float(resultado[i][0]) for i in ids]
    sub["tag"] = [resultado[i][1] for i in ids]
    sub["assunto"] = [resultado[i][2] for i in ids]

    frequencia = sub["assunto"].value_counts().to_dict()
    grupos = _unificar_assuntos(client, setor, sorted(frequencia), frequencia)
    sub["grupo_assunto"] = sub["assunto"].map(grupos)

    # lista curta de cada geografia ordenada pela nota com recência por assunto
    sub["ranking_rec"] = sub["ranking_llm"] * _fator_recencia(sub, "grupo_assunto")
    grupos_fato = _grupos_mesmo_fato(client, setor, sub)
    sub["grupo_fato"] = [grupos_fato.get(i) for i in sub.index]
    sub["componente"] = _componentes(sub, grupos_fato)
    (
        sub["fator_recencia"],
        sub["fator_repeticao"],
        sub["bonus_cobertura"],
        sub["ranking"],
    ) = _nota_final(sub)

    logging.info(
        f"[MonitoramentoEnvio] '{setor}': {len(grupos_fato)} notícias na lista curta de mesmo fato; "
        f"{int((sub['fator_repeticao'] < 1).sum())} penalizadas por repetição; "
        f"{int((sub['bonus_cobertura'] > 0).sum())} com bônus de cobertura."
    )
    return sub


def task_classificador(engine=None, *, quantos_dias_atras=QUANTOS_DIAS_ATRAS,
                       setores=None, run_id=None):
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)
    setores_param = setores or SETORES

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE noticias.noticias_temp;"))

        query = "SELECT * FROM noticias.noticias_classificadas"
        aux = pd.read_sql(query, con=conn)

    aux["data"] = pd.to_datetime(aux["data"], dayfirst=True, errors="coerce")
    aux = aux.drop_duplicates(subset=["link", "setor"], keep="first")

    data_limite = pd.Timestamp.now().normalize() - pd.Timedelta(days=quantos_dias_atras)
    aux = aux[aux["data"] >= data_limite].reset_index(drop=True)

    for c in ("ranking", "tag", "ranking_llm", "assunto", "grupo_assunto", "grupo_fato",
              "componente", "fator_recencia", "fator_repeticao", "bonus_cobertura"):
        aux[c] = None

    setores_encontrados = [
        s for s in aux["setor"].dropna().unique() if s in setores_param
    ]

    client = _novo_cliente()

    for setor in setores_encontrados:
        try:
            sub = _ranquear_setor(client, setor, aux.loc[aux["setor"] == setor])
            if sub is None:
                continue
            for c in ("ranking_llm", "tag", "assunto", "grupo_assunto", "grupo_fato",
                      "componente", "fator_recencia", "fator_repeticao",
                      "bonus_cobertura", "ranking"):
                aux.loc[sub.index, c] = sub[c]
        except Exception as e:
            logging.error(
                f"[MonitoramentoEnvio] Setor '{setor}' falhou na classificação — ignorando e continuando: {e}"
            )
            continue

    expected_cols = [
        "titulo",
        "link",
        "data",
        "texto",
        "setor",
        "fonte",
        "ranking",
        "tag",
    ]
    for c in expected_cols:
        if c not in aux.columns:
            aux[c] = ""

    aux["data"] = pd.to_datetime(aux["data"], errors="coerce", dayfirst=True)
    aux["data"] = aux["data"].dt.to_pydatetime()
    aux["ranking"] = (
        pd.to_numeric(aux["ranking"], errors="coerce").fillna(0).astype("uint32")
    )
    aux["tag"] = aux["tag"].fillna("").astype(str)

    for c in ["titulo", "link", "texto", "setor", "fonte"]:
        aux[c] = aux[c].fillna("").astype(str)

    aux_insert = aux[expected_cols].copy()

    aux_insert = aux_insert.drop_duplicates(subset=["titulo", "setor"])

    if run_id is not None:
        # O CSV leva também a nota crua do LLM, o assunto, o grupo e o fator de
        # recência — é onde se audita por que uma notícia entrou ou saiu. As
        # tabelas do banco seguem com as mesmas colunas de antes.
        auditoria = aux[
            expected_cols
            + ["ranking_llm", "assunto", "grupo_assunto", "grupo_fato", "componente",
               "fator_recencia", "fator_repeticao", "bonus_cobertura"]
        ]
        try:
            storage.upload_bytes(auditoria.to_csv(index=False).encode("utf-8"),
                                 "noticias_envio", run_id, "scores_classificacao.csv")
        except Exception as e:
            logging.error(
                f"[MonitoramentoEnvio] Falha ao gravar o CSV de auditoria: {e}"
            )

    # Auditoria no banco: noticias_email ganhava uma linha por run sem dizer de
    # qual run — não dava para saber que nota alimentou qual e-mail. Só grava as
    # colunas que existirem na tabela (ALTER TABLE é manual, ver PR).
    aux["run_id"] = run_id
    aux["gerado_em"] = pd.Timestamp.now(tz="America/Sao_Paulo").tz_localize(None)
    with engine.connect() as conn:
        colunas_email = set(
            conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'noticias' AND table_name = 'noticias_email'"
                )
            ).scalars()
        )
    extras = [c for c in COLUNAS_AUDITORIA_EMAIL if c in colunas_email]
    aux_email = aux.loc[aux_insert.index, expected_cols + extras].copy()
    for c in ("ranking_llm", "fator_recencia", "fator_repeticao", "bonus_cobertura"):
        if c in aux_email:
            aux_email[c] = pd.to_numeric(aux_email[c], errors="coerce")

    with engine.begin() as conn:
        aux_email.to_sql(
            name="noticias_email",
            schema="noticias",
            con=conn,
            if_exists="append",
            index=False,
        )

    with engine.begin() as conn:
        aux_insert.to_sql(
            name="noticias_temp",
            schema="noticias",
            con=conn,
            if_exists="append",
            index=False,
        )
