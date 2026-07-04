"""
tests/sanidad_flujo_consolidado.py — Sanidad de la vista flujo_consolidado.

Verifica que el flujo neto consolidado de un mes de muestra (SELECT sobre
la vista, filtrando transferencias internas) cuadre contra un cálculo
manual hecho COMPLETAMENTE FUERA de la BD:

- Santander: suma de (deposito - retiro) del parquet del pipeline,
  excluyendo las filas cuya clave de rastreo empareja con Nu.
- Nu: suma de montos de la sección cuenta del extractor, sin movimientos
  internos de cajitas, excluyendo las filas emparejadas con Santander.
- El emparejamiento se recalcula en pandas (mismo criterio que
  tests/diagnostico_reconciliacion_nu.py), sin leer las tablas nuevas.

Así el test detecta errores tanto de carga como de la lógica de la vista.

Uso:
    uv run python tests/sanidad_flujo_consolidado.py [YYYY-MM]
    (default: 2025-03)
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.db import conexion
from src.extractor_nu import extraer_directorio

RAIZ = Path(__file__).resolve().parents[1]
RE_CLAVE_SANT = re.compile(r"CLAVE DE RASTREO ([A-Z0-9]+)")
TOLERANCIA = 0.01

VERDE, ROJO, FIN = "\033[92m", "\033[91m", "\033[0m"


def flujo_vista(mes: str) -> dict[str, float]:
    """Flujo neto del mes según la vista, excluyendo transferencias internas."""
    with conexion() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT banco, COALESCE(SUM(monto), 0)
            FROM flujo_consolidado
            WHERE NOT es_transferencia_interna
              AND date_trunc('month', fecha) = %(mes)s::date
            GROUP BY banco
        """, {"mes": f"{mes}-01"})
        return {banco: float(neto) for banco, neto in cur.fetchall()}


def flujo_manual(mes: str) -> dict[str, float]:
    """Mismo flujo calculado en pandas sin tocar las tablas nuevas."""
    periodo = pd.Period(mes)

    # Lado Santander: parquet del pipeline (fuente de la tabla transacciones)
    trans = pd.read_parquet(RAIZ / "data" / "output" / "transacciones.parquet")
    sant = trans[trans["banco"] == "santander"].copy()
    sant["clave"] = (sant["descripcion_raw"].str.upper()
                     .str.extract(RE_CLAVE_SANT, expand=False))

    # Lado Nu: extractor directo de los PDFs
    nu = extraer_directorio(RAIZ / "data" / "pdfs_nu")
    nu_cuenta = nu[(nu["seccion"] == "cuenta")
                   & ~nu["es_movimiento_interno"]].copy()
    nu_cuenta["clave"] = nu_cuenta["clave_rastreo"].str.upper()

    # Reconciliación recalculada: intersección de claves de rastreo
    claves_pares = (set(sant["clave"].dropna())
                    & set(nu_cuenta["clave"].dropna()))

    # Netos del mes excluyendo AMBOS lados de las transferencias emparejadas
    # (una transferencia entre cuentas propias no es ingreso ni gasto)
    sant_mes = sant[(sant["fecha"].dt.to_period("M") == periodo)
                    & ~sant["clave"].isin(claves_pares)]
    nu_mes = nu_cuenta[
        (pd.to_datetime(nu_cuenta["fecha"]).dt.to_period("M") == periodo)
        & ~nu_cuenta["clave"].isin(claves_pares)]

    return {
        "santander": float((sant_mes["deposito"] - sant_mes["retiro"]).sum()),
        "nu": float(nu_mes["monto"].sum()),
    }


def main() -> int:
    mes = sys.argv[1] if len(sys.argv) > 1 else "2025-03"
    print(f"\nSanidad de flujo_consolidado — mes de muestra: {mes}\n")

    vista = flujo_vista(mes)
    manual = flujo_manual(mes)

    ok_global = True
    print(f"{'banco':<11} {'vista (SQL)':>14} {'manual (pandas)':>16} {'diff':>10}")
    print("-" * 55)
    for banco in sorted(set(vista) | set(manual)):
        v, m = vista.get(banco, 0.0), manual.get(banco, 0.0)
        diff = v - m
        ok = abs(diff) <= TOLERANCIA
        ok_global &= ok
        marca = f"{VERDE}✓{FIN}" if ok else f"{ROJO}✗{FIN}"
        print(f"{banco:<11} {v:>14,.2f} {m:>16,.2f} {diff:>10.2f}  {marca}")

    total_v, total_m = sum(vista.values()), sum(manual.values())
    diff_total = total_v - total_m
    ok_total = abs(diff_total) <= TOLERANCIA
    ok_global &= ok_total
    print("-" * 55)
    print(f"{'TOTAL':<11} {total_v:>14,.2f} {total_m:>16,.2f} {diff_total:>10.2f}"
          f"  {VERDE + '✓' + FIN if ok_total else ROJO + '✗' + FIN}")

    if ok_global:
        print(f"\n{VERDE}El flujo neto consolidado cuadra contra el cálculo "
              f"manual (±${TOLERANCIA}).{FIN}")
        return 0
    print(f"\n{ROJO}DISCREPANCIA: la vista no cuadra con el cálculo manual.{FIN}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
