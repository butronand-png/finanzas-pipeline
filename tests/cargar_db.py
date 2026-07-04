import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.db import cargar_dataframe

df = pd.read_parquet("data/output/transacciones.parquet")
print(f"Cargando {len(df)} transacciones a Postgres...\n")

stats = cargar_dataframe(df)

print("=== Resultado ===")
for k, v in stats.items():
    print(f"  {k}: {v}")