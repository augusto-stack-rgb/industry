"""O que as fontes de ganchos compartilham: o universo de empresas, o HTTP e a
gravacao deduplicada."""
import re
import unicodedata
from datetime import date

import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ..supabase import Supabase

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
FRENTE = 'clientes'

# palavras que nao identificam a empresa no nome
GENERICAS = {'ltda', 'me', 'epp', 'eireli', 'sa', 's', 'a', 'cia', 'e', 'de', 'da', 'do', 'das', 'dos',
             'industria', 'industrial', 'comercio', 'comercial', 'servicos', 'brasil', 'do brasil',
             'importacao', 'exportacao', 'distribuidora', 'grupo', 'empresa', 'solucoes', 'limitada'}


def norm(s: str | None) -> str:
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def nucleo(nome: str | None) -> set[str]:
    """Palavras que identificam a empresa: tira sufixos societarios e genericas."""
    return {p for p in norm(nome).split() if p not in GENERICAS and len(p) > 1}


# palavras de ramo: sozinhas nao identificam ninguem ("nutricao animal" casa
# com qualquer fabricante de racao)
SETORIAIS = {'nutricao', 'animal', 'alimentos', 'alimenticios', 'transportes', 'transportadora', 'logistica',
             'plasticos', 'metalurgica', 'metais', 'equipamentos', 'maquinas', 'pecas', 'componentes',
             'quimica', 'quimicos', 'produtos', 'tecnologia', 'engenharia', 'construcoes', 'construtora',
             'materiais', 'eletricos', 'automacao', 'agricola', 'agro', 'textil', 'moveis', 'embalagens',
             'combustiveis', 'distribuicao', 'atacado', 'atacadista', 'participacoes', 'holding'}


def mesma_empresa(achado: str, *nomes: str | None) -> bool:
    """O nome achado (vaga, financiamento) e de uma das grafias da empresa?
    Exige todas as palavras do nucleo de uma das grafias, para nao confundir
    Aeromob com Aeromot, e ao menos uma palavra que nao seja de ramo."""
    a = nucleo(achado)
    if not a:
        return False
    for n in nomes:
        nn = nucleo(n)
        if not nn:
            continue
        comum_ = nn & a
        if not (comum_ - SETORIAIS):
            # so palavras de ramo em comum: exige o nome inteiro igual
            if norm(n) and norm(n) == norm(achado):
                return True
            continue
        if nn <= a or (a <= nn and len(a) >= 2):
            return True
    return False


def empresas(supabase: Supabase) -> list[dict]:
    """Universo: empresas do enriquecimento com site encontrado, com a avaliacao."""
    with supabase.conectar() as c:
        c.row_factory = dict_row
        return c.execute("""
            select e.cnpj, e.razao_social, e.nome_fantasia, e.site_url, e.uf, e.municipio,
                   a.pacote, a.nota
              from enriquecimento e left join clientes_avaliacao a using (cnpj)
             where e.site_encontrado and e.site_url is not null
             order by a.nota desc nulls last, e.cnpj""").fetchall()


def cliente_http(timeout: float = 20) -> httpx.Client:
    return httpx.Client(headers={'User-Agent': UA, 'Accept-Language': 'pt-BR,pt;q=0.9'},
                        timeout=timeout, follow_redirects=True, verify=False)


def gancho(cnpj, fonte, tipo, pacote, data_fato, fato, evidencia, chave, **detalhes) -> dict:
    if isinstance(data_fato, str):
        data_fato = date.fromisoformat(data_fato[:10]) if data_fato else None
    return {'cnpj': cnpj, 'fonte': fonte, 'tipo': tipo, 'pacote': pacote, 'data': data_fato,
            'fato': fato, 'evidencia': evidencia, 'detalhes': detalhes or None,
            'chave': f'{fonte}|{tipo}|{chave}'}


def gravar(supabase: Supabase, ganchos: list[dict]) -> int:
    """Insere so o que e novo (a `chave` deduplica). Devolve quantos entraram."""
    if not ganchos:
        return 0
    cols = ['cnpj', 'fonte', 'tipo', 'pacote', 'data', 'fato', 'evidencia', 'detalhes', 'chave']
    sql = (f'insert into ganchos ({", ".join(cols)}) values ({", ".join(["%s"] * len(cols))}) '
           'on conflict (chave) do nothing returning id')
    novos = 0
    with supabase.conectar() as c:
        for g in ganchos:
            r = c.execute(sql, [Jsonb(g[k]) if k == 'detalhes' and g[k] else g[k] for k in cols]).fetchone()
            novos += r is not None
    return novos


def estados(supabase: Supabase, fonte: str) -> dict[str, dict]:
    with supabase.conectar() as c:
        return {r[0]: r[1] for r in c.execute(
            'select cnpj, estado from ganchos_estado where fonte = %s', (fonte,))}


def salvar_estados(supabase: Supabase, fonte: str, novos: dict[str, dict]) -> None:
    with supabase.conectar() as c:
        c.cursor().executemany(
            'insert into ganchos_estado (cnpj, fonte, estado) values (%s, %s, %s) on conflict (cnpj, fonte) '
            'do update set estado = excluded.estado, atualizado_em = now()',
            [(cnpj, fonte, Jsonb(e)) for cnpj, e in novos.items()])
