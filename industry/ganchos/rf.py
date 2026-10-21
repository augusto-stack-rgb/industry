"""Mudancas cadastrais na Receita Federal, pela API publica minhareceita.org
(espelho da base da RF, um CNPJ por consulta).

Ganchos com data propria (aparecem ja na primeira rodada):
  socio_novo        socio ou administrador que entrou nos ultimos 12 meses
  saiu_do_simples   exclusao do Simples Nacional nos ultimos 24 meses (costuma
                    ser a empresa passando do teto de faturamento)
  regime_mudou      forma de tributacao do ultimo ano diferente da do anterior
Ganchos por comparacao com a rodada anterior (estado em ganchos_estado):
  capital_aumentou  capital social maior que o da ultima leitura
  cnae_novo         atividade secundaria que nao existia na ultima leitura
"""
import time
from datetime import date, timedelta

from . import comum

FONTE = 'rf'
API = 'https://minhareceita.org/{}'


def ler(cli, cnpj) -> dict | None:
    for tentativa in range(3):
        r = cli.get(API.format(cnpj))
        if r.status_code == 200:
            return r.json()
        if r.status_code == 429:
            time.sleep(30 * (tentativa + 1))
            continue
        return None
    return None


def coletar(supabase, log, pausa: float = 1.5) -> dict:
    emps = comum.empresas(supabase)
    antes = comum.estados(supabase, FONTE)
    hoje = date.today()
    ganchos, estado, falhas = [], {}, 0
    with comum.cliente_http() as cli:
        for e in emps:
            d = ler(cli, e['cnpj'])
            time.sleep(pausa)
            if not d:
                falhas += 1
                continue
            c, url = e['cnpj'], API.format(e['cnpj'])
            for s in d.get('qsa') or []:
                ent = s.get('data_entrada_sociedade')
                if ent and date.fromisoformat(ent) >= hoje - timedelta(days=365):
                    ganchos.append(comum.gancho(
                        c, FONTE, 'socio_novo', 'ambos', ent,
                        f'{s.get("nome_socio")} entrou como {(s.get("qualificacao_socio") or "sócio").lower()} '
                        f'em {date.fromisoformat(ent).strftime("%d/%m/%Y")}', url,
                        f'{c}|{s.get("nome_socio")}|{ent}', faixa_etaria=s.get('faixa_etaria')))
            exc = d.get('data_exclusao_do_simples')
            if exc and not d.get('opcao_pelo_simples') and date.fromisoformat(exc) >= hoje - timedelta(days=730):
                ganchos.append(comum.gancho(
                    c, FONTE, 'saiu_do_simples', 'dashboard', exc,
                    f'Saiu do Simples Nacional em {date.fromisoformat(exc).strftime("%d/%m/%Y")}', url, f'{c}|{exc}'))
            regimes = sorted(d.get('regime_tributario') or [], key=lambda x: x.get('ano') or 0)
            if len(regimes) >= 2 and regimes[-1].get('forma_de_tributacao') != regimes[-2].get('forma_de_tributacao') \
                    and (regimes[-1].get('ano') or 0) >= hoje.year - 2:
                a, b = regimes[-2], regimes[-1]
                ganchos.append(comum.gancho(
                    c, FONTE, 'regime_mudou', 'dashboard', f'{b["ano"]}-01-01',
                    f'Tributação passou de {a.get("forma_de_tributacao")} para {b.get("forma_de_tributacao")} '
                    f'em {b["ano"]}', url, f'{c}|{b["ano"]}'))
            capital = float(d.get('capital_social') or 0)
            cnaes = sorted(str(x['codigo']) for x in d.get('cnaes_secundarios') or [])
            ant = antes.get(c)
            if ant:
                if capital > float(ant.get('capital') or 0) * 1.05:
                    ganchos.append(comum.gancho(
                        c, FONTE, 'capital_aumentou', 'ambos', hoje,
                        f'Capital social subiu de R$ {ant["capital"]:,.0f} para R$ {capital:,.0f}'.replace(',', '.'),
                        url, f'{c}|{capital}'))
                desc = {str(x['codigo']): x.get('descricao') for x in d.get('cnaes_secundarios') or []}
                for cod in set(cnaes) - set(ant.get('cnaes') or []):
                    ganchos.append(comum.gancho(
                        c, FONTE, 'cnae_novo', 'ambos', hoje, f'Nova atividade registrada: {desc.get(cod)}',
                        url, f'{c}|{cod}'))
            estado[c] = {'capital': capital, 'cnaes': cnaes, 'em': hoje.isoformat()}
    comum.salvar_estados(supabase, FONTE, estado)
    novos = comum.gravar(supabase, ganchos)
    log.info(f'{len(estado)} empresas lidas, {falhas} sem resposta, {len(ganchos)} ganchos, {novos} novos')
    return {'empresas': len(emps), 'lidas': len(estado), 'ganchos': len(ganchos), 'novos': novos,
            'primeira_leitura': sum(1 for x in estado if x not in antes)}
