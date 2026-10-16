from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import pandas as pd
import time

import re

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO
from industry.shared.connections import get_selenium_command_executor


def collect_b3(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    url = "https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/leiloes/licitacoes-publicas/licitacoes/em-andamento-e-anteriores/"

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Remote(
        command_executor=get_selenium_command_executor("/wd/hub"),
        options=options
    )

    try:
        print("Acessando B3...")
        driver.get(url)
        wait = WebDriverWait(driver, 30)

        print("Entrando no bvmf_iframe...")
        wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "bvmf_iframe")))

        time.sleep(5)

        def extrair_com_links(sufixo_id, status):
            print(f"Buscando: {status}...")
            try:
                seletor = f"div[id$='{sufixo_id}']"
                container = wait.until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, seletor))
                )

                tabela = container.find_element(By.TAG_NAME, "table")
                linhas = tabela.find_elements(By.TAG_NAME, "tr")

                dados = []
                for linha in linhas:
                    colunas = linha.find_elements(By.TAG_NAME, "td")
                    if len(colunas) >= 2:
                        data_texto = colunas[0].text.strip()
                        try:
                            link_element = colunas[1].find_element(By.TAG_NAME, "a")
                            projeto_nome = link_element.text.strip()
                            projeto_link = link_element.get_attribute("href")
                        except Exception:
                            projeto_nome = colunas[1].text.strip()
                            projeto_link = ""

                        if data_texto and projeto_nome:
                            dados.append(
                                {
                                    "Data": data_texto,
                                    "Projeto": projeto_nome,
                                    "Link": projeto_link,
                                    "Status": status,
                                }
                            )

                print(f"Capturado {len(dados)} itens de {status}.")
                return dados
            except Exception as e:
                print(f"Falha ao capturar {status}: {type(e).__name__}")
                return []

        andamento_sufixo = "grdResumoLeiloesEspeciaisAndamento"
        anteriores_sufixo = "grdResumoLeiloesEspeciaisAnteriores"

        resultados = []
        resultados.extend(extrair_com_links(andamento_sufixo, "Em Andamento"))
        resultados.extend(extrair_com_links(anteriores_sufixo, "Anteriores"))

        # recorte recente: mantém só linhas do(s) ano(s) alvo (por ano na Data)
        def _ano_ok(r):
            m = re.search(r"/(20\d{2})", r.get("Data", "") or "")
            return (m.group(1) in ANOS_ALVO) if m else True  # sem data -> mantém
        resultados = [r for r in resultados if _ano_ok(r)]

        if not resultados:
            print("\nNenhum dado extraído.")
            raise RuntimeError("Nenhum dado extraído de B3")

        df = pd.DataFrame(resultados)
        out_path = dest_dir / "b3_licitacoes_com_links.csv"
        df.to_csv(out_path, index=False, sep=";", decimal=",", encoding="utf-8-sig")

        print(f"\nSucesso! {len(df)} registros com links salvos em '{out_path}'.")
        return out_path
    finally:
        driver.quit()


if __name__ == "__main__":
    path = collect_b3()
    print(f"B3 -> {path}")
