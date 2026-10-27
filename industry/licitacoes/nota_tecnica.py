"""Divisão da nota técnica entre histórico e proposta, por regra explícita.

    python -m industry.licitacoes.nota_tecnica            # licitações abertas e pontuadas
    python -m industry.licitacoes.nota_tecnica --todas    # todas as pontuadas

Para cada licitação grava em ``licitacoes.nota_tecnica``:

- critério de julgamento sem nota técnica (menor preço, maior desconto, maior
  retorno econômico): ``tem_nota_tecnica = false``. Nesses, o histórico só
  aparece na habilitação, como aprova ou reprova;
- critério com técnica (técnica e preço, melhor técnica): lê o edital e os
  anexos e procura os critérios com pontos ("CRITÉRIO B Grau de desenvolvimento.
  Peso: 20 pontos", "Experiência da equipe: 30 pontos"). Cada critério vai para
  HISTÓRICO se o nome fala de experiência, atestado, qualificação, currículo,
  equipe, capacidade técnica, certificação ou formação; o resto é PROPOSTA.
  ``peso_historico`` e ``peso_proposta`` são percentuais do total de pontos;
- sem critério conhecido ou sem tabela de pontos achada: pesos nulos (não
  sabido). O trecho usado fica em ``evidencia``.

Grava também quem pode concorrer (``exige_cnpj`` e ``requisito_cnpj``), lido no
mesmo texto (ver ``participacao``):

- ``pessoa_fisica``: aceita CPF (a coleta marcou, ou o edital diz que pessoa
  física pode participar). ``exige_cnpj = false``;
- ``startup``: só startup da LC 182 (até 10 anos de CNPJ, receita até R$ 16 mi);
- ``restrito``: veda MEI, ou pede capital social / patrimônio líquido mínimo
  ou tempo mínimo de empresa;
- ``me_epp``: participação exclusiva de ME/EPP (MEI entra, é microempresa);
- ``comum``: pede CNPJ e o edital não traz nenhuma das restrições acima;
- nulo: sem edital legível e sem marcação, não sabido.

Nada aqui é LLM: a mesma entrada dá sempre o mesmo resultado.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
import zipfile

import requests

from industry.licitacoes import db

SEM_TECNICA = ("menor preco", "maior desconto", "maior retorno", "maior lance")
COM_TECNICA = ("tecnica e preco", "melhor tecnica", "tecnica")
HISTORICO = re.compile(r"experienc|atestad|qualificac|curricul|equipe|capacidade tecnica|certifica|"
                       r"formacao|titulac|portfolio|tempo de (atuacao|mercado)|historico|referencias", re.I)
# "CRITERIO B Grau de desenvolvimento da solucao. Peso: 20 pontos"
RX_CRITERIO = re.compile(r"criterio\s+([A-Z0-9]{1,2})\b[\s.:)-]*([^.:\n]{3,90}?)[.:]?\s*peso:?\s*(\d{1,3})\s*pontos", re.I)
# "Experiencia da equipe: 30 pontos" / "A potencial de resolucao do problema: 30 pontos"
RX_ITEM = re.compile(r"(?:^|[\s;(])([A-Za-z][A-Za-z ,/()-]{3,90}?)\s*[:=-]\s*(\d{1,3})\s*pontos", re.I)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s)


def _cemig(url: str) -> list[str]:
    """O portal de compras da Cemig não linka o arquivo: cada documento é um
    token JWT cujo payload traz ``targetUrl`` e ``description``. Fica o edital."""
    import base64
    html = requests.get(url, headers=H, timeout=60).text
    urls = []
    for tok, payload64 in re.findall(r"ver-documento/\?token=([\w-]+\.([\w-]+)\.[\w-]+)", html):
        try:
            payload = json.loads(base64.urlsafe_b64decode(payload64 + "=" * (-len(payload64) % 4)))
        except Exception:
            continue
        if "edital" in str(payload.get("description", "")).lower():
            urls.append(payload.get("targetUrl"))
    return [u for u in urls if u]


def _pdfs(url: str) -> list[bytes]:
    url = re.sub(r"(https://pncp\.gov\.br):\d+/", r"\1/", url)    # porta interna da API do PNCP
    if "app-compras.cemig.com.br/pesquisa" in url:
        return [b for u in _cemig(url) for b in _pdfs(u)]
    b = requests.get(url, headers=H, timeout=180).content
    if b[:5] == b"%PDF-":
        return [b]
    if b[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(b))
        return [z.read(n) for n in z.namelist() if n.lower().endswith(".pdf")]
    return []


def _texto(urls: list[str]) -> str:
    import pymupdf
    partes = []
    for u in dict.fromkeys(u for u in urls if u):
        try:
            for b in _pdfs(u):
                with pymupdf.open(stream=b, filetype="pdf") as d:
                    partes.append("\n".join(p.get_text() for p in d))
        except Exception:
            continue
    return _norm("\n".join(partes))


def criterios(texto: str) -> list[tuple[str, int]]:
    """[(nome do critério, pontos)] achados no texto, sem repetir o mesmo nome."""
    blocos: dict[str, int] = {}
    for m in RX_CRITERIO.finditer(texto):
        blocos.setdefault(m.group(2).strip().lower(), int(m.group(3)))
    lista: dict[str, int] = {}
    for m in RX_ITEM.finditer(texto):
        nome = m.group(1).strip().lower()
        if len(nome.split()) >= 2 and "total" not in nome and "maxim" not in nome:
            lista.setdefault(nome, int(m.group(2)))
    # duas leituras do mesmo edital (blocos "CRITERIO X ... Peso" e lista "nome: N
    # pontos"); fica a que soma mais perto de 100, a escala usual
    candidatas = [c for c in (blocos, lista) if c]
    if not candidatas:
        return []
    return list(min(candidatas, key=lambda c: abs(sum(c.values()) - 100)).items())


def avaliar(criterio_julgamento: str | None, texto: str) -> dict:
    crit = _norm(criterio_julgamento or "").lower()
    if any(c in crit for c in SEM_TECNICA):
        return {"tem_nota_tecnica": False, "peso_historico": None, "peso_proposta": None,
                "metodo": "critério sem nota técnica", "evidencia": criterio_julgamento}
    itens = criterios(texto) if texto else []
    total = sum(p for _, p in itens)
    if itens and 50 <= total <= 1000:
        hist = sum(p for n, p in itens if HISTORICO.search(n))
        return {"tem_nota_tecnica": True,
                "peso_historico": round(100 * hist / total), "peso_proposta": round(100 * (total - hist) / total),
                "metodo": "pontos lidos no edital",
                "evidencia": "; ".join(f"{n}: {p}" for n, p in itens)[:1000]}
    tem = True if any(c in crit for c in COM_TECNICA) else None
    return {"tem_nota_tecnica": tem, "peso_historico": None, "peso_proposta": None,
            "metodo": "pesos não achados" if texto else "sem edital legível",
            "evidencia": criterio_julgamento}


# Quem pode concorrer. O texto vem de _norm (sem acento), comparado sem caixa.
# Só frases que abrem a disputa a pessoa física. "Pessoa física" aparece em todo
# edital em outro sentido (CPF na habilitação, sancionados, subcontratação, IR),
# e a amostra de 27/10/2026 mostrou que casar a palavra solta erra mais que acerta.
_PF_SIM = re.compile(r"poder(a|ao) participar[^.;]{0,100}?pessoas? fisicas?|"
                     r"(aberta|abertas|abertos) (a|as|para) pessoas? fisicas?|"
                     r"(sera|serao) (admitid|permitid)a?s? a participacao de pessoas? fisicas?|"
                     r"contratacao de pessoas? fisicas? ou juridicas?,? isoladamente", re.I)
_PF_NAO = re.compile(r"veda|nao (e|sera|serao|podera|poderao)|proibid|impedid|exceto|salvo|caso admitid|"
                     r"que (integre|esteja|se encontre|possua|mantenha)|sancionad|suspens|inidone|"
                     r"exclusivamente (a |as )?pessoas? juridicas?", re.I)
_STARTUP = re.compile(r"startups?[^.;]{0,300}?(16\.?000\.?000|receita bruta)|"
                      r"(16\.?000\.?000|receita bruta)[^.;]{0,300}?startups?|enquadramento como startup", re.I)
_RESTRITO = [
    ("veda MEI", re.compile(r"(vedad|nao (sera|serao|podera|poderao)|proibid)[^.;]{0,80}?"
                            r"(microempreendedor(es)? individua|\bmei\b)", re.I)),
    ("capital ou patrimônio mínimo", re.compile(r"(capital social|patrimonio liquido)( integralizado)? minimo|"
                                                r"(capital social|patrimonio liquido)[^.;]{0,40}?(nao inferior|igual ou superior)", re.I)),
    ("tempo mínimo de empresa", re.compile(r"(minimo de|pelo menos|no minimo) \d+ (\(\w+\) )?anos de "
                                           r"(existencia|constituicao|atividade|funcionamento|inscricao)", re.I)),
]


def _trecho(texto: str, m: re.Match) -> str:
    return texto[max(0, m.start() - 60): m.end() + 60].strip()


def participacao(texto: str, objeto: str = "", beneficio_me_epp: str | None = None,
                 pessoa_fisica: bool = False) -> dict:
    """{exige_cnpj, requisito_cnpj, requisito_evidencia}: quem pode concorrer."""
    def r(exige, req, ev):
        return {"exige_cnpj": exige, "requisito_cnpj": req, "requisito_evidencia": (ev or "")[:500] or None}
    alvo = _norm(f"{objeto or ''}\n{texto or ''}")
    if pessoa_fisica:
        return r(False, "pessoa_fisica", "marcada na coleta (aviso aceita consultor individual)")
    sim = next((m for m in _PF_SIM.finditer(alvo) if not _PF_NAO.search(_trecho(alvo, m))), None)
    if sim:
        return r(False, "pessoa_fisica", _trecho(alvo, sim))
    exclusiva = "exclusiva" in (beneficio_me_epp or "").lower()
    if not texto:
        return r(True, "me_epp", beneficio_me_epp) if exclusiva else r(None, None, None)
    m = _STARTUP.search(alvo)
    if m:
        return r(True, "startup", _trecho(alvo, m))
    for nome, rx in _RESTRITO:
        m = rx.search(alvo)
        if m:
            return r(True, "restrito", f"{nome}: {_trecho(alvo, m)}")
    if exclusiva:
        return r(True, "me_epp", beneficio_me_epp)
    return r(True, "comum", "edital lido: nenhuma restrição de porte, tempo ou capital, nem pessoa física")


def calcular(todas: bool = False) -> dict:
    """Avalia e grava; devolve contagens por método (metadata do asset)."""
    from sqlalchemy import text as sa
    eng = db.get_engine()
    filtro = "" if todas else """
        and coalesce(pp.data_encerramento_proposta, d.data_encerramento_proposta::date,
                     case when b.data_encerramento_proposta ~ '^\\d{4}-\\d{2}-\\d{2}'
                          then left(b.data_encerramento_proposta, 10)::date end) >= current_date"""
    with eng.begin() as c:
        c.execute(sa("""create table if not exists licitacoes.nota_tecnica (
            id_unificado text primary key, tem_nota_tecnica boolean, peso_historico integer,
            peso_proposta integer, metodo text, evidencia text, atualizado_em timestamptz default now())"""))
        for col, tipo in (("exige_cnpj", "boolean"), ("requisito_cnpj", "text"), ("requisito_evidencia", "text")):
            c.execute(sa(f"alter table licitacoes.nota_tecnica add column if not exists {col} {tipo}"))
        linhas = c.execute(sa(f"""
            select gf.id_unificado, coalesce(d.criterio_julgamento, b.criterio_julgamento), d.link_edital, d.anexos,
                   b.edital_url, b.link, gf.objeto, d.beneficio_me_epp, coalesce(b.pessoa_fisica = 'sim', false)
              from licitacoes.gold_filtros gf
              join licitacoes.bronze b on b.id_unificado = gf.id_unificado
              left join licitacoes.detalhes d on d.id_unificado = gf.id_unificado
              left join licitacoes.prazos_proposta pp on pp.id_unificado = gf.id_unificado
             where true {filtro}""")).fetchall()
    resultados = []
    for idu, crit, link_edital, anexos, edital_url, link, objeto, me_epp, pf in linhas:
        crit_n = _norm(crit or "").lower()
        # o edital é lido sempre: mesmo no menor preço, diz quem pode concorrer
        urls = [link_edital, edital_url]
        try:
            urls += [x.get("url") for x in (json.loads(anexos) if isinstance(anexos, str) else anexos or [])
                     if isinstance(x, dict)]
        except Exception:
            pass
        if link and (link.lower().endswith(".pdf") or "app-compras.cemig.com.br" in link):
            urls.append(link)
        texto = _texto(urls)
        if not crit_n:   # sem critério no cadastro: tenta no próprio edital
            m = re.search(r"criterio de julgamento[^.]{0,40}?(menor preco|maior desconto|tecnica e preco|melhor tecnica)",
                          texto, re.I)
            crit = m.group(1) if m else crit
        r = {**avaliar(crit, texto), **participacao(texto, objeto, me_epp, pf)}
        resultados.append({"id_unificado": idu, **r})
        print(f"{idu[:40]:<40} {r['metodo']:<26} hist={r['peso_historico']} prop={r['peso_proposta']} "
              f"cnpj={r['requisito_cnpj']}")
    with eng.begin() as c:
        for r in resultados:
            c.execute(sa("""insert into licitacoes.nota_tecnica
                (id_unificado, tem_nota_tecnica, peso_historico, peso_proposta, metodo, evidencia,
                 exige_cnpj, requisito_cnpj, requisito_evidencia, atualizado_em)
                values (:id_unificado, :tem_nota_tecnica, :peso_historico, :peso_proposta, :metodo, :evidencia,
                        :exige_cnpj, :requisito_cnpj, :requisito_evidencia, now())
                on conflict (id_unificado) do update set tem_nota_tecnica = excluded.tem_nota_tecnica,
                  peso_historico = excluded.peso_historico, peso_proposta = excluded.peso_proposta,
                  metodo = excluded.metodo, evidencia = excluded.evidencia, exige_cnpj = excluded.exige_cnpj,
                  requisito_cnpj = excluded.requisito_cnpj, requisito_evidencia = excluded.requisito_evidencia,
                  atualizado_em = now()"""), r)
    contagem = {}
    for r in resultados:
        contagem[r["metodo"]] = contagem.get(r["metodo"], 0) + 1
        k = f"cnpj: {r['requisito_cnpj'] or 'não sabido'}"
        contagem[k] = contagem.get(k, 0) + 1
    print(f"{len(resultados)} licitações avaliadas: {contagem}")
    return {"avaliadas": len(resultados), **contagem}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--todas", action="store_true")
    calcular(p.parse_args().todas)


if __name__ == "__main__":
    main()
