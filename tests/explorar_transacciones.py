import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

df = pd.read_parquet("data/output/transacciones.parquet")


def extraer_codigo(desc):
    if "|" not in desc:
        return desc[:50]
    despues_pipe = desc.split("|", 1)[1].strip()
    return despues_pipe[:50]


df["codigo"] = df["descripcion"].apply(extraer_codigo)
todos = df["codigo"].value_counts()

print(f"Códigos únicos: {len(todos)}\n")
print(f"{'freq':>5}  {'monto':>12}  codigo")
print("-" * 80)
for codigo, count in todos.items():
    monto_total = df[df["codigo"] == codigo]["retiro"].sum() + df[df["codigo"] == codigo]["deposito"].sum()
    print(f"{count:>5}  ${monto_total:>10,.2f}  {codigo}")