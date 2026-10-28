"""Coletores do universo de compras públicas, um dia por chamada.

- PNCP, API de consulta (Brasil inteiro): ``contratacoes`` (por modalidade) e
  ``contratos``. Máximo de 50 registros por página. Medido em 27/10/2026: cerca de 10
  requisições em rajada passam e depois vem 429 em HTML, sem dizer o limite. Daí a
  pausa entre páginas e a espera que dobra a cada 429.
- Dados Abertos do Compras.gov.br (``itens`` e ``resultados``): espelho do PNCP só
  para o que passa pelo Compras.gov.br. Até 500 por página e sem 429 nos testes. A
  data final do filtro é exclusiva (dia d..d+1), e os dados chegam com cerca de 1 mês
  de atraso (em 27/10, outubro ainda vinha vazio).

Cada função devolve a lista de linhas no formato da tabela de ``schema.sql``, com a
resposta original em ``dados``.
"""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests

from industry.licitacoes.net import ColetorBloqueado

log = logging.getLogger(__name__)

PNCP_CONSULTA = "https://pncp.gov.br/api/consulta/v1"
COMPRAS_GOV = "https://dadosabertos.compras.gov.br/modulo-contratacoes"
# Todas as modalidades do PNCP: o universo de mercado inclui leilão, inexigibilidade
# e credenciamento (o filtro do que interessa é por perfil, depois da coleta).
MODALIDADES_PNCP = list(range(1, 14))
ESPERA_429_S = 20.0
TENTATIVAS = 7
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0", "Accept": "application/json"}


class Ritmo:
    """Pausa entre requisições ao PNCP que se ajusta sozinha. Cada 429 custa 20 s ou
    mais de espera; com pausa fixa de 0,8 s o pregão de um dia levou 10 s por página
    (27/10/2026). Começa em 3 s (ritmo seguro medido em 15/10), sobe 1 s a cada 429 e
    desce 0,1 s a cada 20 páginas seguidas sem erro, até o piso."""

    def __init__(self, inicial: float = 3.0, piso: float = 1.5, teto: float = 10.0):
        self.pausa, self.piso, self.teto, self._ok = inicial, piso, teto, 0

    def sucesso(self):
        self._ok += 1
        if self._ok >= 20:
            self.pausa, self._ok = max(self.piso, self.pausa - 0.1), 0

    def bloqueio(self):
        self.pausa, self._ok = min(self.teto, self.pausa + 1.0), 0


RITMO_PNCP = Ritmo()


def _get(s: requests.Session, url: str, params: dict, ritmo: Ritmo | None = None) -> dict | None:
    """Uma página; ``None`` = sem conteúdo (204). Em 429, 5xx ou erro de rede espera
    e tenta de novo, dobrando a espera."""
    espera = ESPERA_429_S
    motivo = ""
    for _ in range(TENTATIVAS):
        try:
            r = s.get(url, params=params, headers=H, timeout=90)
            if r.status_code == 204:
                return None
            if r.status_code == 200 and r.text.lstrip()[:1] == "{":
                if ritmo:
                    ritmo.sucesso()
                return r.json()
            motivo = f"HTTP {r.status_code}"
            if r.status_code == 429 and ritmo:
                ritmo.bloqueio()
        except requests.RequestException as e:
            motivo = type(e).__name__
        time.sleep(espera)
        espera = min(espera * 2, 300)
    raise ColetorBloqueado(f"{url.rsplit('/', 1)[-1]} {params}: falhou ({motivo})")


def _paginar(s, url: str, params: dict, chave_lista: str, tamanho: int, pausa: float | Ritmo):
    """Lê as páginas até acabar; devolve (registros, páginas). ``pausa`` é fixa (s) ou
    um ``Ritmo`` adaptativo."""
    regs, pagina, total = [], 1, None
    ritmo = pausa if isinstance(pausa, Ritmo) else None
    while True:
        d = _get(s, url, {**params, "pagina": pagina, "tamanhoPagina": tamanho}, ritmo)
        # pausa depois de TODA requisição, inclusive a última: a próxima chamada
        # (outra modalidade, outro dia) sai logo em seguida
        time.sleep(ritmo.pausa if ritmo else pausa)
        lote = (d or {}).get(chave_lista) or []
        regs += lote
        total = (d or {}).get("totalPaginas") or 0
        if not lote or pagina >= total:
            return regs, pagina
        pagina += 1


def _ts(v):
    return v or None


def _dia(v):
    return (v or "")[:10] or None


# ── PNCP ──────────────────────────────────────────────────────────────────────
def contratacoes(s: requests.Session, dia: date, modalidade: int) -> tuple[list[dict], int]:
    ymd = dia.strftime("%Y%m%d")
    regs, pags = _paginar(s, f"{PNCP_CONSULTA}/contratacoes/publicacao",
                          {"dataInicial": ymd, "dataFinal": ymd, "codigoModalidadeContratacao": modalidade},
                          "data", 50, RITMO_PNCP)
    linhas = []
    for x in regs:
        org, un, amp = x.get("orgaoEntidade") or {}, x.get("unidadeOrgao") or {}, x.get("amparoLegal") or {}
        linhas.append({
            "numero_controle_pncp": x.get("numeroControlePNCP"),
            "ano": x.get("anoCompra"), "sequencial": x.get("sequencialCompra"),
            "numero_compra": x.get("numeroCompra"), "processo": x.get("processo"),
            "orgao_cnpj": org.get("cnpj"), "orgao_nome": org.get("razaoSocial"),
            "esfera": org.get("esferaId"), "poder": org.get("poderId"),
            "unidade_codigo": un.get("codigoUnidade"), "unidade_nome": un.get("nomeUnidade"),
            "uf": un.get("ufSigla"), "municipio": un.get("municipioNome"), "codigo_ibge": un.get("codigoIbge"),
            "modalidade_id": x.get("modalidadeId"), "modalidade_nome": x.get("modalidadeNome"),
            "modo_disputa_id": x.get("modoDisputaId"), "modo_disputa_nome": x.get("modoDisputaNome"),
            "instrumento_nome": x.get("tipoInstrumentoConvocatorioNome"),
            "amparo_legal_codigo": amp.get("codigo"), "amparo_legal_nome": amp.get("nome"),
            "srp": x.get("srp"), "objeto": x.get("objetoCompra"),
            "informacao_complementar": x.get("informacaoComplementar"),
            "situacao_id": x.get("situacaoCompraId"), "situacao_nome": x.get("situacaoCompraNome"),
            "valor_estimado": x.get("valorTotalEstimado"), "valor_homologado": x.get("valorTotalHomologado"),
            "data_publicacao": _ts(x.get("dataPublicacaoPncp")),
            "data_abertura_proposta": _ts(x.get("dataAberturaProposta")),
            "data_encerramento_proposta": _ts(x.get("dataEncerramentoProposta")),
            "data_atualizacao": _ts(x.get("dataAtualizacaoGlobal") or x.get("dataAtualizacao")),
            "sistema_origem": x.get("usuarioNome"), "link_sistema_origem": x.get("linkSistemaOrigem"),
            "dados": x,
        })
    return linhas, pags


def contratos(s: requests.Session, dia: date) -> tuple[list[dict], int]:
    ymd = dia.strftime("%Y%m%d")
    regs, pags = _paginar(s, f"{PNCP_CONSULTA}/contratos", {"dataInicial": ymd, "dataFinal": ymd},
                          "data", 50, RITMO_PNCP)
    linhas = []
    for x in regs:
        org, un = x.get("orgaoEntidade") or {}, x.get("unidadeOrgao") or {}
        cat, tipo = x.get("categoriaProcesso") or {}, x.get("tipoContrato") or {}
        linhas.append({
            "numero_controle_pncp": x.get("numeroControlePNCP"),
            "numero_controle_compra": x.get("numeroControlePncpCompra"),
            "numero_controle_ata": x.get("numeroControlePncpAta"),
            "ano": x.get("anoContrato"), "sequencial": x.get("sequencialContrato"),
            "tipo_contrato": tipo.get("nome"), "categoria_id": cat.get("id"), "categoria": cat.get("nome"),
            "orgao_cnpj": org.get("cnpj"), "orgao_nome": org.get("razaoSocial"),
            "esfera": org.get("esferaId"), "poder": org.get("poderId"),
            "unidade_codigo": un.get("codigoUnidade"), "unidade_nome": un.get("nomeUnidade"),
            "uf": un.get("ufSigla"), "municipio": un.get("municipioNome"), "codigo_ibge": un.get("codigoIbge"),
            "fornecedor_ni": x.get("niFornecedor"), "fornecedor_nome": x.get("nomeRazaoSocialFornecedor"),
            "fornecedor_tipo_pessoa": x.get("tipoPessoa"),
            "subcontratado_ni": x.get("niFornecedorSubContratado"),
            "subcontratado_nome": x.get("nomeFornecedorSubContratado"),
            "objeto": x.get("objetoContrato"),
            "valor_inicial": x.get("valorInicial"), "valor_global": x.get("valorGlobal"),
            "valor_acumulado": x.get("valorAcumulado"),
            "data_assinatura": _dia(x.get("dataAssinatura")),
            "vigencia_inicio": _dia(x.get("dataVigenciaInicio")), "vigencia_fim": _dia(x.get("dataVigenciaFim")),
            "data_publicacao": _ts(x.get("dataPublicacaoPncp")),
            "data_atualizacao": _ts(x.get("dataAtualizacaoGlobal") or x.get("dataAtualizacao")),
            "sistema_origem": x.get("usuarioNome"),
            "dados": x,
        })
    return linhas, pags


# ── Compras.gov.br (Dados Abertos) ────────────────────────────────────────────
def _intervalo(dia: date) -> tuple[str, str]:
    return dia.isoformat(), (dia + timedelta(days=1)).isoformat()   # fim exclusivo


def itens(s: requests.Session, dia: date) -> tuple[list[dict], int]:
    ini, fim = _intervalo(dia)
    regs, pags = _paginar(s, f"{COMPRAS_GOV}/2_consultarItensContratacoes_PNCP_14133",
                          {"dataInclusaoPncpInicial": ini, "dataInclusaoPncpFinal": fim},
                          "resultado", 500, 0.2)
    return [{
        "id_compra_item": x.get("idCompraItem"),
        "numero_controle_compra": x.get("numeroControlePNCPCompra") or x.get("idContratacaoPNCP"),
        "numero_item": x.get("numeroItemPncp"),
        "descricao": x.get("descricaoResumida") or x.get("descricaodetalhada"),
        "material_ou_servico": x.get("materialOuServico"), "categoria_nome": x.get("itemCategoriaNome"),
        "cod_item_catalogo": x.get("codItemCatalogo"), "codigo_classe": x.get("codigoClasse"),
        "codigo_grupo": x.get("codigoGrupo"), "codigo_pdm": x.get("codigoPdm"), "nome_pdm": x.get("nomePdm"),
        "codigo_ncm": x.get("codigoNCM"), "unidade_medida": x.get("unidadeMedida"),
        "quantidade": x.get("quantidade"), "valor_unitario_estimado": x.get("valorUnitarioEstimado"),
        "valor_total": x.get("valorTotal"), "criterio_julgamento": x.get("criterioJulgamentoNome"),
        "situacao": x.get("situacaoCompraItemNome"), "tipo_beneficio": x.get("tipoBeneficioNome"),
        "tem_resultado": x.get("temResultado"), "orgao_cnpj": x.get("orgaoEntidadeCnpj"),
        "data_inclusao": _ts(x.get("dataInclusaoPncp")), "data_atualizacao": _ts(x.get("dataAtualizacaoPncp")),
        "dados": x,
    } for x in regs if x.get("idCompraItem")], pags


def resultados(s: requests.Session, dia: date) -> tuple[list[dict], int]:
    ini, fim = _intervalo(dia)
    regs, pags = _paginar(s, f"{COMPRAS_GOV}/3_consultarResultadoItensContratacoes_PNCP_14133",
                          {"dataResultadoPncpInicial": ini, "dataResultadoPncpFinal": fim},
                          "resultado", 500, 0.2)
    return [{
        "id_compra_item": x.get("idCompraItem"), "sequencial_resultado": x.get("sequencialResultado"),
        "numero_controle_compra": x.get("numeroControlePNCPCompra") or x.get("idContratacaoPNCP"),
        "numero_item": x.get("numeroItemPncp"),
        "fornecedor_ni": x.get("niFornecedor"), "fornecedor_nome": x.get("nomeRazaoSocialFornecedor"),
        "tipo_pessoa": x.get("tipoPessoa"), "porte_id": x.get("porteFornecedorId"),
        "porte_nome": x.get("porteFornecedorNome"), "natureza_juridica": x.get("naturezaJuridicaNome"),
        "ordem_classificacao_srp": x.get("ordemClassificacaoSrp"),
        "quantidade": x.get("quantidadeHomologada"), "valor_unitario": x.get("valorUnitarioHomologado"),
        "valor_total": x.get("valorTotalHomologado"), "percentual_desconto": x.get("percentualDesconto"),
        "situacao": x.get("situacaoCompraItemResultadoNome"), "beneficio_meepp": x.get("aplicacaoBeneficioMeepp"),
        "uf": x.get("unidadeOrgaoUfSigla"), "orgao_cnpj": x.get("orgaoEntidadeCnpj"),
        "data_resultado": _ts(x.get("dataResultadoPncp")), "data_atualizacao": _ts(x.get("dataAtualizacaoPncp")),
        "dados": x,
    } for x in regs if x.get("idCompraItem") and x.get("sequencialResultado") is not None], pags
