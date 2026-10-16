"""Relatório de notícias por setor (HTML) e envio opcional por e-mail.

Porte do ``task_enviar_emails.py`` da DAG MARKETING_EnvioNoticias, só com o
layout que era usado (``editorial_bullets``) e com estas mudanças:

- texto ÚNICO por geografia (Global, Brasil, Regional): saiu a primeira parte de
  bullets resumidos; ficou o parágrafo longo, agora com mais trechos em negrito;
- sem os cabeçalhos em PNG por setor e sem o logo (cabeçalho em HTML puro);
- sem o rodapé "Saiba mais em <datahub>";
- foco: oportunidades de negócio e fatos que movem o mercado, para uma
  consultoria de ML Engineering, Data Management e Data Engineering;
- e-mail desligado por padrão (config.ENVIO_EMAIL_ATIVO); ligado, vai só para os
  destinatários fixos em config.DESTINATARIOS, com todos os setores. O relatório é sempre gravado em
  ``noticias.relatorios_email`` (o home pode exibir dali);
- LLM pelo roteador de chaves (era Ollama local).
"""
import html as _html
import json
import logging
import os
import re
import smtplib
import time
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import pendulum
from sqlalchemy import text as sa_text

from industry.noticias.envio.config import (
    DESTINATARIOS,
    DIA_ENVIO_EMAIL,
    ENVIO_EMAIL_ATIVO,
    MODELO_REDACAO,
    NUMERO_DE_NOTICIAS,
    QUANTOS_DIAS_ATRAS,
    SETORES,
    calcular_unidade_de_tempo,
)
from industry.shared import llm
from industry.shared.connections import NOTICIAS_PG, get_sqlalchemy_engine

log = logging.getLogger(__name__)

LAYOUT = "paragrafos"
TIMEOUT_REDACAO_S = 300
# Texto de cada notícia enviado ao modelo (era o mesmo corte na origem).
CARACTERES_TEXTO = 400

GEOS = [("GLOBAL", "Global"), ("BRASIL", "Brasil"), ("REGIONAL_SUL", "Regional")]

MESES_PT = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]

SOURCE_MAP = {
    "Brazil_Journal_Eletrico": "Brazil Journal - Elétrico",
    "TechCrunch": "Techcrunch",
    "Globo_Rural": "Globo Rural",
    "Noticias_Agricolas": "Notícias Agrícolas",
    "Quatro_Rodas_Negocios": "Quatro Rodas Negócios",
    "Quatro_Rodas_Noticias": "Quatro Rodas Notícias",
    "agrolink_soja": "Agrolink Soja",
    "agrolink_milho": "Agrolink Milho",
    "agrolink_trigo": "Agrolink Trigo",
    "agrolink_arroz": "Agrolink Arroz",
    "WMO": "WMO",
    "Autodata": "Autodata",
    "Brazil_Journal_Tecnologia": "Brazil Journal - Tecnologia",
    "MME": "MME",
    "aneel": "ANEEL",
    "sudam": "SUDAM",
    "sudene": "SUDENE",
    "mdic": "MDIC",
    "cnn_petroleo": "CNN Petróleo",
    "infomoney": "Infomoney",
    "forbes": "Forbes",
}

INSTRUCAO_IDIOMA = (
    "Escreva TODO o texto em português do Brasil, inclusive quando as notícias "
    "estiverem em inglês ou outro idioma. Nomes próprios ficam como estão."
)
_PALAVRAS_EN = re.compile(r"\b(the|and|with|of|are|is|for|its|while|this|has|have|was|were)\b", re.I)
_PALAVRAS_PT = re.compile(r"\b(de|que|com|para|os|as|do|da|em|no|na|uma|um|é|dos|das)\b", re.I)

PROMPT_SISTEMA = "Responda SOMENTE no formato solicitado. Qualquer texto fora do formato é proibido."

PROMPT_RELATORIO = """
Sua tarefa é escrever um relatório editorial das notícias abaixo, organizado por geografia,
para uma consultoria de Machine Learning Engineering, Data Management e Data Engineering que
atende empresas do setor {setor}. O leitor quer saber (1) que OPORTUNIDADES DE NEGÓCIO as
notícias revelam — empresas investindo, expandindo, digitalizando, automatizando, adotando IA
ou sujeitas a novas exigências de dados — e (2) que FATOS IMPORTANTES movem o mercado do setor.

Cada notícia possui um ID e uma TAG de geografia (REGIONAL_SUL, BRASIL ou GLOBAL).

Contexto:
- Período: {unidade_de_tempo}
- Setor: {setor}

Notícias (ID | TAG | fonte | título — começo do texto):
{noticias}

Gere três PARÁGRAFOS — um por geografia, cobrindo APENAS as notícias daquela geografia.

Regras obrigatórias:
- Cada parágrafo cobre todas as notícias daquela geografia, da mais relevante para a menos
  relevante, com UMA ou DUAS frases por notícia. A segunda frase é opcional e só traz dado,
  motivo ou consequência QUE ESTEJA NO TEXTO da notícia (valores, prazos, escopo, quem é
  afetado). Não especule: não afirme que algo "exigirá gestão de dados", "abre oportunidade
  para consultorias" ou "sinaliza transformação digital" se a notícia não disser isso.
- TODA frase, sem exceção, termina com o ID da notícia entre chaves — nunca deixe uma frase
  sem referência.
- Cada frase cita APENAS UM ID. Nunca coloque dois ou mais IDs na mesma frase (não faça
  {{2}}{{4}} nem {{2}} {{4}}).
- Use apenas IDs das notícias fornecidas para aquela geografia.
- NEGRITO (obrigatório): cada parágrafo com notícias deve ter NO MÍNIMO 3 trechos entre
  **asteriscos duplos** — pelo menos um por notícia. Negrite trechos curtos (até 12
  palavras): o nome da empresa ou do órgão junto do fato central, os números e valores que
  importam e a consequência para o mercado. Nunca negrite o parágrafo inteiro nem uma frase
  longa inteira. Exemplo de frase no tom e na densidade de negrito esperados:
  "A **Petrobras abriu licitação para uma plataforma de dados de E&P**, com contrato de
  **36 meses** {{3}}. O edital prevê **integração dos dados de exploração e produção** num
  único ambiente {{3}}."
- Quando a notícia trouxer investimento, contratação, expansão, nova planta, fusão,
  digitalização, automação, uso de IA/dados ou exigência regulatória de dados, deixe esse
  fato explícito e em negrito — é o que a consultoria procura. Não acrescente comentários
  sobre a consultoria ou sobre "oportunidades" que a notícia não menciona.
- Não invente dados, empresas nem números.
- Linguagem executiva.
- Se não houver notícias de uma geografia, escreva apenas "Sem destaques no período."

Formato EXATO obrigatório (sem nenhum texto adicional):
PARAGRAFO_GLOBAL: <parágrafo sobre as notícias GLOBAL, cada frase termina com {{ID}}>
PARAGRAFO_BRASIL: <parágrafo sobre as notícias BRASIL, cada frase termina com {{ID}}>
PARAGRAFO_REGIONAL_SUL: <parágrafo sobre as notícias REGIONAL_SUL, cada frase termina com {{ID}}>
"""


# ── helpers ──────────────────────────────────────────────────────────────────
def _jsonable(value):
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and pd.isna(value):
        return None
    if value is pd.NaT:
        return None
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _parece_ingles(texto: str) -> bool:
    """Mais palavras funcionais de inglês do que de português."""
    texto = re.sub(r"[A-Z_]+:", " ", texto or "")
    return len(_PALAVRAS_EN.findall(texto)) > len(_PALAVRAS_PT.findall(texto))


def _preposicao_tempo(unidade: str) -> str:
    if unidade == "dia":
        return "do dia"
    if unidade == "semana":
        return "da semana"
    if "semanas" in unidade:
        return f"das últimas {unidade}"
    if "dias" in unidade:
        return f"dos últimos {unidade}"
    return f"da {unidade}"


def _data_semana(agora) -> str:
    return f"Semana de {agora.day:02d} de {MESES_PT[agora.month - 1]} de {agora.year}"


def _data_dia(agora) -> str:
    return f"{agora.day:02d} de {MESES_PT[agora.month - 1]} de {agora.year}"


def _chamar_llm(client, mensagens: list[dict], rotulo: str) -> str:
    """Chamada com a instrução de idioma; repete uma vez se vier em inglês
    (com notícias em inglês na lista, a origem chegou a escrever em inglês)."""
    msgs = [{"role": "system", "content": INSTRUCAO_IDIOMA}] + mensagens
    texto = client.create(messages=msgs).choices[0].message.content.strip()
    if _parece_ingles(texto):
        log.warning(f"[{rotulo}] resposta em inglês — repetindo com reforço de idioma")
        texto = client.create(messages=msgs + [
            {"role": "assistant", "content": texto},
            {"role": "user", "content": "A resposta acima está em inglês. Reescreva TUDO em "
                                        "português do Brasil, mantendo exatamente o mesmo formato."},
        ]).choices[0].message.content.strip()
    return texto


# Refs consecutivas ({11}{12}{14} ou {11} {12}) e marcação de negrito.
_REF_RUN = re.compile(r"(?:\{\d+\}\s*){2,}")
_SINGLE_REF = re.compile(r"\d+")
# ** ou * viram negrito — o modelo é inconsistente sobre quantos asteriscos usa.
_MARKUP = re.compile(r"\{(\d+)\}|\*{1,2}(.+?)\*{1,2}")
_SUP = '<sup style="font-size:10px;color:#1a3a4a;font-weight:bold;">{}</sup>'


def _collapse_ref_runs(text: str) -> str:
    def _fuse(m: re.Match) -> str:
        ids = sorted(set(int(n) for n in _SINGLE_REF.findall(m.group(0))))
        return "".join(f"{{{n}}}" for n in ids)
    return _REF_RUN.sub(_fuse, text)


def _strip_orphan_asterisks(text: str) -> str:
    """Asterisco que abre destaque e nunca fecha sai do texto."""
    return text.replace("*", "") if text.count("*") % 2 == 1 else text


def _render_inline(text: str) -> str:
    """{N} -> <sup>N</sup>; *trecho* / **trecho** -> <strong>; resto escapado."""
    text = _strip_orphan_asterisks(_collapse_ref_runs(text))
    out, last, prev_ref = [], 0, False
    for m in _MARKUP.finditer(text):
        gap = text[last:m.start()]
        if gap:
            out.append(_html.escape(gap))
        ref, bold = m.group(1), m.group(2)
        if ref is not None:
            if prev_ref and not gap:
                out.append(_SUP.format(","))
            out.append(_SUP.format(ref))
        else:
            out.append(f"<strong>{_html.escape(bold)}</strong>")
        prev_ref = ref is not None
        last = m.end()
    out.append(_html.escape(text[last:]))
    return "".join(out)


def _parse_paragrafos(raw_text: str) -> dict[str, str]:
    paragrafos = {g: "" for g, _ in GEOS}
    atual, linhas = None, []

    def flush():
        if atual:
            paragrafos[atual] = " ".join(linhas).strip()

    for line in raw_text.split("\n"):
        st = line.strip()
        achou = False
        for geo, _ in GEOS:
            prefixo = f"PARAGRAFO_{geo}:"
            if st.upper().startswith(prefixo):
                flush()
                atual, linhas, achou = geo, [st[len(prefixo):].strip()], True
                break
        if not achou and atual and st:
            linhas.append(st)
    flush()
    return paragrafos


def _lista_para_prompt(noticias: list[dict]) -> str:
    linhas = []
    for n in noticias:
        texto = re.sub(r"\s+", " ", str(n.get("texto") or ""))[:CARACTERES_TEXTO]
        linhas.append(f"{n['id']} | {n['tag']} | {n.get('fonte', '')} | {n['titulo']} — {texto}")
    return "\n".join(linhas)


# ── HTML ─────────────────────────────────────────────────────────────────────
_FONTE = "font-family: 'Source Sans 3', Arial, Helvetica, sans-serif;"


def _montar_html(setor, unidade_de_tempo, data_display, paragrafos, aux_filtrado) -> str:
    blocos = ""
    for i, (geo, rotulo) in enumerate(GEOS):
        sep = ("border-top: 1px solid #eeeeee; margin-top: 24px; padding-top: 24px;"
               if i > 0 else "")
        blocos += (
            f'            <div style="{sep}">\n'
            f'              <p class="section-label" style="margin: 0 0 6px 0; font-size: 10px; '
            f'font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #1a3a4a; {_FONTE}">'
            f"{rotulo}</p>\n"
            f'              <p class="paragraph-text" style="margin: 0; font-size: 16px; color: #333333; '
            f'line-height: 1.7; {_FONTE}">{_render_inline(paragrafos.get(geo, ""))}</p>\n'
            f"            </div>\n"
        )

    fontes = ""
    for i, n in enumerate(aux_filtrado.to_dict("records"), start=1):
        bruto = (n.get("fonte") or "").strip()
        rotulo_fonte = SOURCE_MAP.get(bruto, bruto.replace("_", " "))
        try:
            data_txt = pd.to_datetime(n["data"]).strftime("%d/%m/%Y") if n.get("data") is not None else ""
        except Exception:  # noqa: BLE001
            data_txt = ""
        fontes += (
            f"              <tr>\n"
            f'                <td class="source-text" style="padding: 5px 0; font-size: 12px; color: #666666; '
            f'line-height: 1.5; border-bottom: 1px solid #eeeeee; {_FONTE}">\n'
            f'                  <strong style="color: #1a3a4a;">{i}</strong> &nbsp; '
            f"{_html.escape(rotulo_fonte)} ({data_txt}) – "
            f'<a href="{_html.escape(n.get("link", ""), quote=True)}" style="color: #1a3a4a; '
            f'text-decoration: underline;">{_html.escape(n.get("titulo", ""))}</a>\n'
            f"                </td>\n"
            f"              </tr>\n"
        )

    titulo = (f"Notícias mais importantes {_preposicao_tempo(unidade_de_tempo)} — "
              f"{_html.escape(setor)}")
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="x-apple-disable-message-reformatting">
<link href="https://fonts.googleapis.com/css2?family=Source+Sans+3:wght@300;400;600;700&display=swap" rel="stylesheet">
<title>{titulo}</title>
<style>
  body, table, td, p, a {{ -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }}
  @media only screen and (max-width: 600px) {{
    .container-principal {{ width: 100% !important; max-width: 100% !important; }}
    .header-cell {{ padding: 24px 20px 20px 20px !important; }}
    .body-cell {{ padding: 24px 20px !important; }}
    .title-h1 {{ font-size: 20px !important; line-height: 1.3 !important; }}
    .paragraph-text {{ font-size: 16px !important; line-height: 1.65 !important; }}
    .section-label {{ font-size: 11px !important; }}
    .source-text {{ font-size: 13px !important; line-height: 1.55 !important; }}
  }}
</style>
</head>
<body style="margin: 0; padding: 0; background-color: #ffffff; {_FONTE}">
<table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%" style="background-color: #ffffff;">
  <tr>
    <td align="center" style="padding: 0;">
      <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="600" class="container-principal" style="background-color: #ffffff; max-width: 600px;">

        <!-- Cabeçalho -->
        <tr>
          <td class="header-cell" style="background-color: #1a3a4a; padding: 30px 50px 26px 50px;">
            <p style="margin: 0 0 14px 0; font-size: 11px; color: #8ab0c8; letter-spacing: 2.5px; text-transform: uppercase; {_FONTE}">
              Oportunidades e mercado &nbsp;&middot;&nbsp; <span style="letter-spacing: 0; color: #a9c4d6;">{_html.escape(data_display)}</span>
            </p>
            <h1 class="title-h1" style="margin: 0; font-size: 26px; color: #ffffff; font-weight: bold; line-height: 1.3; {_FONTE}">
              {titulo}
            </h1>
          </td>
        </tr>

        <!-- Texto por geografia -->
        <tr>
          <td class="body-cell" style="background-color: #ffffff; padding: 30px 50px 30px 50px;">
{blocos}          </td>
        </tr>

        <!-- Fontes -->
        <tr>
          <td class="body-cell" style="background-color: #ffffff; padding: 25px 50px 30px 50px; border-top: 1px solid #e0e0e0;">
            <p style="margin: 0 0 14px 0; font-size: 10px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: #888888; {_FONTE}">Fontes</p>
            <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
{fontes}            </table>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


MIN_NEGRITO = 3
_NEGRITO = re.compile(r"\*\*(.+?)\*\*")
REVISAO_NEGRITO = (
    "Mantenha exatamente o mesmo texto, os mesmos IDs e o mesmo formato, mas marque com "
    "**asteriscos duplos** pelo menos 3 trechos curtos em cada parágrafo que tem notícias "
    "(pelo menos um por notícia): empresa ou órgão + fato central, números e a consequência "
    "para o mercado. Não negrite frases inteiras."
)


def _pouco_negrito(paragrafos: dict[str, str]) -> bool:
    """Algum parágrafo ficou com menos de um negrito por notícia (até MIN_NEGRITO)."""
    for p in paragrafos.values():
        refs = len(set(re.findall(r"\{(\d+)\}", p)))
        if refs and len(_NEGRITO.findall(p)) < min(MIN_NEGRITO, refs):
            return True
    return False


def montar_relatorio(client, setor, unidade_de_tempo, data_display, noticias, aux_filtrado):
    """(html, paragrafos) de um setor."""
    prompt = PROMPT_RELATORIO.format(setor=setor, unidade_de_tempo=unidade_de_tempo,
                                     noticias=_lista_para_prompt(noticias))
    raw = _chamar_llm(client, [{"role": "system", "content": PROMPT_SISTEMA},
                               {"role": "user", "content": prompt}], f"relatorio/{setor}")
    paragrafos = _parse_paragrafos(raw)
    if _pouco_negrito(paragrafos):
        # O modelo tende a negritar só valores. Uma revisão focada costuma resolver.
        log.info(f"[relatorio/{setor}] pouco negrito — pedindo revisão")
        revisto = _parse_paragrafos(_chamar_llm(client, [
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": REVISAO_NEGRITO},
        ], f"relatorio/{setor}/negrito"))
        if all(revisto.values()) and not _pouco_negrito(revisto):
            paragrafos = revisto
        else:
            log.warning(f"[relatorio/{setor}] negrito continuou abaixo do mínimo")
    html = _montar_html(setor, unidade_de_tempo, data_display, paragrafos, aux_filtrado)
    return html, paragrafos


# ── persistência e e-mail ─────────────────────────────────────────────────────
def _salvar_relatorio(engine, setor, html_content, paragrafos, noticias,
                      unidade_de_tempo, data_display, quantos_dias_atras) -> None:
    with engine.begin() as conn:
        conn.execute(sa_text(
            """
            INSERT INTO noticias.relatorios_email (
                setor, layout, unidade_de_tempo, data_display, quantos_dias_atras,
                paragrafos, noticias, html_content
            ) VALUES (
                :setor, :layout, :unidade_de_tempo, :data_display, :quantos_dias_atras,
                CAST(:paragrafos AS JSONB), CAST(:noticias AS JSONB), :html_content
            )
            """), {
            "setor": setor, "layout": LAYOUT, "unidade_de_tempo": unidade_de_tempo,
            "data_display": data_display, "quantos_dias_atras": quantos_dias_atras,
            "paragrafos": json.dumps(_jsonable(paragrafos)),
            "noticias": json.dumps(_jsonable(noticias)),
            "html_content": html_content,
        })


def enviar_email(to: list[str], subject: str, html_content: str) -> None:
    """SMTP com STARTTLS; credenciais SMTP_HOST/PORT/USER/PASSWORD do .env."""
    host, user = os.getenv("SMTP_HOST"), os.getenv("SMTP_USER")
    senha, porta = os.getenv("SMTP_PASSWORD"), int(os.getenv("SMTP_PORT", "587"))
    if not (host and user and senha):
        raise RuntimeError("SMTP_HOST/SMTP_USER/SMTP_PASSWORD não definidos no .env")
    msg = MIMEMultipart()
    msg.attach(MIMEText(html_content, "html", "utf-8"))
    msg["From"], msg["To"], msg["Subject"] = user, ", ".join(to), subject
    with smtplib.SMTP(host, porta) as server:
        server.starttls()
        server.login(user, senha)
        server.sendmail(user, to, msg.as_string())


# ── rodada ───────────────────────────────────────────────────────────────────
def gerar_relatorios(engine=None, *, quantos_dias_atras=QUANTOS_DIAS_ATRAS,
                     numero_de_noticias=NUMERO_DE_NOTICIAS, setores=None,
                     dry_run=False, forcar_envio=False) -> dict:
    """Monta e grava o relatório de cada setor; envia e-mail só se ligado."""
    engine = engine or get_sqlalchemy_engine(NOTICIAS_PG)
    setores = setores or SETORES
    unidade_de_tempo = calcular_unidade_de_tempo(quantos_dias_atras)
    agora = pendulum.now("America/Sao_Paulo")

    dia_de_envio = agora.weekday() == DIA_ENVIO_EMAIL
    enviar = (ENVIO_EMAIL_ATIVO and not dry_run and bool(DESTINATARIOS)
              and (dia_de_envio or forcar_envio))
    if not enviar:
        motivo = ("e-mail desligado (NOTICIAS_ENVIO_EMAIL)" if not ENVIO_EMAIL_ATIVO
                  else "sem destinatários" if not DESTINATARIOS
                  else "dry_run" if dry_run else "fora do dia de envio")
        log.info(f"[relatorio] {motivo}: relatórios só gravados em noticias.relatorios_email.")

    aux = pd.read_sql("SELECT * FROM noticias.noticias_temp", con=engine)
    if aux is None or aux.empty:
        return {"setores": 0, "enviados": 0}
    aux.columns = aux.columns.str.lower()
    aux["data"] = pd.to_datetime(aux["data"], dayfirst=True, errors="coerce")
    aux["ranking"] = pd.to_numeric(aux["ranking"], errors="coerce")
    aux = aux[aux["data"].notna() & (aux["data"].dt.date >= agora.subtract(days=quantos_dias_atras).date())]
    for c in ("tag", "setor", "titulo", "texto", "link", "fonte"):
        aux[c] = aux[c].fillna("").astype(str)

    client = llm.cliente(MODELO_REDACAO, timeout=TIMEOUT_REDACAO_S, temperature=0.2,
                         reasoning_effort="none", max_tokens=4096)
    data_display = (_data_dia(agora) if "dia" in unidade_de_tempo else _data_semana(agora))
    if not enviar:
        data_display = f"Atualizado em {_data_dia(agora)}"

    gerados = enviados = 0
    for setor in [s for s in aux["setor"].unique() if s in setores]:
        df_setor = aux[(aux["setor"] == setor) & aux["tag"].isin([g for g, _ in GEOS])]
        if df_setor.empty:
            continue
        try:
            aux_filtrado = (
                df_setor.sort_values(["tag", "ranking", "data"], ascending=[True, False, False],
                                     na_position="last")
                .groupby("tag", group_keys=False).head(numero_de_noticias)
                .drop_duplicates(subset=["link"], keep="first")
            )
            noticias = [{"id": i, "titulo": r["titulo"], "texto": r["texto"], "link": r["link"],
                         "fonte": r["fonte"], "data": r["data"], "tag": r["tag"],
                         "ranking": r["ranking"]}
                        for i, r in enumerate(aux_filtrado.to_dict("records"), start=1)]
            if not noticias:
                continue
            html_content, paragrafos = montar_relatorio(
                client, setor, unidade_de_tempo, data_display, noticias, aux_filtrado)
            gerados += 1
            if enviar:
                enviar_email(DESTINATARIOS,
                             f"{setor} — notícias {_preposicao_tempo(unidade_de_tempo)}",
                             html_content)
                enviados += 1
            try:
                _salvar_relatorio(engine, setor, html_content, paragrafos, noticias,
                                  unidade_de_tempo, data_display, quantos_dias_atras)
            except Exception as e:  # noqa: BLE001
                log.error(f"[relatorio] falha ao gravar o relatório de '{setor}': {e}")
            time.sleep(1)
        except (llm.CotaEsgotada, llm.SemChavesValidas) as e:
            log.error(f"[relatorio] parando: {e}")
            break
        except Exception as e:  # noqa: BLE001 — um setor não derruba os outros
            log.error(f"[relatorio] setor '{setor}' falhou — seguindo: {e}")
    return {"setores": gerados, "enviados": enviados}
