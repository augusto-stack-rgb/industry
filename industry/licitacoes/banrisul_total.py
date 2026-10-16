from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import pandas as pd
from time import sleep, time

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO

FONTE = "Banrisul"


def collect_banrisul(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    URL = "https://pregaobanrisul.com.br/editais/pesquisa.json"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
    }

    # Session with retry strategy
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)

    todos = []
    req_count = 0

    t_inicio = time()
    t_lote = time()

    for ano in sorted(int(a) for a in ANOS_ALVO):
        print(f"\n Coletando ano {ano}")

        start = 0
        length = 10
        draw = 1

        while True:
            data = {
                "draw": draw,
                "start": start,
                "length": length,
                "issuanceNumber": "",
                "processNumber": "",
                "legalNormType": "",
                "combinedBiddingType": "",
                "status": "",
                "tradeOffice": "",
                "processingType": "",
                "temporaryLawType": "",
                "publicationStartDate": f"01/01/{ano}",
                "publicationEndDate": f"31/12/{ano}",
                "startDateFrom": "",
                "startDateTo": "",
                "objectType": "",
                "judgmentType": "",
                "classifications": "",
                "description": "",
            }

            try:
                resp = session.post(URL, headers=headers, data=data, timeout=30)
                resp.raise_for_status()

                json_resp = resp.json()
                registros = json_resp.get("data", [])

                if not registros:
                    break

                todos.extend(registros)

                start += length
                draw += 1
                req_count += length

                if req_count % 100 == 0:
                    agora = time()
                    print(
                        f"{req_count} req | "
                        f"{len(todos)} registros | "
                        f"{(agora - t_lote):.1f}s últimos 100 | "
                        f"{(agora - t_inicio) / 60:.1f} min total"
                    )
                    t_lote = time()

                sleep(0.5)
            except requests.exceptions.RequestException as e:
                print(f"Erro ao coletar ano {ano}: {e}")
                break

    df = pd.DataFrame(todos)

    for col in ["publishDate", "startDate"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="ms", errors="coerce").dt.date

    df.columns = df.columns.str.lower().str.replace(r"[^\w]+", "_", regex=True)

    out_path = dest_dir / "licitacoes_banrisul_2020_atual.csv"
    df.to_csv(
        out_path,
        sep=";",
        decimal=",",
        index=False,
        encoding="utf-8-sig",
    )

    print(f"Total de registros: {len(df)}")
    print(f"Tempo total: {(time() - t_inicio) / 60:.1f} minutos")

    return out_path


if __name__ == "__main__":
    print(collect_banrisul())
