import pandas as pd
import re

from datetime import datetime, timedelta

from industry.noticias.coleta.config import FRASES_REMOVER_TEXTO
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

def normalizar_data(valor):
    if pd.isna(valor):
        return datetime.today().strftime("%d/%m/%Y")

    v = str(valor).strip()

    # ISO 8601: YYYY-MM-DD ou YYYY-MM-DDThh:mm:ss[Z/±offset]
    # dayfirst não se aplica — o ano na frente já resolve a ambiguidade
    if re.match(r"^\d{4}-\d{2}-\d{2}", v):
        dt_iso = pd.to_datetime(v, dayfirst=False, errors="coerce", utc=True)
        if pd.notna(dt_iso):
            return dt_iso.strftime("%d/%m/%Y")

    v = v.lower()

    dt_try = pd.to_datetime(v, dayfirst=True, errors="coerce", utc=True)
    if not pd.isna(dt_try):
        return dt_try.strftime("%d/%m/%Y")

    prefixos = ["publicado em", "publicada em"]
    for p in prefixos:
        if v.startswith(p):
            v = v.replace(p, "").strip()

    if "semana" in v:
        partes = v.split()
        for p in partes:
            if p.isdigit():
                return (datetime.today() - timedelta(weeks=int(p))).strftime("%d/%m/%Y")

    if "dia" in v:
        partes = v.split()
        for p in partes:
            if p.isdigit():
                return (datetime.today() - timedelta(days=int(p))).strftime("%d/%m/%Y")

    if "hora" in v:
        partes = v.split()
        for p in partes:
            if p.isdigit():
                return (datetime.today() - timedelta(hours=int(p))).strftime("%d/%m/%Y")

    lixos = ["|", "•", "atualizado em"]
    for lixo in lixos:
        if lixo in v:
            v = v.split(lixo)[0]

    meses = {
        "janeiro": "01",
        "jan": "01",
        "fevereiro": "02",
        "fev": "02",
        "março": "03",
        "mar": "03",
        "abril": "04",
        "abr": "04",
        "maio": "05",
        "mai": "05",
        "junho": "06",
        "jun": "06",
        "julho": "07",
        "jul": "07",
        "agosto": "08",
        "ago": "08",
        "setembro": "09",
        "set": "09",
        "outubro": "10",
        "out": "10",
        "novembro": "11",
        "nov": "11",
        "dezembro": "12",
        "dez": "12",
    }

    for nome, num in meses.items():
        v = v.replace(nome, num)

    v = v.replace(" de ", "/")
    v = v.replace("-", "/")

    if ":" in v:
        v = v.split()[0]

    if "h" in v:
        v = v.split("h")[0]

    v = v.strip()
    dt = pd.to_datetime(v, dayfirst=True, errors="coerce")

    if pd.isna(dt):
        return datetime.today().strftime("%d/%m/%Y")

    return dt.strftime("%d/%m/%Y")


def normalizar_titulo(titulo) -> str:
    if pd.isna(titulo):
        return ""
    titulo = str(titulo)
    return re.sub(r"^\d{1,2}:\d{2}", "", titulo).strip()


def normalizar_texto(texto) -> str:
    if pd.isna(texto):
        return texto
    texto = str(texto)
    for frase in FRASES_REMOVER_TEXTO:
        texto = re.sub(r"\s*" + frase + r"\s*", " ", texto, flags=re.IGNORECASE).strip()
    return texto

def salvar_dataframe_limpo(engine=None):
    if engine is None:
        engine = get_sqlalchemy_engine(NOTICIAS_PG)

    hoje = datetime.today().strftime("%Y-%m-%d")

    query = f"""
        SELECT *
        FROM noticias.noticias_raw
        WHERE data_coleta = '{hoje}'
    """
    df_raw = pd.read_sql(query, con=engine)

    if df_raw.empty:
        return

    df_raw["data"] = df_raw["data"].apply(normalizar_data)
    df_raw["titulo"] = df_raw["titulo"].apply(normalizar_titulo)
    df_raw["texto"] = df_raw["texto"].apply(normalizar_texto)

    df_clean = df_raw.dropna(subset=["texto"])

    cols_noticias = [
        "titulo",
        "link",
        "data",
        "texto",
        "setor",
        "fonte",
    ]
    df_clean = df_clean[cols_noticias].astype(str)

    with engine.begin() as conn:
        df_clean.to_sql(
            name="noticias",
            schema="noticias",
            con=conn,
            if_exists="append",
            index=False,
        )
