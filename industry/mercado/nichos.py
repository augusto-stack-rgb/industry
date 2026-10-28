"""Nichos de mercado, share de cada fornecedor e prospects (frentes 2 e 3 do plano).

    python -m industry.mercado.nichos            # recalcula tudo e exporta o CSV de prospects

1. **Vendas**: o que foi contratado e quem ganhou.
   - contratos do PNCP (Brasil inteiro) das categorias Serviços e Informática (TIC);
   - vencedores por item do Compras.gov.br em itens de serviço, só das compras que ainda
     não têm contrato coletado (para não contar a mesma venda duas vezes).
   O MVP fica só com serviços e TI: saem saúde, limpeza, vigilância e obra (`_FORA`),
   a não ser que o texto seja de TI (sistema de gestão da saúde fica).
2. **Nichos**: embeddings da descrição (modelo multilíngue local, sem custo de API)
   agrupados por k-means. O nome é formado pelos termos mais característicos do grupo
   (c-TF-IDF). O catálogo CATSER viria pronto, mas veio vazio em todos os itens (27/10).
3. **Mercado por nicho**: valor anualizado (cada fonte pelo número de dias que cobre),
   vendas, fornecedores, HHI e participação dos 4 maiores.
4. **Prospects**: fornecedor (grupo pela raiz do CNPJ) ativo num nicho onde tem share
   pequeno, num nicho grande e não dominado. Cortes do MVP em `CORTES`; ver o TODO.

Tudo vai para o Postgres local (`mercado.nicho`, `mercado.fornecedor_nicho`,
`mercado.prospect`) e o CSV de prospects para `.data/mercado/`.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

from industry.licitacoes.termos import normalizar, termos_encontrados
from industry.shared.connections import MERCADO_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

DADOS = Path(__file__).resolve().parents[2] / ".data" / "mercado"
MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Cortes do MVP (PLANO_PRODUTO.md): serão recalibrados depois de ver a distribuição real.
CORTES = {
    "share_max": 0.02,              # share do fornecedor no nicho abaixo de 2%
    "nicho_min_ano": 5_000_000,     # nicho com mais de R$ 5 mi por ano
    "hhi_max": 2500,                # acima disso o nicho é dominado por poucos
    "demais_ganho_max": 5_000_000,  # porte "Demais" só entra se ganhou menos que isso (não é gigante)
    # ativo de verdade: quem disputa com frequência é quem pagaria por inteligência de licitação
    # (sem isso, 8 mil de 11,7 mil fornecedores viravam prospect, mediana de 1 venda; 28/10/2026)
    "vendas_min": 3,
    "ganho_min_ano": 100_000,
}

_FORA = re.compile(r"limpeza|conservacao|higieniza|jardinag|rocada|dedetiza|desratiza|vigilancia|portaria|"
                   r"medic|hospital|saude|exames?\b|laborat|odonto|enfermag|farmac|cirurg|\bobras?\b|"
                   r"radiolog|radiograf|dialise|nefrolog|clinic|terapia|fisioterap|psicolog|vacina|"
                   r"pavimenta|construcao|reforma|manutencao predial|engenharia civil|recapeamento|"
                   # concessionárias, sem disputa: viravam os maiores nichos com HHI acima de 8.000
                   r"energia eletrica|fornecimento de energia|agua e esgoto|esgotamento sanitario|"
                   r"abastecimento de agua|saneamento|correios|postais|telefonia fixa|despesa referente")
# texto padrão que abre quase todo objeto e só atrapalha o agrupamento
_ABERTURA = re.compile(r"^(?:o objeto .{0,40}? e a |objeto:?\s*)?(?:contratacao|aquisicao|registro de precos?)"
                       r"(?: para (?:futura e eventual|eventual))?(?: contratacao)?(?: de)?(?: empresas?)?"
                       r"(?: especializadas?)?(?: (?:na|para a?|em) (?:prestacao de )?)?(?:servicos? de )?")


def _limpar(t: str) -> str:
    s = normalizar(t)
    s = _ABERTURA.sub("", s.strip())
    return re.sub(r"[^a-z0-9 ]+", " ", s)[:300].strip()


def _no_escopo(t: str) -> bool:
    n = normalizar(t)
    return not _FORA.search(n) or bool(termos_encontrados(t))


def carregar_vendas(eng) -> tuple[pd.DataFrame, dict]:
    with eng.connect() as c:
        dias = dict(c.execute(text("""select fonte, count(distinct dia) from mercado.coleta_log
                                       where status = 'ok' and linhas > 0 group by 1""")).fetchall())
        contratos = pd.read_sql(text("""
            select 'contrato' as fonte, ct.numero_controle_pncp as id, ct.numero_controle_compra as compra,
                   ct.fornecedor_ni as ni, ct.fornecedor_nome as nome, null::text as porte, ct.objeto as descricao,
                   ct.valor_global as valor, ct.uf, ct.orgao_cnpj, null::int as ordem
              from mercado.contrato ct
              left join mercado.contratacao cp on cp.numero_controle_pncp = ct.numero_controle_compra
             where ct.categoria in ('Serviços', 'Informática (TIC)') and ct.fornecedor_tipo_pessoa = 'PJ'
               and coalesce(ct.valor_global, 0) > 0
               and coalesce(cp.modalidade_id, 0) <> 9   -- inexigibilidade: sem disputa"""), c)
        resultados = pd.read_sql(text("""
            select 'resultado' as fonte, r.id_compra_item || '-' || r.sequencial_resultado as id,
                   r.numero_controle_compra as compra, r.fornecedor_ni as ni, r.fornecedor_nome as nome,
                   r.porte_nome as porte, i.descricao, r.valor_total as valor, r.uf, r.orgao_cnpj,
                   r.ordem_classificacao_srp as ordem
              from mercado.resultado r join mercado.item i using (id_compra_item)
             where i.material_ou_servico = 'S' and r.tipo_pessoa = 'PJ' and r.situacao like 'Informado%'
               and coalesce(r.valor_total, 0) > 0
               and not exists (select 1 from mercado.contrato ct where ct.numero_controle_compra = r.numero_controle_compra)"""), c)
    v = pd.concat([x for x in (contratos, resultados) if not x.empty], ignore_index=True)
    v = v[v["descricao"].fillna("").map(_no_escopo)].copy()
    # anualiza pelo período que cada fonte cobre
    cobertura = {"contrato": dias.get("contratos", 0), "resultado": dias.get("resultados", 0)}
    v["valor"] = v["valor"].astype(float)
    v["valor_ano"] = v["valor"] * v["fonte"].map(lambda f: 365 / max(cobertura[f], 1))
    v["raiz"] = v["ni"].str[:8]
    v["texto"] = v["descricao"].fillna("").map(_limpar)
    v = v[v["texto"].str.len() >= 8]
    log.info(f"[nichos] {len(v)} vendas no escopo; cobertura (dias) {cobertura}")
    return v, cobertura


def _embeddings(textos: list[str]) -> np.ndarray:
    """Embeddings com cache em disco por hash do texto (cada rodada só calcula os novos)."""
    DADOS.mkdir(parents=True, exist_ok=True)
    cache_path = DADOS / "embeddings.parquet"
    cache = pd.read_parquet(cache_path) if cache_path.exists() else pd.DataFrame(columns=["h", "v"])
    feitos = dict(zip(cache["h"], cache["v"]))
    hs = [hashlib.md5(t.encode()).hexdigest() for t in textos]
    faltam = [(h, t) for h, t in dict(zip(hs, textos)).items() if h not in feitos]
    if faltam:
        from sentence_transformers import SentenceTransformer
        modelo = SentenceTransformer(MODELO, device="cpu")
        vet = modelo.encode([t for _, t in faltam], batch_size=128, show_progress_bar=True,
                            normalize_embeddings=True)
        novos = pd.DataFrame({"h": [h for h, _ in faltam], "v": list(vet.astype(np.float32))})
        cache = pd.concat([cache, novos], ignore_index=True)
        cache.to_parquet(cache_path, index=False)
        feitos.update(zip(novos["h"], novos["v"]))
    return np.vstack([feitos[h] for h in hs])


def agrupar(v: pd.DataFrame) -> pd.DataFrame:
    from sklearn.cluster import MiniBatchKMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    unicos = v["texto"].drop_duplicates().tolist()
    emb = _embeddings(unicos)
    # ~40 textos distintos por nicho: com 120 saíam 128 nichos misturando locação de veículo e de stand
    k = int(np.clip(len(unicos) / 40, 100, 1000))
    km = MiniBatchKMeans(n_clusters=k, random_state=0, n_init=3, batch_size=4096).fit(emb)
    rotulo = dict(zip(unicos, km.labels_))
    v["nicho"] = v["texto"].map(rotulo)
    # nome: termos mais característicos de cada grupo (c-TF-IDF sobre o texto juntado)
    docs = v.groupby("nicho")["texto"].apply(lambda s: " ".join(s.drop_duplicates()[:400]))
    tf = TfidfVectorizer(ngram_range=(1, 2), max_df=0.3, min_df=2, sublinear_tf=True,
                         stop_words=["de", "da", "do", "das", "dos", "para", "com", "em", "e", "a", "o",
                                     "na", "no", "nas", "nos", "por", "ao", "aos", "as", "os", "um", "uma"])
    m = tf.fit_transform(docs.values)
    termos = np.array(tf.get_feature_names_out())
    nomes = {}
    for i, nid in enumerate(docs.index):
        top = [termos[j] for j in m[i].toarray().ravel().argsort()[::-1][:6]]
        escolhidos = []
        for t in top:   # evita "sistema" e "sistema gestao" juntos
            if not any(t in e or e in t for e in escolhidos):
                escolhidos.append(t)
        nomes[nid] = " / ".join(escolhidos[:3])
    # o c-TF-IDF puxa termos raros ("prótese / cordão / crachá" num grupo de manutenção de
    # cozinha industrial); o LLM nomeia a partir dos textos mais próximos do centro do grupo
    centrais = {}
    dist = km.transform(emb)
    for nid in range(k):
        idx = np.where(km.labels_ == nid)[0]
        if len(idx):
            centrais[nid] = [unicos[i] for i in idx[np.argsort(dist[idx, nid])[:8]]]
    nomes.update(_nomear_llm(centrais))
    v["nicho_nome"] = v["nicho"].map(nomes)
    return v


def _nomear_llm(centrais: dict[int, list[str]], lote: int = 20) -> dict[int, str]:
    """{nicho: nome curto} pelo LLM, em lotes, com cache em disco pelo conteúdo do grupo.
    Sem chave ou com falha, o nicho fica com o nome do c-TF-IDF."""
    import json

    from industry.licitacoes.config import LLM_MODEL
    from industry.shared import llm

    cache_path = DADOS / "nomes_nichos.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    chave = {nid: hashlib.md5("|".join(ex).encode()).hexdigest() for nid, ex in centrais.items()}
    faltam = [nid for nid in centrais if chave[nid] not in cache]
    try:
        cli = llm.cliente(LLM_MODEL, timeout=120) if faltam else None
    except llm.SemChavesValidas:
        cli, faltam = None, []
    sistema = ("Você dá nome a grupos de compras públicas brasileiras. Para cada grupo recebe descrições "
               "de itens ou contratos parecidos. Devolva um nome curto (3 a 6 palavras, português, sem "
               "aspas) que diga O QUE é contratado, como 'Manutenção de ar-condicionado' ou 'Locação de "
               "veículos com motorista'. Responda SOMENTE JSON: {\"nomes\": [{\"id\": N, \"nome\": \"...\"}]}")
    for i in range(0, len(faltam), lote):
        grupo = faltam[i:i + lote]
        pedido = json.dumps([{"id": nid, "descricoes": [d[:120] for d in centrais[nid]]} for nid in grupo],
                            ensure_ascii=False)
        try:
            r = cli.create(messages=[{"role": "system", "content": sistema}, {"role": "user", "content": pedido}],
                           temperature=0, response_format={"type": "json_object"}, reasoning_effort="low",
                           max_completion_tokens=60 * len(grupo) + 200)
            for x in json.loads(r.choices[0].message.content).get("nomes", []):
                if int(x["id"]) in chave and str(x.get("nome", "")).strip():
                    cache[chave[int(x["id"])]] = str(x["nome"]).strip()[:80]
        except Exception as e:  # noqa: BLE001 — fica o nome do c-TF-IDF
            log.warning(f"[nichos] nomes por LLM falharam no lote {i // lote}: {str(e)[:120]}")
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    return {nid: cache[h] for nid, h in chave.items() if h in cache}


def mercado_por_nicho(v: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    fn = (v.groupby(["nicho", "raiz"])
           .agg(valor_ano=("valor_ano", "sum"), vendas=("id", "count"), nome=("nome", "first"),
                ni=("ni", "first"), ufs=("uf", "nunique"), orgaos=("orgao_cnpj", "nunique"),
                atras_srp=("ordem", lambda s: int((s.fillna(1) > 1).sum())))
           .reset_index())
    tot = fn.groupby("nicho")["valor_ano"].transform("sum")
    fn["share"] = fn["valor_ano"] / tot
    nic = (fn.groupby("nicho")
             .agg(valor_ano=("valor_ano", "sum"), fornecedores=("raiz", "nunique"), vendas=("vendas", "sum"),
                  hhi=("share", lambda s: float((s * 100).pow(2).sum())),
                  cr4=("share", lambda s: float(s.nlargest(4).sum())))
             .reset_index())
    nic["nome"] = nic["nicho"].map(v.drop_duplicates("nicho").set_index("nicho")["nicho_nome"])
    nic["ufs"] = nic["nicho"].map(v.groupby("nicho")["uf"].nunique())
    exemplo = v.sort_values("valor", ascending=False).drop_duplicates("nicho").set_index("nicho")["descricao"]
    nic["exemplo"] = nic["nicho"].map(exemplo).str[:200]
    return nic.sort_values("valor_ano", ascending=False), fn


def marcar_prospects(nic: pd.DataFrame, fn: pd.DataFrame, v: pd.DataFrame) -> pd.DataFrame:
    porte = (v.dropna(subset=["porte"]).groupby("raiz")["porte"]
              .agg(lambda s: s.value_counts().index[0]))
    total_forn = v.groupby("raiz").agg(ganho_total_ano=("valor_ano", "sum"), nichos=("nicho", "nunique"),
                                      ufs_total=("uf", "nunique"))
    p = fn.merge(nic[["nicho", "nome", "valor_ano", "hhi", "fornecedores", "ufs"]]
                 .rename(columns={"nome": "nicho_nome", "valor_ano": "nicho_ano", "ufs": "nicho_ufs"}), on="nicho")
    p = p.merge(total_forn, left_on="raiz", right_index=True)
    p["porte"] = p["raiz"].map(porte).fillna("não informado")
    c = CORTES
    ok = ((p["share"] < c["share_max"]) & (p["vendas"] >= c["vendas_min"]) & (p["valor_ano"] >= c["ganho_min_ano"]) & (p["nicho_ano"] > c["nicho_min_ano"]) & (p["hhi"] < c["hhi_max"])
          & ((p["porte"] != "Demais") | (p["ganho_total_ano"] < c["demais_ganho_max"])))
    p = p[ok].copy()
    p["espaco_ano"] = p["nicho_ano"] - p["valor_ano"]
    # dor: ficou atrás no registro de preços, ou atua em poucas UFs de um nicho espalhado
    p["dor"] = 1 + np.minimum(p["atras_srp"], 5) * 0.2 + (p["nicho_ufs"] >= 10) * (p["ufs"] <= 2) * 0.5
    p["prioridade"] = np.log10(p["espaco_ano"]) * np.log1p(p["vendas"]) * p["dor"]
    # uma linha por fornecedor: o nicho onde a prioridade é maior
    p = p.sort_values("prioridade", ascending=False).drop_duplicates("raiz")

    def frase(r):
        return (f"ganhou R$ {r.valor_ano:,.0f}/ano em '{r.nicho_nome}' ({r.vendas} venda(s), {r.ufs} UF); "
                f"o nicho movimenta R$ {r.nicho_ano:,.0f}/ano com {r.fornecedores} fornecedores; "
                f"share {r.share:.2%}").replace(",", ".")
    p["evidencia"] = p.apply(frase, axis=1)
    # o que ele de fato vendeu no nicho: as 2 maiores vendas, para conferir o nicho à mão
    ex = (v.sort_values("valor", ascending=False).drop_duplicates(["raiz", "nicho", "texto"])
           .groupby(["raiz", "nicho"])["descricao"].apply(lambda s: " || ".join(x[:110] for x in s.head(2))))
    p["exemplos"] = [ex.get((r, n), "") for r, n in zip(p["raiz"], p["nicho"])]
    return p


def calcular() -> dict:
    eng = get_sqlalchemy_engine(MERCADO_PG)
    v, cobertura = carregar_vendas(eng)
    v = agrupar(v)
    nic, fn = mercado_por_nicho(v)
    pros = marcar_prospects(nic, fn, v)
    with eng.begin() as c:
        c.execute(text("create schema if not exists mercado"))
    nic.to_sql("nicho", eng, schema="mercado", if_exists="replace", index=False)
    fn.to_sql("fornecedor_nicho", eng, schema="mercado", if_exists="replace", index=False)
    pros.to_sql("prospect", eng, schema="mercado", if_exists="replace", index=False)
    DADOS.mkdir(parents=True, exist_ok=True)
    saida = DADOS / f"prospects_{date.today().isoformat()}.csv"
    pros[["ni", "nome", "porte", "nicho_nome", "valor_ano", "vendas", "share", "nicho_ano", "fornecedores",
          "hhi", "ufs", "nichos", "atras_srp", "prioridade", "evidencia", "exemplos"]].to_csv(saida, index=False, sep=";")
    nic.to_csv(DADOS / f"nichos_{date.today().isoformat()}.csv", index=False, sep=";")
    r = {"vendas": len(v), "nichos": len(nic), "fornecedores": int(fn["raiz"].nunique()),
         "prospects": len(pros), "cobertura_dias": cobertura, "csv": str(saida)}
    log.info(f"[nichos] {r}")
    return r


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    print(calcular())
