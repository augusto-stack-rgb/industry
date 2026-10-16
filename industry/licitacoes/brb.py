from pathlib import Path
import requests
import pandas as pd
from time import sleep, time

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.licitacoes.net import get_json


def collect_brb(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    URL_BASE = "https://pdd.brb.com.br/PLC/api/licitacao"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://pdd.brb.com.br",
        "Referer": "https://pdd.brb.com.br/PLC/licitacao",
    }

    # Sessão única + warm-up: carregar a página antes da API pega os cookies do
    # WAF (F5) e reduz o bloqueio "Request Rejected", que é intermitente por taxa.
    sess = requests.Session()
    sess.headers.update(HEADERS)
    try:
        sess.get("https://pdd.brb.com.br/PLC/licitacao", timeout=30)
    except requests.RequestException:
        pass  # warm-up é best-effort; get_json ainda re-tenta/levanta adiante

    todos_registros = []
    t_inicio = time()

    for ano in sorted(int(a) for a in ANOS_ALVO):
        print(f"\n--- Coletando BRB para o ano: {ano} ---")

        pagina_atual = 1
        per_page = 50

        while True:
            params = {
                "per_page": per_page,
                "page": pagina_atual,
                "tipo": 0,
                "fase": 0,
                "keyWord": "",
                "inicio": f"{ano}-01-01T00:00:00.000Z",
                "fim": f"{ano}-12-31T23:59:59.000Z",
            }

            # get_json levanta ColetorBloqueado em WAF/HTTP inválido (falha a task);
            # NÃO engolimos mais o erro num except/break, que mascarava o bloqueio
            # do WAF ("Request Rejected" com HTTP 200) como coleta parcial "ok".
            dados_json = get_json(
                URL_BASE, fonte="BRB", session=sess, params=params, timeout=30
            )
            registros = (
                dados_json.get("data", [])
                if isinstance(dados_json, dict)
                else dados_json
            )

            if not registros:
                print(f"Fim das páginas para o ano {ano}.")
                break

            todos_registros.extend(registros)
            print(
                f"Ano {ano} | Página {pagina_atual} | Total acumulado: {len(todos_registros)}"
            )

            if len(registros) < per_page:
                break

            pagina_atual += 1
            sleep(0.5)

    if not todos_registros:
        raise RuntimeError("Nenhum registro coletado no BRB")

    df = pd.DataFrame(todos_registros)

    colunas_para_remover = [
        "biddingtypename",
        "nome_tipo",
        "biddingTypeName",
        "nomeTipo",
    ]
    df = df.drop(
        columns=[c for c in colunas_para_remover if c in df.columns], errors="ignore"
    )

    df.columns = df.columns.str.lower().str.replace(r"[^\w]+", "_", regex=True)

    # A API devolve "situacao" como objeto {'id':N,'fase':'...'} — sem isso, o
    # repr do dict Python ia parar cru no Excel/e-mail, e nada conseguia usar o
    # texto da fase (ex.: "Licitação homologada") pra saber que já fechou.
    if "situacao" in df.columns:
        df["situacao"] = df["situacao"].map(
            lambda v: v.get("fase", "") if isinstance(v, dict) else v
        )

    for col in df.columns:
        if "data" in col or "date" in col or col in ["inicio", "fim"]:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date

    out_path = dest_dir / "licitacoes_brb_2020_2025.csv"
    df.to_csv(
        out_path,
        sep=";",
        decimal=",",
        index=False,
        encoding="utf-8-sig",
    )

    print("\n--- Extração Concluída ---")
    print(f"Total de registros: {len(df)}")
    print(f"Arquivo salvo como: {out_path}")
    print(f"Tempo total: {(time() - t_inicio) / 60:.2f} minutos")
    return out_path


if __name__ == "__main__":
    print(collect_brb())
