"""Materializa um asset do industry fora do daemon do Dagster: python rodar_asset.py <asset>"""
import os
import sys

from dotenv import dotenv_values

os.chdir('C:/Users/augus/Documents/codigos/industry')
sys.path.insert(0, '.')
for k, v in dotenv_values('.env').items():
    os.environ.setdefault(k, v or '')

import dagster as dg  # noqa: E402

from industry.definitions import defs  # noqa: E402

nome = sys.argv[1]
r = dg.materialize([defs.get_assets_def(nome)], resources=defs.resources)
print(nome, 'ok' if r.success else 'FALHOU')
sys.exit(0 if r.success else 1)
