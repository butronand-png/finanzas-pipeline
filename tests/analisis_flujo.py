import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.categorizador import categorizar

df = pd.read_parquet("data/output/transacciones.parquet")
df[["comercio", "categoria"]] = df["descripcion_raw"].apply(
    lambda d: pd.Series(categorizar(d))
)

print(f"Periodo: {df['fecha'].min().date()} a {df['fecha'].max().date()}\n")

# --- Análisis 1: Flujo bruto vs neto sin transferencias entre cuentas ---
print("=" * 60)
print("ANÁLISIS 1: Flujo bruto vs corregido")
print("=" * 60)

bruto_dep = df["deposito"].sum()
bruto_ret = df["retiro"].sum()
bruto = bruto_dep - bruto_ret
print(f"Bruto:")
print(f"  Depósitos: ${bruto_dep:,.2f}")
print(f"  Retiros:   ${bruto_ret:,.2f}")
print(f"  Neto:      ${bruto:+,.2f}\n")

# Excluir transferencias entre cuentas tuyas
excluir = ["transferencia_nu", "transferencia_otros"]
df_real = df[~df["categoria"].isin(excluir)]
real_dep = df_real["deposito"].sum()
real_ret = df_real["retiro"].sum()
real = real_dep - real_ret
print(f"Excluyendo transferencias entre cuentas propias:")
print(f"  Depósitos: ${real_dep:,.2f}")
print(f"  Retiros:   ${real_ret:,.2f}")
print(f"  Neto:      ${real:+,.2f}\n")

# Solo gasto discrecional (excluir ITAM también)
excluir_disc = excluir + ["itam_colegiatura", "itam_otros"]
df_disc = df[~df["categoria"].isin(excluir_disc)]
disc_dep = df_disc["deposito"].sum()
disc_ret = df_disc["retiro"].sum()
disc = disc_dep - disc_ret
print(f"Flujo discrecional (sin transferencias ni ITAM):")
print(f"  Ingresos:  ${disc_dep:,.2f}")
print(f"  Gastos:    ${disc_ret:,.2f}")
print(f"  Neto:      ${disc:+,.2f}\n")

# --- Análisis 2: Por mes ---
print("=" * 60)
print("ANÁLISIS 2: Flujo discrecional por mes")
print("=" * 60)
df_disc = df_disc.copy()
df_disc["mes"] = df_disc["fecha"].dt.to_period("M")
mensual = df_disc.groupby("mes").agg(
    ingresos=("deposito", "sum"),
    gastos=("retiro", "sum"),
).reset_index()
mensual["neto"] = mensual["ingresos"] - mensual["gastos"]

print(f"\n{'mes':<10}  {'ingresos':>12}  {'gastos':>12}  {'neto':>12}")
print("-" * 55)
for _, row in mensual.iterrows():
    print(f"{str(row['mes']):<10}  ${row['ingresos']:>10,.2f}  ${row['gastos']:>10,.2f}  ${row['neto']:>+10,.2f}")

prom_neto = mensual['neto'].mean()
print(f"\nPromedio mensual neto: ${prom_neto:+,.2f}")