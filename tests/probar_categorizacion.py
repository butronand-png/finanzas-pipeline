import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.categorizador import categorizar

df = pd.read_parquet("data/output/transacciones.parquet")

# Aplicar categorizador
df[["comercio", "categoria"]] = df["descripcion_raw"].apply(
    lambda d: pd.Series(categorizar(d))
)

# Cobertura
total = len(df)
sin_categoria = (df["categoria"] == "sin_categoria").sum()
cobertura = 100 * (total - sin_categoria) / total

print(f"Total transacciones: {total}")
print(f"Categorizadas:       {total - sin_categoria} ({cobertura:.1f}%)")
print(f"Sin categoría:       {sin_categoria} ({100 - cobertura:.1f}%)")

# Resumen por categoría
print(f"\n=== Por categoría ===\n")
agrupado = df.groupby("categoria").agg(
    n=("descripcion_raw", "count"),
    deposito=("deposito", "sum"),
    retiro=("retiro", "sum"),
).reset_index()
agrupado["flujo_neto"] = agrupado["deposito"] - agrupado["retiro"]
agrupado = agrupado.sort_values("retiro", ascending=False)

print(f"{'categoria':<30} {'n':>4}  {'depósitos':>12}  {'retiros':>12}  {'flujo':>12}")
print("-" * 80)
for _, row in agrupado.iterrows():
    print(f"{row['categoria']:<30} {row['n']:>4}  "
          f"${row['deposito']:>10,.2f}  ${row['retiro']:>10,.2f}  "
          f"${row['flujo_neto']:>+10,.2f}")

# Top códigos SIN categorizar — para ampliar el diccionario
print(f"\n=== Top 20 sin categorizar (para ampliar diccionario) ===\n")
sin_cat_df = df[df["categoria"] == "sin_categoria"].copy()

def extraer_codigo_corto(desc):
    if "|" in desc:
        return desc.split("|", 1)[1].strip()[:50]
    return desc[:50]

sin_cat_df["codigo"] = sin_cat_df["descripcion_raw"].apply(extraer_codigo_corto)
top_sin_cat = sin_cat_df["codigo"].value_counts().head(20)

for codigo, count in top_sin_cat.items():
    monto = sin_cat_df[sin_cat_df["codigo"] == codigo]["retiro"].sum() + \
            sin_cat_df[sin_cat_df["codigo"] == codigo]["deposito"].sum()
    print(f"  {count:>3}x  ${monto:>10,.2f}  {codigo}")