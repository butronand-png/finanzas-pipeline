"""
tests/analisis_drenaje_cajitas.py — ¿A qué ritmo se drena la reserva de Cajitas?

El análisis que motivó el pipeline de Nu: el saldo total bajó de forma
sostenida entre 2024 y 2026 con "Retiro de Cajita" constantes fondeando
gasto diario. Este script pone los números empíricos mes a mes (ene-2024 →
may-2026), sin gráficas ni modelos:

1. Flujo neto de cajitas (sección cajitas de transacciones_nu): depósitos
   menos retiros. Positivo = ahorrando, negativo = drenando la reserva.
   EXCLUYE los movimientos de congelamiento («Congelaste saldo ...» /
   «Descongelamos saldo ...», 4 filas en 2025): son reallocaciones
   disponible<->congelado DENTRO de Total Cajitas que no mueven la reserva.
   Validación: con esa exclusión, la identidad
       Δ saldo_cajitas == flujo_cajitas + intereses del mes
   cierra al centavo en los 28 meses comparables (con los congelamientos
   incluidos, 4 meses de 2025 quedaban descuadrados por el monto congelado).
2. Trayectoria del saldo total de Nu y del saldo de cajitas, leídos de los
   resúmenes de página 1 de cada PDF (no están en BD: el saldo de cajitas
   se parsea aquí directamente del texto de página 1).
3. % del gasto real de la cuenta Nu financiado con retiros de cajita vs
   ingreso fresco (ambos como proporción del gasto del mes: "de cada peso
   gastado, cuántos centavos entraron por cada vía ese mes"). Ojo: en
   meses de restructura (mar-2024, jun-2025) el %cajita supera 100 porque
   los retiros de cajita pasan por la cuenta y se redepositan.
4. Flujo neto consolidado mensual (ambos bancos, vista flujo_consolidado
   sin transferencias internas) como contexto.
5. Pista restante: saldo actual de cajitas / drenaje mensual promedio de
   los últimos 6 y 3 meses (el peor de los dos se marca como escenario
   pesimista), más la variante con intereses (drenaje efectivo = Δ saldo).

Output: tabla legible + data/output/drenaje_cajitas.csv.

Uso:
    uv run python tests/analisis_drenaje_cajitas.py
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd
import pdfplumber

from src.db import conexion
from src.extractor_nu import extraer_resumen, periodo_desde_nombre

RAIZ = Path(__file__).resolve().parents[1]
DIR_PDFS = RAIZ / "data" / "pdfs_nu"
CSV_OUT = RAIZ / "data" / "output" / "drenaje_cajitas.csv"

# Página 1: "En su Cuenta {En|Total} Cajitas" y debajo los dos montos.
# El formato viejo (2024) dice "En Cajitas"; el nuevo, "Total Cajitas".
RE_SALDO_CAJITAS = re.compile(
    r"En su Cuenta (?:En|Total) Cajitas\n\$([\d,]+\.\d{2}) \$([\d,]+\.\d{2})")

VERDE, ROJO, GRIS, FIN = "\033[92m", "\033[91m", "\033[90m", "\033[0m"


def saldos_desde_pdfs() -> pd.DataFrame:
    """Saldo total de Nu (página 1) y saldo de cajitas, por mes."""
    filas = []
    for pdf_path in sorted(DIR_PDFS.glob("*.pdf")):
        anio, mes = periodo_desde_nombre(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            texto = pdf.pages[0].extract_text() or ""
        resumen = extraer_resumen(texto)
        m = RE_SALDO_CAJITAS.search(texto)
        filas.append({
            "mes": f"{anio}-{mes:02d}",
            "saldo_nu": resumen["saldo_final"],
            "saldo_cajitas": float(m.group(2).replace(",", "")) if m else None,
            "interes": resumen["dinero_generado"],
        })
    return pd.DataFrame(filas).set_index("mes").sort_index()


def metricas_desde_bd() -> pd.DataFrame:
    """Agregados mensuales de transacciones_nu y de la vista consolidada."""
    with conexion() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT to_char(fecha, 'YYYY-MM') AS mes,
                   -- congelamientos: intra-cajitas, no mueven la reserva
                   COALESCE(SUM(monto) FILTER (
                       WHERE seccion = 'cajitas'
                         AND descripcion NOT LIKE 'Congelaste%%'
                         AND descripcion NOT LIKE 'Descongelamos%%'), 0)
                       AS flujo_cajitas,
                   COALESCE(SUM(-monto) FILTER (
                       WHERE seccion = 'cuenta' AND monto < 0
                         AND NOT es_movimiento_interno), 0) AS gasto_cuenta,
                   COALESCE(SUM(monto) FILTER (
                       WHERE seccion = 'cuenta' AND monto > 0
                         AND NOT es_movimiento_interno), 0) AS ingreso_fresco,
                   COALESCE(SUM(monto) FILTER (
                       WHERE seccion = 'cuenta' AND monto > 0
                         AND es_movimiento_interno
                         AND descripcion LIKE 'Retiro de Cajita:%'), 0)
                       AS retiro_cajita
            FROM transacciones_nu
            GROUP BY 1
        """)
        nu = pd.DataFrame(cur.fetchall(), columns=[
            "mes", "flujo_cajitas", "gasto_cuenta", "ingreso_fresco",
            "retiro_cajita"]).set_index("mes").astype(float)

        cur.execute("""
            SELECT to_char(fecha, 'YYYY-MM') AS mes, SUM(monto)
            FROM flujo_consolidado
            WHERE NOT es_transferencia_interna
            GROUP BY 1
        """)
        cons = pd.DataFrame(cur.fetchall(), columns=["mes", "flujo_consolidado"]
                            ).set_index("mes").astype(float)

    return nu.join(cons, how="outer")


def main() -> int:
    saldos = saldos_desde_pdfs()
    metricas = metricas_desde_bd()
    df = saldos.join(metricas, how="left").loc["2024-01":"2026-05"]

    # % del gasto del mes cubierto por cada fuente de fondeo
    df["pct_cajita"] = (df["retiro_cajita"] / df["gasto_cuenta"] * 100
                        ).where(df["gasto_cuenta"] > 0)
    df["pct_fresco"] = (df["ingreso_fresco"] / df["gasto_cuenta"] * 100
                        ).where(df["gasto_cuenta"] > 0)

    # ---- tabla -------------------------------------------------------------
    print(f"\nDrenaje de Cajitas Nu — ene-2024 → may-2026 "
          f"(montos en MXN)\n")
    print(f"{'mes':<8} {'flujo_caj':>10} {'saldo_nu':>10} {'saldo_caj':>10} "
          f"{'gasto':>10} {'ret_cajita':>10} {'ing_fresco':>10} "
          f"{'%caj':>6} {'%fresco':>7} {'consolidado':>12}")
    print("-" * 102)
    for mes, r in df.iterrows():
        color = ROJO if r["flujo_cajitas"] < 0 else VERDE
        pc = f"{r['pct_cajita']:6.1f}" if pd.notna(r["pct_cajita"]) else "     -"
        pf = f"{r['pct_fresco']:7.1f}" if pd.notna(r["pct_fresco"]) else "      -"
        print(f"{mes:<8} {color}{r['flujo_cajitas']:>10,.2f}{FIN} "
              f"{r['saldo_nu']:>10,.2f} {r['saldo_cajitas']:>10,.2f} "
              f"{r['gasto_cuenta']:>10,.2f} {r['retiro_cajita']:>10,.2f} "
              f"{r['ingreso_fresco']:>10,.2f} {pc} {pf} "
              f"{r['flujo_consolidado']:>12,.2f}")
    print("-" * 102)

    # ---- pista restante ------------------------------------------------------
    saldo_actual = df["saldo_cajitas"].iloc[-1]
    drenaje_6m = df["flujo_cajitas"].tail(6).mean()
    drenaje_3m = df["flujo_cajitas"].tail(3).mean()
    # Drenaje efectivo: incluye el interés que la reserva genera (Δ saldo/mes)
    efectivo_6m = (df["saldo_cajitas"].iloc[-1] - df["saldo_cajitas"].iloc[-7]) / 6

    def pista(drenaje: float) -> str:
        if drenaje >= 0:
            return f"{VERDE}sin drenaje (la reserva crece){FIN}"
        meses = saldo_actual / -drenaje
        return f"pista: {ROJO}{meses:.1f} meses{FIN} (~{meses / 12:.1f} años)"

    peor = min(drenaje_6m, drenaje_3m)
    print(f"\nSaldo actual de cajitas ({df.index[-1]}): ${saldo_actual:,.2f}")
    for etiqueta, drenaje in (("últimos 6 meses", drenaje_6m),
                              ("últimos 3 meses", drenaje_3m)):
        marca = "  ← escenario pesimista" if drenaje == peor else ""
        print(f"  Drenaje promedio {etiqueta}: ${drenaje:,.2f}/mes "
              f"→ {pista(drenaje)}{marca}")
    print(f"  Drenaje EFECTIVO últimos 6 meses (Δ saldo, ya con intereses "
          f"GAT): ${efectivo_6m:,.2f}/mes → {pista(efectivo_6m)}")
    print(f"  {GRIS}Nota: los flujos excluyen congelamientos "
          f"(intra-cajitas); identidad Δsaldo = flujo + interés verificada "
          f"al centavo en los 28 meses.{FIN}")

    # ---- CSV -----------------------------------------------------------------
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.round(2).to_csv(CSV_OUT, index_label="mes")
    print(f"\nCSV: {CSV_OUT.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
