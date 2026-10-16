#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Painel de Operações M&A — gerador de relatório (HTML por padrão, PDF opcional).

Estrutura:
  PÁGINA 1  Cabeçalho + KPIs
            · Tabela 1: operações por fonte
            · Tabela 2: resumo YTD (estado | setor | continente | nº | valor)
  PÁGINA 2  Operações do último tri, agrupadas por ESTADO e depois por SETOR,
            com subtotais (nº de transações e valor) no topo de cada bloco.
  PÁGINA 3  YTD — blocos por continente (com bandeiras, Brasil em 1º),
            gráficos de setor e séries mensais.
  PÁGINA 4  Fontes (verso) — nº de operações por fonte.

Paleta e tipografia seguem o design system da documentação do time
(https://datahub.bateleur.com.br/docs/, ``_static/brand.css``): neutro
monocromático quente, Geist / Geist Mono, bordas finas. Ver a seção "Paleta e
tipografia" abaixo.

Para o relatório restrito a uma região (ex.: o PDF "só Brasil" que vai como
segundo anexo do email semanal), filtre com :func:`filtrar_regiao` e passe
``escopo_regiao`` a :func:`construir_contexto` — o resto do relatório é idêntico.

Uso:
    python mna_dashboard.py entrada.xlsx -o painel.html
    python mna_dashboard.py entrada.xlsx -o painel.pdf --format pdf
    python mna_dashboard.py entrada.xlsx --as-of 2026-06-18

Bibliotecas: pandas, matplotlib, openpyxl (obrigatórias); weasyprint (p/ PDF).
"""

from __future__ import annotations

import base64
import io
import re
import unicodedata
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

from industry.noticias.mna.regioes import (  # noqa: F401  (reexport)
    CONTINENTE_BASE,
    CONTINENTE_MERGE,
    CONTINENTE_ORDEM,
    PAIS_CONTINENTE,
    REGIAO_BRASIL,
    continente_de,
    ordenar_continentes,
)

# ════════════════════════════════════════════════════════════════════════
#  CONFIGURAÇÃO
# ════════════════════════════════════════════════════════════════════════

# --- Câmbio: tudo é convertido para esta moeda para poder somar valores. ---
# IMPORTANTE: taxas ILUSTRATIVAS. Atualize com os valores que você usa.
MOEDA_ALVO = "USD"
FX_PARA_USD = {"USD": 1.00, "BRL": 0.18, "EUR": 1.08, "GBP": 1.27}

# --- Janelas de tempo ---
#   "quarter" -> trimestre-calendário corrente até a data de referência
#   "days:N"  -> últimos N dias
#   "month"   -> mês-calendário corrente
ULTIMO_TRI_MODO = "quarter"

# --- Mapeamento de Status -> ESTADO (3 buckets). ---
ESTADO_ORDEM = ["concluído", "em negociação", "perdido"]
ESTADO_DEFAULT = "em negociação"  # para status vazio/desconhecido
# Mostrar no painel apenas operações nestes estados (ex.: só concluídas).
# Para voltar a mostrar tudo, use: ["concluído", "em negociação", "perdido"]
ESTADOS_VISIVEIS = ["concluído", "em negociação"]
ESTADO_MAP = {
    # concluído
    "concluido": "concluído",
    "aprovado": "concluído",
    "vendido": "concluído",
    "fechado": "concluído",
    "finalizado": "concluído",
    "completo": "concluído",
    # em negociação
    "em negociacao": "em negociação",
    "negociacao": "em negociação",
    "anunciado": "em negociação",
    "rumor": "em negociação",
    "planejado": "em negociação",
    "em estudo": "em negociação",
    "em investigacao": "em negociação",
    "oferta nao vinculante": "em negociação",
    "proposta": "em negociação",
    "proposta de parceria": "em negociação",
    "em analise": "em negociação",
    "pendente": "em negociação",
    "proposto": "em negociação",
    # perdido
    "cancelado": "perdido",
    "rejeitado": "perdido",
    "perdida": "perdido",
    "perdido": "perdido",
    "bloqueado": "perdido",
    "abandonado": "perdido",
    "suspenso": "perdido",
    "fracassado": "perdido",
    "encerrado": "perdido",
    "recusada": "perdido",
    "recusado": "perdido",
}
# cor por estado (cabeçalhos/bordas dos blocos)
ESTADO_COR = {"concluído": "#3D7A4E", "em negociação": "#1B4DB1", "perdido": "#A33B3B"}

# --- Continentes/regiões: mapa e ordem vivem em ``regioes.py`` (fonte única,
#     também usada pela DAG de email, que não pode importar matplotlib). ---
TOP_N_SETORES = 10

# --- Notícias analisadas por fonte (total coletado na base de notícias). ---
# Dado externo, informado pelo usuário (não vem do Excel de operações).
# Atualize quando a base de coleta mudar.
NOTICIAS_BASE = {
    "Bloomberg": 3226,
    "infomoney": 2210,
    "Forbes_Money": 668,
    "Money_Times": 1031,
    "NYT_MnA": 94,
    "BrazilJournal_MnA": 223,
    "FusoesAquisicoes": 30,
    "TheGuardian_MnA": 46,
    "InfoMoney_MnA": 36,
    "World_Oil": 379,
    "NeoFeed": 302,
    "forbes": 655,
    "Brazil_Journal_Fintechs": 92,
    "Bloomberg_Linea": 100,
    "Globo_Rural": 1429,
    "Offshore_Energy": 428,
    "Quatro_Rodas_Noticias": 758,
    "Finsiders_Brasil": 175,
    "IndustriaNews": 368,
    "Startups": 210,
    "Autodata": 571,
    "CNN_Money": 398,
    "Portal_Novarejo": 49,
    "Quatro_Rodas_Negocios": 10,
    "Suno": 458,
    "Baguete": 298,
    "Decisor_Brasil": 16,
    "IT_Forum": 427,
    "OilPrice": 984,
    "Revista_Buildings": 34,
    "Brazil_Energy_Insight": 220,
    "Brazil_Journal_Eletrico": 111,
    "Medicina_SA": 216,
    "Panorama_Farmaceutico": 253,
    "SuperHiper": 304,
    "Central_do_Varejo": 288,
    "Exame_Negocios": 38,
    "Olhar_Digital": 649,
    "Petronoticias": 348,
    "Portal_do_Agronegocio": 1032,
    "SC_Builders": 44,
    "TechCrunch": 1849,
    "Acelera_Varejo": 14,
    "Agencia_Infra": 684,
    "Agrofy": 40,
    "Brazil_Journal_Tecnologia": 87,
    "CIMM": 131,
    "CNBC_MnA": 33,
    "Canal_Rural": 605,
    "EconomiaSC_Startups": 37,
    "Eixos": 298,
    "Revista_MT": 118,
}

# ════════════════════════════════════════════════════════════════════════
#  Paleta e tipografia — design system da documentação
#  (https://datahub.bateleur.com.br/docs/, _static/brand.css)
# ════════════════════════════════════════════════════════════════════════
# Tokens LIGHT copiados 1:1 do brand.css: neutro monocromático quente. Só cores
# e fontes mudam em relação à versão anterior do painel — estrutura das páginas,
# tabelas e gráficos é a mesma.
INK = "#1A1A1A"        # --bat-text-primary
INK_SOFT = "#5C5855"   # --bat-text-secondary
INK_FAINT = "#8A8380"  # --bat-text-tertiary
PAPER = "#FAFAFA"      # --bat-bg-primary
SURFACE = "#F5F5F3"    # --bat-bg-secondary
FILL = "#EBEBEA"       # --bat-bg-tertiary
LINE = "#D6D3D2"       # --bat-border
LINE_SOFT = "#E8E8E6"  # --bat-border-subtle
# A documentação é monocromática (link = cor do texto), então o "acento" é a
# própria tinta — não há mais azul no painel.
ACCENT = "#1A1A1A"
VALUE = "#2E2C2B"      # --bat-code-text: valores só destacam por peso, não por cor

# Gráficos são a única exceção ao monocromático: 10 categorias empilhadas em
# tons de cinza ficam ilegíveis. Paleta de baixa saturação, na mesma família
# quente/neutra dos tokens acima.
CAT = ["#2E2C2B", "#6B625B", "#8C7B6A", "#4A5A5E", "#7A6E4E",
       "#5E5A6B", "#3F4A44", "#9A8F84", "#5C4F4A", "#77808A"]

# Pilhas de fonte do brand.css. Geist vem por @import do Google Fonts (o Chrome
# headless que imprime o PDF tem saída de rede); os fallbacks cobrem o caso de
# a fonte não carregar — daí "DejaVu Sans"/"DejaVu Sans Mono", que existem na
# imagem do worker.
FONT_TEXT = ('"Geist",ui-sans-serif,system-ui,-apple-system,"Segoe UI",'
             '"DejaVu Sans","Liberation Sans",Arial,sans-serif')
FONT_MONO = ('"Geist Mono",ui-monospace,"SF Mono","JetBrains Mono",'
             '"DejaVu Sans Mono",Menlo,Consolas,monospace')
RADIUS = "0.5rem"      # --bat-radius
RADIUS_SM = "0.375rem"  # --bat-radius-sm

# Mesma família tipográfica nos rótulos dos gráficos (cai no DejaVu se Geist
# não estiver instalada na imagem — matplotlib não baixa webfont).
plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Geist", "DejaVu Sans", "Liberation Sans", "Arial"]

# ════════════════════════════════════════════════════════════════════════
#  Utilidades de texto / normalização
# ════════════════════════════════════════════════════════════════════════


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _key(s) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return re.sub(r"\s+", " ", _strip_accents(str(s)).lower().replace("_", " ").strip())


# ---- Resolução tolerante de nomes de coluna (evita KeyError se o export muda)
COLUNAS_CANON = {
    "ID Operação": ["id operacao", "id", "id da operacao", "operacao id", "op id"],
    "Empresa Adquirente": ["empresa adquirente", "adquirente", "comprador"],
    "Empresa Alvo": ["empresa alvo", "alvo", "target", "empresa-alvo"],
    "Valor": ["valor", "value", "deal value"],
    "Moeda": ["moeda", "currency"],
    "Setor": ["setor", "sector", "industria", "segmento"],
    "Status": ["status", "situacao", "estagio", "estado"],
    "País Adquirente": ["pais adquirente", "pais do adquirente", "country acquirer"],
    "País Alvo": ["pais alvo", "pais do alvo", "country target"],
    "Descrição": ["descricao", "description", "resumo", "sumario"],
    "Título do Artigo": ["titulo do artigo", "titulo", "title", "titulo artigo"],
    "Link do Artigo": ["link do artigo", "link", "url", "link artigo"],
    "Data do Artigo": [
        "data do artigo",
        "data",
        "date",
        "data da noticia",
        "data publicacao",
        "data de publicacao",
        "data artigo",
    ],
    "Fonte": ["fonte", "source", "veiculo", "fonte artigo"],
    "ZS Score": ["zs score", "score", "zs", "relevancia"],
    "Processado em": ["processado em", "processed at", "computed at"],
}
COLUNAS_OBRIGATORIAS = ["Data do Artigo"]  # sem isto não dá para seguir


def _resolver_colunas(df: pd.DataFrame) -> pd.DataFrame:
    achadas = {_key(c): c for c in df.columns}
    rename = {}
    for canon, aliases in COLUNAS_CANON.items():
        for al in [canon] + aliases:
            if _key(al) in achadas:
                rename[achadas[_key(al)]] = canon
                break
    df = df.rename(columns=rename)
    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise SystemExit(
            f"[erro] Coluna(s) obrigatória(s) não encontrada(s): {faltando}\n"
            f"        Colunas no arquivo: {list(df.columns)}\n"
            f"        Ajuste COLUNAS_CANON no script ou renomeie no Excel."
        )
    # garante presença das demais (vazias se faltarem)
    for c in COLUNAS_CANON:
        if c not in df.columns:
            df[c] = pd.NA
    return df


def _ler_excel(path: str) -> pd.DataFrame:
    """Lê o arquivo (xlsx ou csv) achando a aba e a linha de cabeçalho."""
    if path.lower().endswith((".csv", ".tsv", ".txt")):
        sep = "\t" if path.lower().endswith(".tsv") else None
        df = pd.read_csv(path, sep=sep, engine="python", encoding="utf-8-sig")
        df = df.dropna(how="all")
        return _resolver_colunas(df)
    xls = pd.ExcelFile(path)
    alvo = {a for v in COLUNAS_CANON.values() for a in v} | {_key(k) for k in COLUNAS_CANON}
    melhor, melhor_score = None, -1
    for aba in xls.sheet_names:
        prev = xls.parse(aba, header=None, nrows=12)
        for h in range(min(8, len(prev))):
            cels = [_key(x) for x in prev.iloc[h].tolist()]
            score = sum(1 for c in cels if c in alvo)
            if score > melhor_score:
                melhor, melhor_score = (aba, h), score
    aba, header = melhor
    df = xls.parse(aba, header=header)
    df = df.dropna(how="all")
    return _resolver_colunas(df)


# ---- Setores / continentes / estado
# Setores "crus" -> setores abrangentes. Mapa exato (chave normalizada) para
# casos curtos/ambíguos, depois regras por palavra-chave (1ª que casar vence).
SETOR_EXATO = {
    "aco": "Indústria & Mineração",
    "ti": "Tecnologia",
    "ia": "Tecnologia",
    "cx": "Tecnologia",
    "tissue": "Indústria & Mineração",
    "diversificado": "Outros",
}
SETOR_REGRAS = [
    (("inteligencia artificial",), "Tecnologia"),
    (("militar",), "Defesa & Aeroespacial"),
    (("defesa", "aeroespacial"), "Defesa & Aeroespacial"),
    (("fibra", "transmissao optica"), "Tecnologia"),
    (("fintech", "ativos digitais", "cripto", "recebiveis"), "Fintech"),
    (("jogos", "game"), "Tecnologia"),
    (("dental", "odontolog"), "Saúde"),
    (
        (
            "software",
            "tecnologia",
            "tech",
            "internet service provider",
            "supermercado online",
            "creator economy",
            "gerenciamento de performance",
            "epm",
        ),
        "Tecnologia",
    ),
    (("radiopharma", "farmac", "pharma", "medicament", "vacina"), "Farmacêutico"),
    (("saude", "dental", "oftalmolog", "oncolog", "biotecnolog", "solucoes medicas", "clinic", "hospitalar"), "Saúde"),
    (
        (
            "petrole",
            "oleo",
            "gas",
            "oil",
            "upstream",
            "offshore",
            "lng",
            "etanol",
            "ethanol",
            "eolica",
            "renovavel",
            "utilidade",
            "eletric",
            "power",
            "subsea",
            "petrochemical",
            "petroquimic",
            "energia",
        ),
        "Energia",
    ),
    (
        (
            "banco",
            "bancari",
            "financ",
            "pagamentos",
            "seguro",
            "wealth",
            "asset management",
            "gestao de ativos",
            "mercado de capitais",
        ),
        "Financeiro",
    ),
    (
        (
            "varejo",
            "varejista",
            "calcad",
            "e-commerce",
            "ecommerce",
            "comercio eletronico",
            "consumo",
            "cosmetic",
            "fragrancia",
            "moda",
            "vestuario",
        ),
        "Varejo",
    ),
    (("aliment", "bebida", "entrega de comida", "entregas", "distribuicao de alimentos"), "Alimentos & Bebidas"),
    (("agro",), "Agro"),
    (("telecom", "telefon"), "Telecom"),
    (
        ("midia", "entretenimento", "publicidade", "jornal", "televisao", "musica", "streaming"),
        "Mídia & Entretenimento",
    ),
    (("hotel", "hospitalidade", "hotelaria", "viagens", "turismo"), "Turismo & Hotelaria"),
    (("golfe", "esporte", "lazer", "casino", "loteria"), "Esportes & Lazer"),
    (
        ("logistic", "transporte", "porto", "terminal", "aeroporto", "aviacao", "rodovia", "ferrovia", "maritim"),
        "Logística & Transporte",
    ),
    (("imovel", "imovei", "imobiliario", "real estate", "reit", "habitacao"), "Imobiliário"),
    (("construcao", "cimento", "engenharia"), "Construção"),
    (("saneamento", "agua", "reciclagem", "residuos"), "Saneamento"),
    (("educacao", "ensino"), "Educação"),
    (
        (
            "quimic",
            "mineracao",
            "minerio",
            "industria",
            "manufatura",
            "ceramica",
            "elevador",
            "siderurgia",
            "componentes",
            "locacao de maquinas",
            "equipamentos",
        ),
        "Indústria & Mineração",
    ),
    (("servico", "consultoria", "laboratorio", "teste"), "Serviços"),
]


def canon_setor(raw) -> str:
    k = _key(raw)
    if not k:
        return "Não informado"
    if k in SETOR_EXATO:
        return SETOR_EXATO[k]
    for chaves, label in SETOR_REGRAS:
        if any(c in k for c in chaves):
            return label
    return "Outros"


def map_estado(raw) -> str:
    return ESTADO_MAP.get(_key(raw), ESTADO_DEFAULT)


# Tipo de operação não existe na base; é inferido do texto. Padrão: Aquisição
# (a própria estrutura adquirente -> alvo indica aquisição). Marca "Fusão"
# apenas quando o texto sinaliza fusão de fato.
_FUSAO_KW = (
    "fusao",
    "fusoes",
    "fundir",
    "fundiu",
    "fundiram",
    "fundindo",
    "se fundem",
    "se fundir",
    "fusao entre",
    "merger of equals",
    "combinacao de negocios",
    "business combination",
    "merge of equals",
)


def tipo_operacao(*textos) -> str:
    t = _key(" ".join(str(x) for x in textos if x is not None and not (isinstance(x, float) and pd.isna(x))))
    return "Fusão" if any(k in t for k in _FUSAO_KW) else "Aquisição"


# ════════════════════════════════════════════════════════════════════════
#  Parsing de valor a partir da descrição
# ════════════════════════════════════════════════════════════════════════
_ESCALA = {"mil": 1e3, "milhao": 1e6, "milhoes": 1e6, "bilhao": 1e9, "bilhoes": 1e9, "trilhao": 1e12, "trilhoes": 1e12}
_RE_NUM = r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d+)?|\d+(?:[.,]\d+)?)"
_RE_VALOR = re.compile(_RE_NUM + r"\s*(?:de\s+)?(milh[õo]es?|bilh[õo]es?|trilh[õo]es?|mil)\b", re.IGNORECASE)


def _num_ptbr(s: str) -> float:
    s = s.strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") >= 1 and len(s.rsplit(".", 1)[1]) == 3:
        s = s.replace(".", "")
    return float(s)


def _moeda_no_texto(t: str):
    tl = _key(t)
    if "r$" in t.lower() or "reais" in tl or " real" in tl:
        return "BRL"
    if "us$" in t.lower() or "dolar" in tl or "dollar" in tl:
        return "USD"
    if "€" in t or "euro" in tl:
        return "EUR"
    if "£" in t or "libra" in tl:
        return "GBP"
    if "$" in t:
        return "USD"
    return None


def parse_valor(descricao, moeda_col):
    if descricao is None or (isinstance(descricao, float) and pd.isna(descricao)):
        return None, None
    texto = str(descricao)
    m = _RE_VALOR.search(texto)
    if not m:
        return None, None
    try:
        base = _num_ptbr(m.group(1))
    except ValueError:
        return None, None
    escala = _ESCALA.get(_strip_accents(m.group(2).lower()), 1.0)
    moeda = _moeda_no_texto(texto) or (str(moeda_col).strip().upper() if pd.notna(moeda_col) else None)
    if moeda not in FX_PARA_USD:
        moeda = MOEDA_ALVO
    return base * escala, moeda


def para_moeda_alvo(valor, moeda):
    if valor is None or moeda is None:
        return None
    return valor * FX_PARA_USD.get(moeda, 1.0) / FX_PARA_USD[MOEDA_ALVO]


# ════════════════════════════════════════════════════════════════════════
#  Carga
# ════════════════════════════════════════════════════════════════════════


def carregar(path: str) -> pd.DataFrame:
    df = _ler_excel(path)
    df["_data"] = pd.to_datetime(df["Data do Artigo"], errors="coerce")
    df = df[df["_data"].notna()].copy()
    parsed = df.apply(lambda r: parse_valor(r.get("Descrição"), r.get("Moeda")), axis=1)
    df["_valor"] = [para_moeda_alvo(v, m) for v, m in parsed]
    df["_tem_valor"] = df["_valor"].notna()
    df["_setor"] = df["Setor"].apply(canon_setor)
    df["_estado"] = df["Status"].apply(map_estado)
    df["_tipo"] = df["Status"].fillna("não informado").astype(str).str.strip().str.lower().replace("", "não informado")
    df["_tipo_op"] = df.apply(lambda r: tipo_operacao(r.get("Descrição"), r.get("Título do Artigo")), axis=1)

    base_col = "País Alvo" if CONTINENTE_BASE == "alvo" else "País Adquirente"
    alt_col = "País Adquirente" if CONTINENTE_BASE == "alvo" else "País Alvo"
    cont = df[base_col].apply(continente_de)
    cont_alt = df[alt_col].apply(continente_de)
    cont = [c if c != "Não informado" else a for c, a in zip(cont, cont_alt)]
    df["_continente"] = [CONTINENTE_MERGE.get(c, c) for c in cont]

    df["_maj"] = df["Empresa Adquirente"].fillna("—").astype(str).str.strip()
    df["_min"] = df["Empresa Alvo"].fillna("—").astype(str).str.strip()
    df["_link"] = df["Link do Artigo"].fillna("").astype(str).str.strip()
    return df


def carregar_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    """Prepara um DataFrame de operações (ex.: da tabela noticias.mna_operations).

    Igual a :func:`carregar`, mas recebe um DataFrame em vez de ler um arquivo.
    Diferença central: usa a coluna numérica ``Valor`` já extraída pelo pipeline
    (convertida à moeda-alvo pela ``Moeda``); só cai no parsing da descrição
    quando ``Valor`` está ausente.
    """
    df = _resolver_colunas(raw.copy())
    df["_data"] = pd.to_datetime(df["Data do Artigo"], errors="coerce")
    df = df[df["_data"].notna()].copy()

    valor_num = pd.to_numeric(df["Valor"], errors="coerce")
    parsed = df.apply(lambda r: parse_valor(r.get("Descrição"), r.get("Moeda")), axis=1)
    valores = []
    for i, (_, r) in enumerate(df.iterrows()):
        v = valor_num.iloc[i]
        if pd.notna(v):
            valores.append(para_moeda_alvo(float(v), r.get("Moeda")))
        else:
            pv, pm = parsed.iloc[i]
            valores.append(para_moeda_alvo(pv, pm))
    df["_valor"] = valores
    df["_tem_valor"] = df["_valor"].notna()

    df["_setor"] = df["Setor"].apply(canon_setor)
    df["_estado"] = df["Status"].apply(map_estado)
    df["_tipo"] = df["Status"].fillna("não informado").astype(str).str.strip().str.lower().replace("", "não informado")
    df["_tipo_op"] = df.apply(lambda r: tipo_operacao(r.get("Descrição"), r.get("Título do Artigo")), axis=1)

    base_col = "País Alvo" if CONTINENTE_BASE == "alvo" else "País Adquirente"
    alt_col = "País Adquirente" if CONTINENTE_BASE == "alvo" else "País Alvo"
    cont = df[base_col].apply(continente_de)
    cont_alt = df[alt_col].apply(continente_de)
    cont = [c if c != "Não informado" else a for c, a in zip(cont, cont_alt)]
    df["_continente"] = [CONTINENTE_MERGE.get(c, c) for c in cont]

    df["_maj"] = df["Empresa Adquirente"].fillna("—").astype(str).str.strip()
    df["_min"] = df["Empresa Alvo"].fillna("—").astype(str).str.strip()
    df["_link"] = df["Link do Artigo"].fillna("").astype(str).str.strip()
    return df


# ════════════════════════════════════════════════════════════════════════
#  Formatação
# ════════════════════════════════════════════════════════════════════════
_SIMB = {"USD": "US$", "BRL": "R$", "EUR": "€", "GBP": "£"}


def fmt_valor(v) -> str:
    if v is None or pd.isna(v) or v == 0:
        return "—"
    s = _SIMB.get(MOEDA_ALVO, "")
    a = abs(v)
    if a >= 1e9:
        return f"{s}{v/1e9:,.1f}".replace(".", "X").replace(",", ".").replace("X", ",") + " bi"
    if a >= 1e6:
        return f"{s}{v/1e6:,.0f}".replace(",", ".") + " mi"
    if a >= 1e3:
        return f"{s}{v/1e3:,.0f}".replace(",", ".") + " mil"
    return f"{s}{v:,.0f}".replace(",", ".")


def fmt_int(n) -> str:
    return f"{int(n):,}".replace(",", ".")


def ordenar_estados(presentes) -> list:
    p = set(presentes)
    return [e for e in ESTADO_ORDEM if e in p] + [e for e in p if e not in ESTADO_ORDEM]


# ════════════════════════════════════════════════════════════════════════
#  Gráficos
# ════════════════════════════════════════════════════════════════════════


def _setup_ax(ax):
    ax.set_facecolor("none")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(LINE)
    ax.tick_params(colors=INK_SOFT, labelsize=9, length=0)
    ax.set_axisbelow(True)


def _b64(fig, tight=True) -> str:
    buf = io.BytesIO()
    if tight:
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", transparent=True)
    else:
        fig.savefig(buf, format="png", dpi=150, transparent=True)
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def chart_barras_h(labels, valores, cor=ACCENT, fmt=fmt_int, w=6.2, h=None):
    h = h or max(1.6, 0.42 * len(labels) + 0.5)
    fig, ax = plt.subplots(figsize=(w, h))
    _setup_ax(ax)
    ax.grid(axis="x", color=LINE, linewidth=0.7, alpha=0.7)
    y = range(len(labels))
    ax.barh(list(y), valores, color=cor, height=0.62, zorder=3)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, color=INK, fontsize=9.5)
    ax.invert_yaxis()
    vmax = max(valores) if len(valores) and max(valores) else 1
    for i, v in enumerate(valores):
        ax.text(v + vmax * 0.015, i, fmt(v), va="center", ha="left", fontsize=8.6, color=INK_SOFT)
    ax.set_xlim(0, vmax * 1.18)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(4))
    return _b64(fig)


def chart_serie_mensal(meses, valores):
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.30)
    _setup_ax(ax)
    ax.grid(axis="y", color=LINE, linewidth=0.7, alpha=0.7)
    x = range(len(meses))
    # Barra em cinza médio (não na tinta): a linha por cima é que usa a tinta,
    # e com a paleta monocromática as duas na mesma cor se anulariam.
    ax.bar(list(x), valores, color=CAT[1], width=0.6, zorder=3)
    ax.plot(list(x), valores, color=INK, linewidth=1.4, marker="o", markersize=4, zorder=4)
    mx = max(valores) if valores else 1
    for i, v in enumerate(valores):
        ax.text(i, v + mx * 0.03, fmt_int(v), ha="center", fontsize=8.4, color=INK)
    ax.set_xticks(list(x))
    ax.set_xticklabels(meses, fontsize=8.6, color=INK_SOFT)
    ax.set_ylim(0, mx * 1.2)
    return _b64(fig, tight=False)


def chart_mensal_setor(meses, setores, matriz):
    fig, ax = plt.subplots(figsize=(6.4, 2.9))
    fig.subplots_adjust(left=0.10, right=0.97, top=0.90, bottom=0.30)
    _setup_ax(ax)
    ax.grid(axis="y", color=LINE, linewidth=0.7, alpha=0.7)
    x = range(len(meses))
    base = [0] * len(meses)
    for i, s in enumerate(setores):
        vals = matriz[s]
        ax.bar(list(x), vals, bottom=base, color=CAT[i % len(CAT)], width=0.62, label=s, zorder=3)
        base = [b + v for b, v in zip(base, vals)]
    ax.set_xticks(list(x))
    ax.set_xticklabels(meses, fontsize=8.6, color=INK_SOFT)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        fontsize=7.6,
        ncol=3,
        frameon=False,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.0),
        labelcolor=INK,
    )
    return _b64(fig, tight=False)


def chart_donut_setor(labels, valores, total=None):
    fig, ax = plt.subplots(figsize=(4.4, 3.0))
    cores = [CAT[i % len(CAT)] for i in range(len(labels))]
    tot = total or sum(valores)
    wedges, _ = ax.pie(
        valores,
        colors=cores,
        startangle=90,
        counterclock=False,
        wedgeprops=dict(width=0.42, edgecolor=PAPER, linewidth=1.5),
    )
    ax.set(aspect="equal")
    ax.text(0, 0.12, fmt_int(tot), ha="center", va="center", fontsize=18, fontweight="bold", color=INK)
    ax.text(0, -0.18, "operações", ha="center", va="center", fontsize=8.5, color=INK_SOFT)
    leg = [f"{l} · {v}" for l, v in zip(labels, valores)]
    ax.legend(wedges, leg, fontsize=7.4, frameon=False, labelcolor=INK, loc="center left", bbox_to_anchor=(1.0, 0.5))
    return _b64(fig, tight=True)


# ════════════════════════════════════════════════════════════════════════
#  Contexto
# ════════════════════════════════════════════════════════════════════════
_MES_PT = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]


def janela_ultimo_tri(asof):
    if ULTIMO_TRI_MODO == "ytd":
        return pd.Timestamp(asof.year, 1, 1), f"YTD {asof.year}"
    if ULTIMO_TRI_MODO == "month":
        return pd.Timestamp(asof.year, asof.month, 1), f"{_MES_PT[asof.month-1]}/{asof.year}"
    if ULTIMO_TRI_MODO.startswith("days:"):
        n = int(ULTIMO_TRI_MODO.split(":")[1])
        return asof - pd.Timedelta(days=n), f"últimos {n} dias"
    q = (asof.month - 1) // 3
    return pd.Timestamp(asof.year, q * 3 + 1, 1), f"{q+1}º tri/{asof.year}"


def filtrar_regiao(df, regiao: str):
    """Recorta o DataFrame já preparado para uma única região (``_continente``).

    Usado pela variante "só Brasil" do PDF. Recebe a saída de
    ``carregar_dataframe`` para o filtro cair sobre a mesma normalização de país
    que o painel completo usa (inclusive o fallback alvo → adquirente).
    """
    return df[df["_continente"] == regiao].copy()


def construir_contexto(df, asof, escopo_regiao: str | None = None):
    """Monta o contexto do relatório.

    Args:
        df: operações já preparadas por ``carregar_dataframe``/``carregar``.
        asof: data de referência. Nas variantes por região, passe a MESMA ``asof``
            do painel completo — senão as duas versões cobrem janelas diferentes
            e param de ser comparáveis.
        escopo_regiao: quando preenchido, só rotula o relatório como restrito a
            essa região (o filtro em si é do chamador, via ``filtrar_regiao``).
            Sem isso os dois PDFs sairiam visualmente idênticos.
    """
    df = df[df["_estado"].isin(ESTADOS_VISIVEIS)].copy()
    ini_tri, rotulo_tri = janela_ultimo_tri(asof)
    df_tri = df[(df["_data"] >= ini_tri) & (df["_data"] <= asof)].copy()

    if ULTIMO_TRI_MODO == "ytd":
        kpi_op_label = "Operações YTD"
        kpi_val_label = "Valor YTD"
        resumo_titulo = "Resumo YTD"
        total_label = "Total · YTD"
        panorama_titulo = "YTD · panorama"
    else:
        kpi_op_label = "Operações (últimos 3 meses)"
        kpi_val_label = "Valor (últimos 3 meses)"
        resumo_titulo = "Resumo últimos 3 meses"
        total_label = "Total · últimos 3 meses"
        panorama_titulo = "Últimos 3 meses · panorama"

    # ---- Resumo por setor (último tri): estados visíveis combinados ----
    def _resumo_setor(dfx):
        g = (
            dfx.groupby("_setor")
            .agg(n=("ID Operação", "size"), valor=("_valor", "sum"))
            .reset_index()
            .sort_values(["valor", "n"], ascending=False)
        )
        linhas = [{"setor": r["_setor"], "n": int(r["n"]), "valor": fmt_valor(r["valor"])} for _, r in g.iterrows()]
        return {"n": len(dfx), "valor": fmt_valor(dfx["_valor"].sum()), "linhas": linhas}

    tri_resumo = _resumo_setor(df_tri)

    # ---- Operações do último tri: agrupadas só por setor (sem estado) ----
    setores_tri = []
    ordem_set = df_tri.groupby("_setor")["_valor"].sum().sort_values(ascending=False).index.tolist()
    for s in df_tri.groupby("_setor").size().sort_values(ascending=False).index:
        if s not in ordem_set:
            ordem_set.append(s)
    for s in ordem_set:
        ssub = df_tri[df_tri["_setor"] == s].sort_values("_valor", ascending=False, na_position="last")
        deals = [
            {
                "maj": r["_maj"],
                "min": r["_min"],
                "tipo": r["_tipo_op"],
                "valor": fmt_valor(r["_valor"]),
                "continente": r["_continente"],
                "link": r["_link"],
            }
            for _, r in ssub.iterrows()
        ]
        setores_tri.append({"setor": s, "n": len(ssub), "valor": fmt_valor(ssub["_valor"].sum()), "deals": deals})
    tri_ops = {"n": len(df_tri), "valor": fmt_valor(df_tri["_valor"].sum()), "setores": setores_tri}

    # ---- Gráficos do ÚLTIMO TRI (continentes, setor e séries mensais) ----
    # blocos por continente (último tri, sem bandeiras)
    g_cont = df_tri.groupby("_continente").agg(n=("ID Operação", "size"), valor=("_valor", "sum"))
    cont_cards = []
    for c in ordenar_continentes(df_tri["_continente"].unique()):
        if c not in g_cont.index:
            continue
        cont_cards.append({"nome": c, "n": int(g_cont.loc[c, "n"]), "valor": fmt_valor(g_cont.loc[c, "valor"])})

    # operações por setor (último tri) — barras + donut
    g_set = df_tri.groupby("_setor").agg(n=("ID Operação", "size"), valor=("_valor", "sum")).reset_index()
    top_n = g_set.sort_values("n", ascending=False).head(TOP_N_SETORES)
    chart_set_n = chart_barras_h(top_n["_setor"].tolist(), top_n["n"].tolist(), ACCENT, fmt_int, w=5.4)

    # donut de operações por setor (top 7 + Outros), escopo TRI
    gd = g_set.sort_values("n", ascending=False)
    top7 = gd.head(7)
    resto = int(gd["n"][7:].sum())
    labels = top7["_setor"].tolist()
    valores = top7["n"].tolist()
    if resto > 0:
        labels.append("Outros")
        valores.append(resto)
    chart_donut = chart_donut_setor(labels, valores, total=len(df_tri))

    # ---- Fontes (verso): operações por fonte (base completa) ----
    fontes = df.groupby("Fonte").size().sort_values(ascending=False)
    tab_fontes = [{"fonte": f, "operacoes": int(n), "noticias": NOTICIAS_BASE.get(f)} for f, n in fontes.items()]
    top_f = fontes.head(15)
    chart_fontes = chart_barras_h(
        top_f.index.tolist(), top_f.values.tolist(), ACCENT, fmt_int, w=6.6, h=0.32 * len(top_f) + 0.5
    )

    return {
        "gerado_em": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "asof": asof.strftime("%d/%m/%Y"),
        "escopo_regiao": escopo_regiao,
        "rotulo_tri": rotulo_tri,
        "ini_tri": ini_tri.strftime("%d/%m/%Y"),
        "moeda": MOEDA_ALVO,
        "kpi_op_label": kpi_op_label,
        "kpi_val_label": kpi_val_label,
        "resumo_titulo": resumo_titulo,
        "total_label": total_label,
        "panorama_titulo": panorama_titulo,
        "kpi": {
            "tri_n": len(df_tri),
            "val": fmt_valor(df_tri["_valor"].sum()),
            "cobertura": f"{df_tri['_tem_valor'].mean()*100:.0f}%",
            "total_base": len(df),
        },
        "tab_fontes": tab_fontes,
        "tri_resumo": tri_resumo,
        "tri_ops": tri_ops,
        "cont_cards": cont_cards,
        "chart_set_n": chart_set_n,
        "chart_donut": chart_donut,
        "chart_fontes": chart_fontes,
        "n_fontes": len(fontes),
    }


# ════════════════════════════════════════════════════════════════════════
#  HTML
# ════════════════════════════════════════════════════════════════════════


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


CSS = (
    """
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700;800&family=Geist+Mono:wght@400;500;600&display=swap');
:root{--ink:@INK@;--soft:@SOFT@;--faint:@FAINT@;--paper:@PAPER@;
  --surface:@SURFACE@;--fill:@FILL@;--line:@LINE@;--line-soft:@LINE_SOFT@;
  --accent:@ACCENT@;--value:@VALUE@;
  --font-text:@FONT_TEXT@;--font-mono:@FONT_MONO@;
  --radius:@RADIUS@;--radius-sm:@RADIUS_SM@}
*{box-sizing:border-box}
html,body{margin:0;padding:0;max-width:100%;overflow-x:hidden}
body{background:var(--fill);color:var(--soft);
  font-family:var(--font-text);
  font-size:13px;line-height:1.45;-webkit-font-smoothing:antialiased;
  text-rendering:optimizeLegibility;font-variant-numeric:tabular-nums}
.page{width:100%;max-width:210mm;min-height:297mm;margin:14px auto;padding:5% 5% 4%;
  background:var(--paper);position:relative;box-shadow:0 1px 3px rgba(26,26,26,.08)}
/* Tabelas largas (operações) rolam DENTRO do próprio bloco, não na página. */
.bloco{overflow-x:auto}
table.deals{min-width:520px}
img{max-width:100%;height:auto}
/* Classe herdada do layout: a documentação não usa serifada, então "serif"
   passou a ser a fonte de títulos (Geist, tracking negativo). */
.serif{font-family:var(--font-text);letter-spacing:-.02em}

.masthead{display:flex;justify-content:space-between;align-items:flex-end;
  border-bottom:1px solid var(--line);padding-bottom:10px;margin-bottom:18px}
.masthead h1{font-family:var(--font-text);font-size:23px;font-weight:600;margin:0;
  letter-spacing:-.02em;color:var(--ink)}
.masthead .sub{font-family:var(--font-mono);color:var(--faint);font-size:10px;
  margin-top:5px;text-transform:uppercase;letter-spacing:.16em;font-weight:500}
.masthead .meta{text-align:right;font-size:10.5px;color:var(--faint);line-height:1.5}
.masthead .meta b{color:var(--ink);font-weight:500}

.eyebrow{display:flex;align-items:baseline;gap:10px;margin:22px 0 10px}
.eyebrow .code{font-family:var(--font-mono);font-size:11px;color:var(--faint);
  font-weight:500;letter-spacing:.06em}
/* Título de seção fica na tinta (como os h2 da documentação); os rótulos
   menores (h3.sec, .chart h3) é que usam o cinza claro dos "captions". */
.eyebrow h2{font-family:var(--font-mono);font-size:11.5px;margin:0;
  text-transform:uppercase;letter-spacing:.16em;font-weight:500;color:var(--ink)}
.eyebrow .rule{flex:1;height:1px;background:var(--line-soft)}
.eyebrow .tag{font-size:10.5px;color:var(--faint);white-space:nowrap}

.kpis{display:flex;border:1px solid var(--line-soft);border-radius:var(--radius);
  overflow:hidden;margin-bottom:4px;background:var(--surface)}
.kpis .k{flex:1;padding:12px 14px;border-right:1px solid var(--line-soft)}
.kpis .k:last-child{border-right:0}
.kpis .k .lab{font-family:var(--font-mono);font-size:9.5px;text-transform:uppercase;
  letter-spacing:.14em;color:var(--faint);font-weight:500}
.kpis .k .num{font-family:var(--font-text);font-size:24px;font-weight:600;
  margin-top:4px;line-height:1;color:var(--ink);letter-spacing:-.02em}
.kpis .k.val .num{color:var(--ink)}

.cols2{display:flex;gap:22px;align-items:flex-start}
.cols2>div{flex:1;min-width:0}
.cols2>div:first-child{flex:1.2}
.split2{display:flex;gap:22px;align-items:flex-start}
.split2>div{flex:1;min-width:0}

table.grid{width:100%;border-collapse:collapse;font-size:11px}
table.grid th{text-align:left;font-family:var(--font-mono);font-size:9.5px;
  text-transform:uppercase;letter-spacing:.12em;color:var(--faint);font-weight:500;
  padding:6px 8px;background:var(--fill);border-bottom:1px solid var(--line)}
table.grid td{padding:4px 8px;border-bottom:1px solid var(--line-soft);
  vertical-align:top;color:var(--soft)}
table.grid td.n,table.grid td.val{text-align:right;white-space:nowrap}
table.grid td.val{color:var(--value);font-weight:600}
table.grid td.n{font-family:var(--font-text);color:var(--ink)}
table.grid td.tp{color:var(--faint);text-transform:capitalize}
table.grid tr.setorhd td{background:var(--ink);color:var(--paper);font-weight:600;
  font-size:11px;letter-spacing:.02em;padding:5px 8px}
table.grid tr.setorhd .sub{float:right;font-weight:500;opacity:.85}
table.grid .badge{display:inline-block;width:8px;height:8px;border-radius:2px;
  margin-right:6px;vertical-align:middle}
table.grid tr.estadohd td{background:var(--fill);font-weight:600;
  text-transform:uppercase;letter-spacing:.1em;font-size:10px;
  font-family:var(--font-mono);color:var(--ink);
  border-top:1px solid var(--line);border-bottom:1px solid var(--line-soft)}
table.grid td.ind{padding-left:22px}

/* Resumo último trimestre: setor compartilhado, 2 estados lado a lado */
table.grid.pareada th.esttop{text-align:left;border-bottom:1px solid var(--line);
  color:var(--ink);padding-bottom:4px}
table.grid.pareada th.esttop .badge{display:inline-block;width:8px;height:8px;
  border-radius:2px;margin-right:5px;vertical-align:middle}
table.grid.pareada th.esttop .sub{font-weight:500;color:var(--faint);
  font-family:var(--font-text);text-transform:none;letter-spacing:0;font-size:9px}
table.grid.pareada th.sep,table.grid.pareada td.sep{border-left:1px solid var(--line-soft)}
table.grid.pareada td.sep{padding-left:14px}
table.grid.pareada th.sep.esttop{padding-left:14px}

/* Tabela maior agrupada */
.bloco{border:1px solid var(--line-soft);border-radius:var(--radius);
  overflow:hidden;margin-bottom:12px}
.bloco .estado-h{background:var(--ink);color:var(--paper);padding:8px 12px;
  display:flex;justify-content:space-between;align-items:baseline}
.bloco .estado-h .nm{font-family:var(--font-mono);font-weight:500;
  text-transform:uppercase;letter-spacing:.14em;font-size:11px}
.bloco .estado-h .tot{font-size:12px;opacity:.85}
.bloco .total-h{background:var(--ink);color:var(--paper);padding:8px 12px;
  display:flex;justify-content:space-between;align-items:baseline}
.bloco .total-h .nm{font-family:var(--font-mono);font-weight:500;
  text-transform:uppercase;letter-spacing:.14em;font-size:11px}
.bloco .total-h .tot{font-size:12px;opacity:.85}
table.grid tr.totrow td{background:var(--fill);font-weight:600;color:var(--ink);
  border-top:1px solid var(--line);border-bottom:1px solid var(--line-soft)}
.setor-h{display:flex;justify-content:space-between;align-items:baseline;
  padding:6px 12px;background:var(--surface);
  border-bottom:1px solid var(--line-soft);border-top:1px solid var(--line-soft)}
.setor-h .nm{font-weight:600;font-size:11px;color:var(--ink)}
.setor-h .tot{font-size:10.5px;color:var(--faint)}
.setor-h .tot b{color:var(--value)}
table.deals{width:100%;border-collapse:collapse;font-size:10.5px}
table.deals td{padding:4px 12px;border-bottom:1px solid var(--line-soft);
  vertical-align:top;color:var(--soft)}
table.deals.ops thead th{text-align:left;font-family:var(--font-mono);font-size:9px;
  text-transform:uppercase;letter-spacing:.12em;color:var(--faint);font-weight:500;
  padding:6px 12px;border-bottom:1px solid var(--line);background:var(--fill)}
table.deals.ops thead th.val{text-align:right}
table.deals tr.setorrow td{background:var(--surface);padding:5px 12px;
  border-bottom:1px solid var(--line-soft);border-top:1px solid var(--line-soft)}
table.deals tr.setorrow .nm{font-weight:600;font-size:10.5px;color:var(--ink)}
table.deals tr.setorrow .tot{float:right;font-size:10px;color:var(--faint)}
table.deals tr.setorrow .tot b{color:var(--value)}
table.deals td.maj{font-weight:600;color:var(--ink);width:22%;overflow-wrap:break-word}
table.deals td.min{width:26%;color:var(--soft);overflow-wrap:break-word}
table.deals td.tp{width:14%;color:var(--faint);text-transform:capitalize}
table.deals td.cont{width:14%;color:var(--faint)}
table.deals td.val{width:16%;text-align:right;color:var(--value);font-weight:600;
  white-space:nowrap}
table.deals td.lnk{width:8%;text-align:center}
table.deals td.lnk a{display:inline-block;width:17px;height:17px;line-height:15px;
  text-align:center;border:1px solid var(--line);border-radius:var(--radius-sm);
  background:var(--surface);color:var(--soft);font-size:11px;text-decoration:none;
  font-weight:500}
table.deals td.lnk a:hover{background:var(--ink);border-color:var(--ink);color:var(--paper)}

/* Cards de continente — uma linha só */
.cont-row{display:flex;gap:8px;margin-bottom:6px}
.cont-card{flex:1;min-width:0;border:1px solid var(--line-soft);
  border-radius:var(--radius-sm);background:var(--surface);padding:9px 10px}
.cont-card .nome{font-family:var(--font-mono);font-size:9px;text-transform:uppercase;
  letter-spacing:.12em;color:var(--faint);font-weight:500;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cont-card .n{font-size:19px;font-weight:600;line-height:1.15;color:var(--ink);
  letter-spacing:-.02em;margin-top:2px}
.cont-card .n small{font-size:10px;color:var(--faint);font-weight:400;letter-spacing:0}
.cont-card .v{font-size:10px;color:var(--value);font-weight:600}

.two{display:flex;gap:18px;margin-top:6px;align-items:flex-start}
.two>.chart{flex:1;min-width:0}
.chart h3{font-family:var(--font-mono);font-size:9.5px;text-transform:uppercase;
  letter-spacing:.14em;color:var(--faint);margin:0 0 6px;font-weight:500}
.chart img{width:100%;height:auto;display:block}
.note{font-size:10px;color:var(--faint);margin-top:4px;font-style:italic}
h3.sec{font-family:var(--font-mono);font-size:9.5px;text-transform:uppercase;
  letter-spacing:.14em;color:var(--faint);margin:0 0 8px;font-weight:500}
/* Rótulos de seção da documentação levam "/ " antes do texto. */
.eyebrow h2::before,h3.sec::before{content:"/ ";opacity:.55}

.foot{position:absolute;bottom:10mm;left:16mm;right:16mm;display:flex;
  justify-content:space-between;font-family:var(--font-mono);font-size:8.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--faint);
  border-top:1px solid var(--line-soft);padding-top:6px}

@page{size:A4;margin:15mm 14mm 16mm;
  @bottom-left{content:"Painel de Operações M&A";font-family:@FONT_MONO@;
    font-size:8pt;letter-spacing:.1em;color:@FAINT@}
  @bottom-right{content:"Página " counter(page) " / " counter(pages);
    font-family:@FONT_MONO@;font-size:8pt;color:@FAINT@}}
@media print{
  body{background:#fff;font-size:12px}
  .page{width:auto;margin:0;padding:0;min-height:0;box-shadow:none}
  .page + .page{break-before:page}
  .foot{display:none}
  thead{display:table-header-group}
  table.grid tr,table.deals tr{break-inside:avoid}
  .bloco{break-inside:auto}
  .masthead,.eyebrow,h3.sec,.estado-h,.setor-h,.total-h,
  table.grid tr.setorhd,table.grid tr.estadohd,table.grid tr.totrow,
  table.deals tr.setorrow{break-after:avoid}
  .cont-row,.cont-card,.chart,.two{break-inside:avoid}
}
""".replace(
        "@INK@", INK
    )
    .replace("@SOFT@", INK_SOFT)
    .replace("@FAINT@", INK_FAINT)
    .replace("@PAPER@", PAPER)
    .replace("@SURFACE@", SURFACE)
    .replace("@FILL@", FILL)
    .replace("@LINE_SOFT@", LINE_SOFT)
    .replace("@LINE@", LINE)
    .replace("@ACCENT@", ACCENT)
    .replace("@VALUE@", VALUE)
    .replace("@FONT_TEXT@", FONT_TEXT)
    .replace("@FONT_MONO@", FONT_MONO)
    .replace("@RADIUS_SM@", RADIUS_SM)
    .replace("@RADIUS@", RADIUS)
)


def _render_tab_fontes(rows):
    return "\n".join(
        f'<tr><td>{esc(r["fonte"])}</td>'
        f'<td class="n">{fmt_int(r["operacoes"])}</td>'
        f'<td class="n">{fmt_int(r["noticias"]) if r.get("noticias") is not None else "—"}</td>'
        f"</tr>"
        for r in rows
    )


def _render_setor_resumo(r, total_label="Total · último tri"):
    out = [
        f'<tr class="totrow"><td>{esc(total_label)}</td>'
        f'<td class="n">{fmt_int(r["n"])}</td>'
        f'<td class="val">{esc(r["valor"])}</td></tr>'
    ]
    for x in r["linhas"]:
        out.append(
            f'<tr><td class="ind">{esc(x["setor"])}</td>'
            f'<td class="n">{fmt_int(x["n"])}</td>'
            f'<td class="val">{esc(x["valor"])}</td></tr>'
        )
    return "\n".join(out)


def _render_tri_ops(g):
    rows = []
    for s in g["setores"]:
        rows.append(
            f'<tr class="setorrow"><td colspan="6">'
            f'<span class="nm">{esc(s["setor"])}</span>'
            f'<span class="tot">{fmt_int(s["n"])} op · <b>{esc(s["valor"])}</b>'
            f"</span></td></tr>"
        )
        for d in s["deals"]:
            link = (
                f'<a href="{esc(d["link"])}" target="_blank" rel="noopener" ' f'title="Abrir notícia">↗</a>'
                if d.get("link")
                else ""
            )
            rows.append(
                f'<tr><td class="maj">{esc(d["maj"])}</td>'
                f'<td class="min">{esc(d["min"])}</td>'
                f'<td class="tp">{esc(d["tipo"])}</td>'
                f'<td class="cont">{esc(d["continente"])}</td>'
                f'<td class="val">{esc(d["valor"])}</td>'
                f'<td class="lnk">{link}</td></tr>'
            )
    header = (
        f'<div class="total-h"><span class="nm">Operações</span>'
        f'<span class="tot">{fmt_int(g["n"])} transações · {esc(g["valor"])}'
        f"</span></div>"
    )
    thead = (
        '<thead><tr><th class="maj">Adquirente</th><th class="min">Alvo</th>'
        '<th class="tp">Tipo</th><th class="cont">Continente</th>'
        '<th class="val">Valor</th><th class="lnk"></th></tr></thead>'
    )
    return (
        f'<div class="bloco">{header}'
        f'<table class="deals ops">{thead}'
        f"<tbody>{chr(10).join(rows)}</tbody></table></div>"
    )


def _render_cont_cards(cards):
    return "\n".join(
        f'<div class="cont-card">'
        f'<div class="nome">{esc(c["nome"])}</div>'
        f'<div class="n">{fmt_int(c["n"])} <small>op.</small></div>'
        f'<div class="v">{esc(c["valor"])}</div></div>'
        for c in cards
    )


def render_html(ctx) -> str:
    k = ctx["kpi"]
    reg = ctx.get("escopo_regiao")
    # Sem o recorte no título/subtítulo, as duas versões do PDF (completa e só
    # Brasil) chegariam no email indistinguíveis pelo conteúdo.
    escopo_titulo = f" · {esc(reg)}" if reg else ""
    escopo_sub = (f"Monitoramento de fusões &amp; aquisições · região: {esc(reg)}"
                  if reg else "Monitoramento de fusões &amp; aquisições")
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Painel M&amp;A{escopo_titulo} — {esc(ctx['asof'])}</title>
<style>{CSS}</style></head><body>

<!-- ═══ PÁGINA 1 ═══ -->
<section class="page">
  <div class="masthead">
    <div><h1 class="serif">Painel de Operações M&amp;A{escopo_titulo}</h1>
      <div class="sub">{escopo_sub}</div></div>
    <div class="meta">Data de referência: <b>{esc(ctx['asof'])}</b><br>
      Moeda: <b>{esc(ctx['moeda'])}</b> · Base: <b>{fmt_int(k['total_base'])}</b> operações<br>
      Cobertura de valor: parcela das operações com valor extraído da descrição da notícia</div>
  </div>

  <div class="kpis">
    <div class="k"><div class="lab">{esc(ctx['kpi_op_label'])}</div><div class="num">{fmt_int(k['tri_n'])}</div></div>
    <div class="k val"><div class="lab">{esc(ctx['kpi_val_label'])}</div><div class="num">{esc(k['val'])}</div></div>
    <div class="k"><div class="lab">Cobertura de valor</div><div class="num">{esc(k['cobertura'])}</div></div>
  </div>

  <div class="eyebrow"><span class="code serif">§01</span><h2>{esc(ctx['resumo_titulo'])}</h2>
    <span class="rule"></span>
    <span class="tag">{esc(ctx['rotulo_tri'])} · desde {esc(ctx['ini_tri'])} · por setor</span></div>
  <table class="grid"><thead><tr><th>Setor</th>
    <th class="n">Nº operações</th><th class="n">Valor somado</th></tr></thead>
    <tbody>{_render_setor_resumo(ctx['tri_resumo'], ctx['total_label'])}</tbody></table>

  <div class="eyebrow"><span class="code serif">§02</span><h2>{esc(ctx['panorama_titulo'])}</h2>
    <span class="rule"></span><span class="tag">{esc(ctx['rotulo_tri'])}</span></div>
  <h3 class="sec">Por continente <span style="font-weight:400">(operações · valor)</span></h3>
  <div class="cont-row">{_render_cont_cards(ctx['cont_cards'])}</div>
  <div class="two" style="margin-top:14px;align-items:center">
    <div class="chart" style="flex:1.15"><h3>Operações por setor</h3>
      <img src="{ctx['chart_set_n']}" alt=""></div>
    <div class="chart" style="flex:0.85"><h3>Distribuição por setor</h3>
      <img src="{ctx['chart_donut']}" alt=""></div>
  </div>

  <div class="foot"><span>Painel de Operações M&amp;A</span><span>Página 1</span></div>
</section>

<!-- ═══ PÁGINA 2 — operações do tri agrupadas ═══ -->
<section class="page">
  <div class="eyebrow"><span class="code serif">§03</span>
    <h2>Operações · último tri</h2><span class="rule"></span>
    <span class="tag">{esc(ctx['rotulo_tri'])} · desde {esc(ctx['ini_tri'])} · agrupado por setor</span>
  </div>
  {_render_tri_ops(ctx['tri_ops'])}
  <div class="foot"><span>Operações por estado e setor</span><span>Página 2</span></div>
</section>

<!-- ═══ PÁGINA 3 — verso: fontes ═══ -->
<section class="page">
  <div class="masthead">
    <div><h1 class="serif">Fontes</h1>
      <div class="sub">Operações identificadas por fonte</div></div>
    <div class="meta"><b>{fmt_int(ctx['n_fontes'])}</b> fontes ·
      <b>{fmt_int(k['total_base'])}</b> operações</div>
  </div>
  <div class="eyebrow"><span class="code serif">§04</span><h2>Operações por fonte</h2>
    <span class="rule"></span><span class="tag">operações identificadas vs. notícias analisadas</span></div>
  <h3 class="sec">15 principais fontes (por operações)</h3>
  <div class="chart" style="max-width:170mm;margin-bottom:14px"><img src="{ctx['chart_fontes']}" alt=""></div>
  <table class="grid"><thead><tr><th>Fonte</th><th class="n">Operações</th>
    <th class="n">Notícias Analisadas</th></tr></thead>
    <tbody>{_render_tab_fontes(ctx['tab_fontes'])}</tbody></table>
  <div class="foot"><span>Painel de Operações M&amp;A · verso</span><span>Página 4</span></div>
</section>

</body></html>"""
