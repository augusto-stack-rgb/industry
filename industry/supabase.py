"""Acesso ao Postgres do Supabase: upsert que preserva o que o home edita, e
registro de execucoes que o home mostra na pagina Pipelines."""
from contextlib import contextmanager

import dagster as dg
import psycopg
from psycopg.types.json import Jsonb


class Supabase(dg.ConfigurableResource):
    # Use a connection string do *session pooler* (IPv4); a conexao direta do
    # plano gratis e so IPv6.
    db_url: str

    def conectar(self):
        return psycopg.connect(self.db_url, autocommit=True)

    def upsert(self, tabela: str, linhas: list[dict], chave: str, lote: int = 1000) -> int:
        """Insere ou atualiza SO as colunas presentes em `linhas`.

        As pipelines nunca mandam status, notas, site ou contato, entao uma nova
        coleta nao apaga o que foi editado no home.
        """
        if not linhas:
            return 0
        cols = list(linhas[0])
        atualiza = ', '.join(f'{c} = excluded.{c}' for c in cols if c != chave)
        sql = (f'insert into {tabela} ({", ".join(cols)}) '
               f'values ({", ".join(["%s"] * len(cols))}) '
               f'on conflict ({chave}) do update set {atualiza}, atualizado_em = now()')
        with self.conectar() as con, con.cursor() as cur:
            for i in range(0, len(linhas), lote):
                cur.executemany(sql, [
                    [Jsonb(v) if isinstance(v, (dict, list)) else v for v in (l[c] for c in cols)]
                    for l in linhas[i:i + lote]])
        return len(linhas)

    @contextmanager
    def execucao(self, context: dg.AssetExecutionContext, frente: str, agenda: str | None = None):
        """Registra a execucao do asset em pipelines/pipeline_runs.

        Uso: `with supabase.execucao(context, 'jobs') as run: ...; run['linhas'] = n`
        """
        nome = context.asset_key.to_user_string()
        descricao = context.assets_def.descriptions_by_key.get(context.asset_key)
        run = {'linhas': None, 'mensagem': None}
        with self.conectar() as con:
            con.execute(
                'insert into pipelines (nome, frente, descricao, agenda, ultima_execucao) '
                'values (%s, %s, %s, %s, now()) on conflict (nome) do update set '
                'frente = excluded.frente, descricao = excluded.descricao, '
                'agenda = excluded.agenda, ultima_execucao = now()',
                (nome, frente, descricao, agenda))
            rid = con.execute('insert into pipeline_runs (pipeline) values (%s) returning id',
                              (nome,)).fetchone()[0]
        try:
            yield run
        except Exception as e:
            self._fechar(rid, 'erro', run['linhas'], f'{type(e).__name__}: {e}'[:2000])
            raise
        self._fechar(rid, 'ok', run['linhas'], run['mensagem'])

    def _fechar(self, rid, status, linhas, mensagem):
        with self.conectar() as con:
            con.execute('update pipeline_runs set terminado_em = now(), status = %s, '
                        'linhas = %s, mensagem = %s where id = %s',
                        (status, linhas, mensagem, rid))
