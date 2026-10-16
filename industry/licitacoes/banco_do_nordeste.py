from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

from industry.licitacoes.config import DEST_DIR
from industry.shared.connections import get_selenium_command_executor


def collect_banco_do_nordeste(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    driver = webdriver.Remote(
        command_executor=get_selenium_command_executor("/wd/hub"),
        options=options
    )

    url = (
        "https://www.bnb.gov.br/web/guest/acesso-a-informacao/licitacoes-e-contratos/"
        "licitacoes/em-andamento"
    )

    try:
        driver.get(url)
        wait = WebDriverWait(driver, 40)

        iframe = wait.until(EC.presence_of_element_located((By.TAG_NAME, "iframe")))
        driver.switch_to.frame(iframe)

        csv_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'CSV')]"))
        )
        driver.execute_script("arguments[0].click();", csv_button)

        time.sleep(15)

        downloaded_files = sorted(
            dest_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not downloaded_files:
            raise FileNotFoundError(
                "Nenhum arquivo CSV encontrado em banco_do_nordeste"
            )

        out_path = downloaded_files[0]
        print(f"Banco do Nordeste -> {out_path}")
        return out_path
    finally:
        driver.quit()


if __name__ == "__main__":
    print(collect_banco_do_nordeste())
