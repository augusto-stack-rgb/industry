"""Coletor Banrisul via TCE-RS / LicitaCon — DADOS ABERTOS (CKAN, requests).

A busca web do LicitaCon tem reCAPTCHA; o TCE-RS publica os dados como dados
abertos (CSV.zip) por órgão. Órgão 7100 = Banrisul Banco do Estado. Coleta
licitações E contratos (contratados) num único CSV (tipo_documento distingue).
Recorte 2024-2026.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

from industry.licitacoes.config import DEST_DIR, ANOS_ALVO

FONTE = "Banrisul - TCE-RS (LicitaCon dados abertos)"
ORGAO = "7100"
URL_LIC = f"https://dados.tce.rs.gov.br/dados/licitacon/licitacao/orgao/{ORGAO}.csv.zip"
URL_CONTRATO = f"https://dados.tce.rs.gov.br/dados/licitacon/contrato/orgao/{ORGAO}.csv.zip"
MODALIDADE = {
    "PRE": "Pregão Eletrônico", "PRP": "Pregão Presencial", "PRI": "Pregão (Lei 13.303)",
    "PRD": "Pregão (Lei 13.303)", "PDE": "Pregão Eletrônico (Lei 13.303)",
    "ESE": "Dispensa/Eletrônico Simplificado", "EST": "Dispensa/Simplificado",
    "CNC": "Concorrência", "CCP": "Concorrência", "CPC": "Concorrência",
    "TMP": "Tomada de Preços", "CNV": "Convite", "LEI": "Leilão",
    "INE": "Inexigibilidade", "DIS": "Dispensa", "RPO": "Registro de Preços",
}


def _pessoas(zf) -> dict:
    out = {}
    try:
        for p in csv.DictReader(io.StringIO(zf.read("pessoas.csv").decode("utf-8-sig"))):
            doc = (p.get("NR_DOCUMENTO") or "").strip()
            if doc and (p.get("NM_PESSOA") or "").strip():
                out[doc] = p["NM_PESSOA"].strip()
    except Exception:
        pass
    return out


def collect_banrisul_tce(dest_dir: Path = DEST_DIR) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    rows = []

    # ── licitações ────────────────────────────────────────────────────────────
    r = s.get(URL_LIC, timeout=120); r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    pessoas = _pessoas(zf)
    vencedores = {}
    try:
        for pr in csv.DictReader(io.StringIO(zf.read("proposta.csv").decode("utf-8-sig"))):
            if pr.get("TP_RESULTADO_PROPOSTA") != "C":
                continue
            k = (pr.get("NR_LICITACAO"), pr.get("ANO_LICITACAO"))
            doc = (pr.get("NR_DOCUMENTO") or "").strip()
            if k not in vencedores or (pr.get("DT_HOMOLOGACAO") and not vencedores[k].get("homol")):
                vencedores[k] = {"cnpj": doc, "nome": pessoas.get(doc, ""),
                                 "homol": (pr.get("DT_HOMOLOGACAO") or "").strip()}
    except Exception:
        pass
    lics = list(csv.DictReader(io.StringIO(zf.read("licitacao.csv").decode("utf-8-sig"))))
    cols = lics[0].keys() if lics else []
    col_val = next((c for c in ["VL_HOMOLOGADO", "VL_LICITACAO"] if c in cols), "VL_LICITACAO")
    dt_cols = [c for c in cols if c.startswith("DT_")]
    for r0 in lics:
        ano = (r0.get("ANO_LICITACAO") or "").strip()
        if ano not in ANOS_ALVO:
            continue
        numero = f"{r0.get('NR_LICITACAO','')}/{ano}"
        cod = (r0.get("CD_TIPO_MODALIDADE") or "").strip()
        venc = vencedores.get((r0.get("NR_LICITACAO"), ano), {})
        rows.append({
            "id": f"tce-lic-{numero}", "fonte": FONTE, "tipo_documento": "edital",
            "tipo": MODALIDADE.get(cod, cod), "modalidade_cod": cod,
            "titulo": f"{MODALIDADE.get(cod, cod)} {numero}".strip(),
            "objeto": (r0.get("DS_OBJETO") or "").strip() or f"Licitação {numero}",
            "link": "https://portal.tce.rs.gov.br/aplicprod/f?p=50500:19",
            "data": next((r0[c] for c in dt_cols if r0.get(c, "").strip()), ""),
            "valor": (r0.get(col_val) or "").strip(), "numero": numero, "ano": ano,
            "contratado": venc.get("nome", "") or venc.get("cnpj", ""),
            "contratado_cnpj": venc.get("cnpj", ""),
            "processo": f"{r0.get('NR_PROCESSO','')}/{r0.get('ANO_PROCESSO','')}",
        })

    # ── contratos (contratados) ───────────────────────────────────────────────
    try:
        rc = s.get(URL_CONTRATO, timeout=120); rc.raise_for_status()
        zfc = zipfile.ZipFile(io.BytesIO(rc.content))
        pessoas_c = _pessoas(zfc)
        for c in csv.DictReader(io.StringIO(zfc.read("contrato.csv").decode("utf-8-sig"))):
            ano = (c.get("ANO_CONTRATO") or "").strip()
            if ano not in ANOS_ALVO:
                continue
            doc = (c.get("NR_DOCUMENTO") or "").strip()
            numero = f"{c.get('NR_CONTRATO','')}/{ano}"
            # contrato.csv também traz CD_TIPO_MODALIDADE (mesmo código da licitação de
            # origem) — sem isso "tipo" ficava fixo em "Contrato", perdendo a modalidade
            # real (ex.: Pregão (Lei 13.303)) que a linha do edital carrega corretamente.
            cod = (c.get("CD_TIPO_MODALIDADE") or "").strip()
            rows.append({
                "id": f"tce-contrato-{numero}", "fonte": FONTE, "tipo_documento": "contrato",
                "tipo": MODALIDADE.get(cod, cod) or "Contrato", "modalidade_cod": cod,
                "titulo": f"Contrato {numero}".strip(),
                "objeto": (c.get("DS_OBJETO") or "").strip() or f"Contrato {numero}",
                "link": c.get("LINK_LICITACON_CIDADAO") or "https://portal.tce.rs.gov.br/aplicprod/f?p=50500:19",
                "data": c.get("DT_ASSINATURA") or c.get("DT_INICIO_VIGENCIA", ""),
                "valor": (c.get("VL_CONTRATO") or "").strip(), "numero": numero, "ano": ano,
                "contratado": pessoas_c.get(doc, "") or doc, "contratado_cnpj": doc,
                "data_inicio_vigencia": c.get("DT_INICIO_VIGENCIA", ""),
                "data_fim_vigencia": c.get("DT_FINAL_VIGENCIA", ""),
                "processo": f"{c.get('NR_PROCESSO','')}/{c.get('ANO_PROCESSO','')}",
            })
    except Exception as e:
        print(f"[tce] contratos indisponíveis: {str(e)[:60]}")

    df = pd.DataFrame(rows)
    out = dest_dir / "banrisul_tce_rs.csv"
    df.to_csv(out, sep=";", index=False, encoding="utf-8-sig")
    print(f"Banrisul TCE-RS: {len(df)} registros -> {out}")
    return out


if __name__ == "__main__":
    print(collect_banrisul_tce())
