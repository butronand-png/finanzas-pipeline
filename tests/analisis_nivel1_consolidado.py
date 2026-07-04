"""
tests/analisis_nivel1_consolidado.py — Nivel 1: distribución empírica del
flujo neto mensual consolidado (ambos bancos).

Fuente: vista flujo_consolidado con WHERE NOT es_transferencia_interna.
La vista ya excluye los movimientos internos de cajitas (solo emite la
sección 'cuenta' de Nu sin es_movimiento_interno) y el filtro deja fuera
la doble cara de las transferencias Santander<->Nu reconciliadas, así que
cada peso se cuenta una sola vez.

Decisiones metodológicas:

- VENTANA: ene-2024 → may-2026. Jun-2026 se EXCLUYE: es un mes parcial
  (el último estado de Santander corta el 15-jun y Nu no tiene PDF de
  junio); incluirlo sesgaría la distribución con un medio-mes.
- COBERTURA ASIMÉTRICA: ene→may-2024 solo tienen datos de Nu (la
  cobertura de Santander arranca el 19-jun-2024). Esos meses subestiman
  el flujo consolidado real. Se conservan porque el corte por regímenes
  (punto siguiente) los aísla; el docstring lo deja explícito en vez de
  imputar datos que no existen.
- DOS REGÍMENES: la distribución global mezcla la fase de acumulación
  (fondeo inicial de cajitas, hasta 2024-04) con la fase de drenaje
  (desde 2024-05). Reportar solo estadísticas globales induciría a error
  (media inflada por los +$18-24k iniciales), así que TODO el análisis se
  repite por sub-periodo. El corte 2024-04/2024-05 viene del análisis de
  drenaje: abril 2024 es el último mes con flujo de cajitas fuertemente
  positivo antes del cambio de signo sostenido.
- OUTLIERS: criterio IQR clásico (fuera de [Q1-1.5·IQR, Q3+1.5·IQR]).
  Es un criterio descriptivo, no un test; con n~29 los percentiles son
  ruidosos y el 1.5 es convención, no óptimo.
- PERCENTILES: interpolación lineal (default de numpy), sd muestral
  (ddof=1).
- HISTOGRAMA: 9 bins de ancho fijo entre min y max de cada serie —
  suficiente para n<=29; más bins fragmentan, menos ocultan la forma.

Output: tabla + histogramas en texto y
data/output/flujo_mensual_consolidado.csv (mes, flujo, regimen).

Uso:
    uv run python tests/analisis_nivel1_consolidado.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.db import conexion

RAIZ = Path(__file__).resolve().parents[1]
CSV_OUT = RAIZ / "data" / "output" / "flujo_mensual_consolidado.csv"

MES_INICIO, MES_FIN = "2024-01", "2026-05"   # jun-2026 parcial: fuera
CORTE_REGIMEN = "2024-04"                    # <= acumulación, > drenaje

GRIS, FIN = "\033[90m", "\033[0m"


def serie_mensual() -> pd.Series:
    """Flujo neto por mes desde la vista, sin transferencias internas."""
    with conexion() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT to_char(fecha, 'YYYY-MM') AS mes, SUM(monto) AS flujo
            FROM flujo_consolidado
            WHERE NOT es_transferencia_interna
            GROUP BY 1 ORDER BY 1
        """)
        s = pd.Series({m: float(f) for m, f in cur.fetchall()}, name="flujo")
    return s.loc[MES_INICIO:MES_FIN]


def estadisticas(s: pd.Series) -> dict:
    q = s.quantile([0.10, 0.25, 0.50, 0.75, 0.90])
    return {
        "n": len(s), "media": s.mean(), "mediana": s.median(),
        "sd": s.std(ddof=1),
        "p10": q[0.10], "p25": q[0.25], "p50": q[0.50],
        "p75": q[0.75], "p90": q[0.90],
    }


def outliers_iqr(s: pd.Series) -> pd.Series:
    """Valores fuera de [Q1-1.5·IQR, Q3+1.5·IQR]."""
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    return s[(s < q1 - 1.5 * iqr) | (s > q3 + 1.5 * iqr)]


def imprimir_stats(titulo: str, s: pd.Series) -> None:
    e = estadisticas(s)
    print(f"\n  {titulo} (n={e['n']})")
    print(f"    media {e['media']:>12,.2f} | mediana {e['mediana']:>12,.2f} "
          f"| sd {e['sd']:>12,.2f}")
    print(f"    p10 {e['p10']:>12,.2f} | p25 {e['p25']:>12,.2f} | "
          f"p50 {e['p50']:>12,.2f} | p75 {e['p75']:>12,.2f} | "
          f"p90 {e['p90']:>12,.2f}")
    outs = outliers_iqr(s)
    if len(outs):
        print(f"    outliers IQR ({len(outs)}):")
        for mes, v in outs.items():
            print(f"      {mes}: {v:>12,.2f}")
    else:
        print("    outliers IQR: ninguno")


def histograma(titulo: str, s: pd.Series, bins: int = 9,
               ancho: int = 40) -> None:
    print(f"\n  Histograma — {titulo}")
    cortes = pd.cut(s, bins=bins)
    conteos = cortes.value_counts().sort_index()
    maximo = conteos.max()
    for intervalo, n in conteos.items():
        barra = "█" * round(n / maximo * ancho) if n else ""
        print(f"    [{intervalo.left:>10,.0f}, {intervalo.right:>10,.0f}) "
              f"{n:>3d} {barra}")


def main() -> int:
    s = serie_mensual()
    acum = s.loc[:CORTE_REGIMEN]
    dren = s.loc["2024-05":]

    print("\nANÁLISIS NIVEL 1 — flujo neto mensual consolidado "
          f"({MES_INICIO} → {MES_FIN}; jun-2026 excluido por parcial)")

    print("\n1) Serie mensual (ingresos - gastos):")
    for mes, v in s.items():
        regimen = "acumulación" if mes <= CORTE_REGIMEN else "drenaje"
        print(f"   {mes}  {v:>12,.2f}  {GRIS}{regimen}{FIN}")

    print("\n2-3) Estadísticas y outliers:")
    imprimir_stats("GLOBAL (mezcla ambos regímenes — interpretar con cuidado)", s)

    print("\n4) Por régimen:")
    imprimir_stats(f"ACUMULACIÓN ({MES_INICIO} → {CORTE_REGIMEN}, solo Nu "
                   "en BD: subestima el flujo real)", acum)
    imprimir_stats(f"DRENAJE (2024-05 → {MES_FIN})", dren)

    print("\n5) Histogramas:")
    histograma("serie completa", s)
    histograma("régimen de drenaje (2024-05 →)", dren)

    df = s.to_frame()
    df["regimen"] = ["acumulacion" if m <= CORTE_REGIMEN else "drenaje"
                     for m in df.index]
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    df.round(2).to_csv(CSV_OUT, index_label="mes")
    print(f"\nCSV: {CSV_OUT.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
