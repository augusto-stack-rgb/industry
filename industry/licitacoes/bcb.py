from pathlib import Path
from urllib.parse import quote
import requests
import pandas as pd
import time

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.licitacoes.net import get_json

FONTE = "BCB - Banco Central do Brasil"


def collect_bcb(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = "https://olinda.bcb.gov.br/olinda/servico/Licitacoes/versao/v1/odata/Editais"
    headers = {"Accept": "application/json"}

    top = 1000
    skip = 0
    todos = []
    # recorte recente: só o(s) ano(s) de ANOS_ALVO (Olinda exige %20, não '+')
    filtro = quote(f"Ano ge {min(int(a) for a in ANOS_ALVO)}")

    inicio_total = time.time()
    ultimo_log = inicio_total
    ultimo_contador = 0

    print("Iniciando coleta das licitações do BCB...\n")

    while True:
        qs = f"$filter={filtro}&$top={top}&$skip={skip}&$format=json"
        # get_json re-tenta em WAF/rede e levanta ColetorBloqueado se persistir
        # (falha a task) em vez de virar 0 registros silenciosos.
        dados = get_json(f"{url}?{qs}", fonte="BCB", headers=headers,
                         timeout=30).get("value", [])
        if not dados:
            break

        todos.extend(dados)
        skip += top

        if len(todos) - ultimo_contador >= 100:
            agora = time.time()
            tempo_bloco = agora - ultimo_log

            print(f"Coletados: {len(todos):>6} | Últimos 100 em {tempo_bloco:6.2f}s")

            ultimo_log = agora
            ultimo_contador = len(todos)

    print("\nColeta finalizada.")

    tempo_total = time.time() - inicio_total
    print(f"Total de registros: {len(todos)}")
    print(f"Tempo total: {tempo_total:,.2f} segundos")

    df = pd.DataFrame(todos)
    # IdEdital é o identificador de verdade (único, é PK do sistema do BCB) — sem
    # isso, o bronze usa "Numero" (curto, reinicia por departamento: "1", "2"...)
    # como base do id_unificado, e licitações de departamentos diferentes com o
    # mesmo Numero colidem entre si (achado: numero="1" tinha 52 objetos
    # diferentes disputando o mesmo id). Ver DECISOES.md — mesmo padrão do
    # Banestes, fonte diferente.
    if "IdEdital" in df.columns:
        df["id"] = df["IdEdital"]

    out_path = dest_dir / "licitacoes_bcb_todas.csv"
    df.to_csv(
        out_path,
        sep=";",
        decimal=",",
        index=False,
        encoding="utf-8-sig",
    )

    return out_path


if __name__ == "__main__":
    print(collect_bcb())
