"""
tests/analisis_recurrencia.py — Nivel 3: gasto recurrente por comercio,
ambos bancos, y cruce con la pista de cajitas.

Universo de gasto (decisiones metodológicas):

- Santander: tabla `transacciones`, banco='santander', retiro>0. La llave
  de agrupación es `comercios.nombre` (cobertura 100% en gastos, ya
  normalizada por el categorizador), NO descripcion_raw (texto OCR libre).
- Nu: `transacciones_nu`, seccion='cuenta', monto<0, sin movimientos
  internos de cajitas. Llave: `descripcion` (Nu no está categorizado aún).
- EXCLUSIONES: (a) ambos lados de las transferencias reconciliadas
  Santander<->Nu (dinero entre cuentas propias, no gasto); (b) el comercio
  'Transferencia a Nu' completo — cubre las transferencias propias que no
  reconciliaron por falta de contraparte (ej. jun-2026 sin PDF de Nu).
- 'Pago a tu tarjeta de crédito Nu' SÍ cuenta como gasto: los estados de
  la tarjeta de crédito no están en el pipeline, así que el pago es el
  proxy observable de ese consumo.
- 'Otro comercio' (Santander) es un bucket catch-all del categorizador,
  no un comercio real; se conserva pero se marca con ⚠ en la tabla.

Normalización de la llave (reglas aplicadas, en orden):
  1. upper()                     — 'DLO*Uber eats' == 'DLO*UBER EATS'
  2. quitar sufijo de operación de Nu: ' COMPRA' / ' TRANSFERENCIA' al
     final de la descripción (es el tipo de operación, no el comercio)
  3. colapsar espacios múltiples a uno y strip
  4. SIN fusión a nivel marca: '7 ELEVEN T 1949' y '7 ELEVEN CAMPECHE'
     quedan separados. Fusionarlos requeriría un mapa curado; para
     detectar recurrencia (suscripciones, rentas) la sucursal exacta es
     incluso señal útil.
  5. La llave se comparte entre bancos: el mismo comercio pagado con
     distinta tarjeta es el mismo gasto recurrente.

Clasificación (sobre grupos con k>=3 transacciones, es decir >=2 gaps):
  - gaps = diferencias en días entre fechas consecutivas (ordenadas,
    mismos días producen gap 0 — legítimo: dos compras el mismo día no
    son patrón de suscripción). CV_gap = sd(gaps)/media(gaps), sd
    muestral (ddof=1).
  - CV_monto = sd(|montos|)/media(|montos|).
  - RECURRENTE_FIJO:     CV_gap < 0.3 y CV_monto < 0.3
  - FRECUENTE_VARIABLE:  CV_gap < 0.3 y CV_monto >= 0.3
  - EVENTUAL:            el resto
  Los umbrales 0.3 son ARBITRARIOS (convención tipo "CV bajo"): no hay
  base estadística fina con k chicas; 0.3 tolera ±30% de variación
  relativa, suficiente para atrapar suscripciones mensuales que caen en
  día 28/30/31 sin dejar pasar gasto discrecional. Sensibilidad: mover a
  0.25/0.35 cambia la frontera, no el orden de magnitud del costo fijo.

Métricas para RECURRENTE_FIJO:
  - periodo estimado = media de gaps (días)
  - monto típico = mediana de |montos| (robusta a un cargo atípico)
  - costo mensual equivalente = monto típico * 30.44/periodo
    (30.44 = 365.25/12, mes promedio)

Vigencia (corrección importante): una recurrencia puede haber TERMINADO
dentro del periodo de datos (ej. LENIGAS: mensual jul-2024→feb-2025 y
nada después). Sumar recurrencias muertas como "costo de vida fijo
actual" lo inflaría. Regla: un fijo está ACTIVO si su última transacción
cae a menos de 2 periodos del fin de los datos (2x tolera saltarse un
ciclo); el costo fijo vigente suma solo los activos y el histórico se
reporta como contexto.

Resumen y cruce con drenaje:
  - costo de vida fijo mensual = suma de los mensualizados fijos ACTIVOS.
  - gasto variable promedio mensual = promedio, sobre los últimos 6 meses
    completos (dic-2025→may-2026, mismo régimen que la pista de cajitas),
    del gasto NO clasificado como fijo. Se reporta también el promedio
    del periodo completo como contexto.
  - pista: lee saldo de cajitas y drenaje 6m de
    data/output/drenaje_cajitas.csv (correr antes
    tests/analisis_drenaje_cajitas.py). Escenario "solo gasto fijo":
    SUPUESTO simplificador de que cada peso de gasto variable eliminado
    mejora 1:1 el flujo de cajitas (nueva_tasa = drenaje + variable_prom).

Output: tablas + data/output/recurrencia_comercios.csv.

Uso:
    uv run python tests/analisis_recurrencia.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.db import conexion

RAIZ = Path(__file__).resolve().parents[1]
CSV_OUT = RAIZ / "data" / "output" / "recurrencia_comercios.csv"
CSV_DRENAJE = RAIZ / "data" / "output" / "drenaje_cajitas.csv"

K_MIN = 3
UMBRAL_CV = 0.3
DIAS_MES = 30.44                       # 365.25 / 12
VENTANA_RECIENTE = ("2025-12", "2026-05")   # últimos 6 meses completos

VERDE, AMARILLO, GRIS, FIN = "\033[92m", "\033[93m", "\033[90m", "\033[0m"

RE_SUFIJO_NU = re.compile(r" (COMPRA|TRANSFERENCIA)$")
RE_ESPACIOS = re.compile(r"\s+")


def normalizar(desc: str) -> str:
    """Reglas 1-3 del docstring: upper, sin sufijo de operación, espacios."""
    d = desc.upper()
    d = RE_SUFIJO_NU.sub("", d)
    return RE_ESPACIOS.sub(" ", d).strip()


def cargar_gastos() -> pd.DataFrame:
    """Gastos de ambos bancos: fecha, monto (positivo), llave normalizada."""
    with conexion() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT t.fecha, t.retiro, c.nombre, 'santander'
            FROM transacciones t
            JOIN comercios c ON c.id = t.comercio_id
            WHERE t.banco = 'santander' AND t.retiro > 0
              AND c.nombre <> 'Transferencia a Nu'
              AND NOT EXISTS (SELECT 1 FROM transferencias_reconciliadas r
                              WHERE r.santander_id = t.id)
            UNION ALL
            SELECT n.fecha, -n.monto, n.descripcion, 'nu'
            FROM transacciones_nu n
            WHERE n.seccion = 'cuenta' AND n.monto < 0
              AND NOT n.es_movimiento_interno
              AND NOT EXISTS (SELECT 1 FROM transferencias_reconciliadas r
                              WHERE r.nu_id = n.id)
        """)
        df = pd.DataFrame(cur.fetchall(),
                          columns=["fecha", "monto", "desc", "banco"])
    df["monto"] = df["monto"].astype(float)
    df["comercio"] = df["desc"].map(normalizar)
    return df


def metricas_grupo(g: pd.DataFrame) -> dict:
    fechas = pd.to_datetime(g["fecha"]).sort_values()
    gaps = fechas.diff().dt.days.dropna()
    montos = g["monto"]
    gap_medio = gaps.mean()
    cv_gap = gaps.std(ddof=1) / gap_medio if gap_medio > 0 else float("inf")
    cv_monto = montos.std(ddof=1) / montos.mean()
    if cv_gap < UMBRAL_CV and cv_monto < UMBRAL_CV:
        clase = "RECURRENTE_FIJO"
    elif cv_gap < UMBRAL_CV:
        clase = "FRECUENTE_VARIABLE"
    else:
        clase = "EVENTUAL"
    return {
        "k": len(g),
        "bancos": "+".join(sorted(g["banco"].unique())),
        "gap_medio_dias": gap_medio,
        "cv_gap": cv_gap,
        "cv_monto": cv_monto,
        "monto_mediano": montos.median(),
        "gasto_total": montos.sum(),
        "clase": clase,
        "mensualizado": (montos.median() * DIAS_MES / gap_medio
                         if gap_medio > 0 else None),
        "primera": fechas.iloc[0].date(),
        "ultima": fechas.iloc[-1].date(),
    }


def main() -> int:
    gastos = cargar_gastos()
    print(f"\nANÁLISIS DE RECURRENCIA — {len(gastos)} transacciones de "
          f"gasto ({(gastos['banco'] == 'santander').sum()} Santander + "
          f"{(gastos['banco'] == 'nu').sum()} Nu)")

    grupos = {c: metricas_grupo(g) for c, g in gastos.groupby("comercio")
              if len(g) >= K_MIN}
    res = pd.DataFrame(grupos).T.sort_values("gasto_total", ascending=False)
    res.index.name = "comercio"

    # Vigencia: activo si la última tx cae a <2 periodos del fin de datos
    fin_datos = pd.to_datetime(gastos["fecha"]).max()
    res["activo"] = [
        (fin_datos - pd.Timestamp(r["ultima"])).days <= 2 * r["gap_medio_dias"]
        for _, r in res.iterrows()]
    print(f"Comercios con k>={K_MIN}: {len(res)} "
          f"(cubren {res['gasto_total'].sum():,.0f} de "
          f"{gastos['monto'].sum():,.0f} MXN del gasto total)")

    conteo = res["clase"].value_counts()
    print(f"\nClasificación: " + " | ".join(
        f"{c}: {n}" for c, n in conteo.items()))

    # ---- recurrentes fijos ---------------------------------------------------
    fijos = res[res["clase"] == "RECURRENTE_FIJO"].sort_values(
        "mensualizado", ascending=False)
    print(f"\nRECURRENTE_FIJO (CV_gap y CV_monto < {UMBRAL_CV}):")
    print(f"{'comercio':<32} {'k':>3} {'periodo_d':>9} {'monto_med':>10} "
          f"{'mensual_eq':>10} {'desde':>11} {'hasta':>11}")
    print("-" * 95)
    for c, r in fijos.iterrows():
        marca = " ⚠bucket" if c == "OTRO COMERCIO" else ""
        estado = f" {VERDE}activo{FIN}" if r["activo"] else f" {GRIS}terminó{FIN}"
        print(f"{c[:32]:<32} {r['k']:>3} {r['gap_medio_dias']:>9.1f} "
              f"{r['monto_mediano']:>10,.2f} {r['mensualizado']:>10,.2f} "
              f"{r['primera']!s:>11} {r['ultima']!s:>11}{estado}{marca}")
    costo_fijo = fijos.loc[fijos["activo"], "mensualizado"].sum()
    costo_fijo_hist = fijos["mensualizado"].sum()
    print("-" * 95)
    print(f"{'COSTO DE VIDA FIJO MENSUAL (solo activos)':<46} "
          f"{costo_fijo:>10,.2f}")
    print(f"{GRIS}{'  histórico, incluyendo recurrencias terminadas':<46} "
          f"{costo_fijo_hist:>10,.2f}{FIN}")

    # ---- frecuentes variables --------------------------------------------------
    variables = res[res["clase"] == "FRECUENTE_VARIABLE"].sort_values(
        "gasto_total", ascending=False)
    print(f"\nFRECUENTE_VARIABLE (ritmo estable, monto variable):")
    print(f"{'comercio':<32} {'k':>3} {'periodo_d':>9} {'cv_monto':>8} "
          f"{'gasto_total':>12}")
    print("-" * 70)
    for c, r in variables.iterrows():
        marca = " ⚠bucket" if c == "OTRO COMERCIO" else ""
        print(f"{c[:32]:<32} {r['k']:>3} {r['gap_medio_dias']:>9.1f} "
              f"{r['cv_monto']:>8.2f} {r['gasto_total']:>12,.2f}{marca}")

    # ---- fijo vs variable -------------------------------------------------------
    comercios_fijos = set(fijos.index)
    gastos["mes"] = pd.to_datetime(gastos["fecha"]).dt.strftime("%Y-%m")
    no_fijo = gastos[~gastos["comercio"].isin(comercios_fijos)]
    var_por_mes = no_fijo.groupby("mes")["monto"].sum()
    var_reciente = var_por_mes.loc[
        VENTANA_RECIENTE[0]:VENTANA_RECIENTE[1]].mean()
    var_completo = var_por_mes.loc[:"2026-05"].mean()

    print(f"\nRESUMEN FIJO vs VARIABLE:")
    print(f"  Costo fijo mensual (recurrentes mensualizados): "
          f"${costo_fijo:>10,.2f}")
    print(f"  Gasto variable promedio/mes, últimos 6 meses completos "
          f"({VENTANA_RECIENTE[0]} → {VENTANA_RECIENTE[1]}): "
          f"${var_reciente:>10,.2f}")
    print(f"  {GRIS}Gasto variable promedio/mes, periodo completo: "
          f"${var_completo:,.2f} (contexto){FIN}")

    # ---- cruce con drenaje de cajitas -------------------------------------------
    print(f"\nCRUCE CON DRENAJE DE CAJITAS:")
    if not CSV_DRENAJE.exists():
        print(f"  No existe {CSV_DRENAJE.name}: corre antes "
              f"tests/analisis_drenaje_cajitas.py")
        return 1
    dren = pd.read_csv(CSV_DRENAJE, index_col="mes")
    saldo = dren["saldo_cajitas"].iloc[-1]
    tasa_actual = dren["flujo_cajitas"].tail(6).mean()
    tasa_solo_fijo = tasa_actual + var_reciente  # supuesto 1:1 (docstring)

    pista_actual = saldo / -tasa_actual if tasa_actual < 0 else float("inf")
    print(f"  Saldo de cajitas ({dren.index[-1]}): ${saldo:,.2f} | "
          f"drenaje 6m: ${tasa_actual:,.2f}/mes")
    print(f"  Pista ACTUAL:          {pista_actual:.1f} meses")
    if tasa_solo_fijo < 0:
        print(f"  Pista SOLO GASTO FIJO: {saldo / -tasa_solo_fijo:.1f} meses "
              f"(tasa ${tasa_solo_fijo:,.2f}/mes)")
    else:
        print(f"  Pista SOLO GASTO FIJO: {VERDE}∞ — recortando el gasto "
              f"variable (${var_reciente:,.2f}/mes) el flujo de cajitas "
              f"pasa a +${tasa_solo_fijo:,.2f}/mes: la reserva deja de "
              f"drenarse{FIN}")

    # ---- CSV ---------------------------------------------------------------------
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    res.round(3).to_csv(CSV_OUT)
    print(f"\nCSV: {CSV_OUT.relative_to(RAIZ)} "
          f"({len(res)} comercios, incluye los EVENTUAL no listados arriba)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
