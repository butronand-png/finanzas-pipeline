"""
tests/cargar_transacciones_nu.py — Carga los movimientos de Nu a Postgres.

Alimenta el schema de sql/004_schema_nu.sql:
1. Corre src/extractor_nu.py sobre data/pdfs_nu/ e inserta en
   `transacciones_nu` con ON CONFLICT (pdf_origen, orden_en_pdf) DO NOTHING
   — reprocesar es un no-op, no duplica.
2. Materializa `transferencias_reconciliadas`: empareja por clave de
   rastreo las transferencias SPEI de Nu contra las claves `CLAVE DE
   RASTREO ...` de las descripciones OCR de Santander (tabla
   `transacciones`), igual que tests/diagnostico_reconciliacion_nu.py.

NOTA: no confundir con tests/cargar_db_nu.py (Bloque D), que carga el
parquet unificado a la tabla `transacciones`. Este script alimenta las
tablas nuevas dedicadas a Nu.

Uso:
    uv run python tests/cargar_transacciones_nu.py
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
from src.extractor_nu import extraer_pdf_nu

RAIZ = Path(__file__).resolve().parents[1]
DIR_PDFS = RAIZ / "data" / "pdfs_nu"

RE_CLAVE_SANT = re.compile(r"CLAVE DE RASTREO ([A-Z0-9]+)")

SQL_INSERT_MOVIMIENTO = """
    INSERT INTO transacciones_nu
        (fecha, descripcion, monto, seccion, es_movimiento_interno,
         detalle_spei, banco_contraparte, clabe_contraparte, clave_rastreo,
         monto_usd, tipo_cambio, pdf_origen, pagina, orden_en_pdf)
    VALUES
        (%(fecha)s, %(descripcion)s, %(monto)s, %(seccion)s,
         %(es_movimiento_interno)s, %(detalle_spei)s, %(banco_contraparte)s,
         %(clabe_contraparte)s, %(clave_rastreo)s, %(monto_usd)s,
         %(tipo_cambio)s, %(pdf_origen)s, %(pagina)s, %(orden_en_pdf)s)
    ON CONFLICT (pdf_origen, orden_en_pdf) DO NOTHING
"""

SQL_INSERT_PAR = """
    INSERT INTO transferencias_reconciliadas
        (santander_id, nu_id, clave_rastreo, monto)
    VALUES (%(santander_id)s, %(nu_id)s, %(clave_rastreo)s, %(monto)s)
    ON CONFLICT (santander_id) DO NOTHING
"""


def _nulo_si_nan(v):
    """NaN/None de pandas -> None de SQL."""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else v


def cargar_movimientos(cur) -> tuple[int, int]:
    """Extrae los PDFs de Nu e inserta en transacciones_nu. -> (nuevas, total)"""
    nuevas, total = 0, 0
    for pdf_path in sorted(DIR_PDFS.glob("*.pdf")):
        df = extraer_pdf_nu(pdf_path)["movimientos"]
        for orden, row in enumerate(df.itertuples(index=False)):
            cur.execute(SQL_INSERT_MOVIMIENTO, {
                "fecha":                 row.fecha,
                "descripcion":           row.descripcion,
                "monto":                 round(float(row.monto), 2),
                "seccion":               row.seccion,
                "es_movimiento_interno": bool(row.es_movimiento_interno),
                "detalle_spei":          _nulo_si_nan(row.detalle_spei),
                "banco_contraparte":     _nulo_si_nan(row.banco_contraparte),
                "clabe_contraparte":     _nulo_si_nan(row.clabe_contraparte),
                "clave_rastreo":         _nulo_si_nan(row.clave_rastreo),
                "monto_usd":             _nulo_si_nan(row.monto_usd),
                "tipo_cambio":           _nulo_si_nan(row.tipo_cambio),
                "pdf_origen":            row.pdf_origen,
                "pagina":                int(row.pagina),
                "orden_en_pdf":          orden,
            })
            nuevas += cur.rowcount
            total += 1
    return nuevas, total


def materializar_reconciliacion(cur) -> tuple[int, int]:
    """
    Empareja Nu vs Santander por clave de rastreo e inserta los pares.
    -> (pares nuevos, pares totales encontrados)
    """
    # Claves del lado Santander (desde la descripción OCR)
    cur.execute("""
        SELECT id, descripcion_raw, deposito, retiro
        FROM transacciones
        WHERE banco = 'santander' AND descripcion_raw ILIKE '%%clave de rastreo%%'
    """)
    por_clave: dict[str, tuple[int, float]] = {}
    for id_, desc, dep, ret in cur.fetchall():
        m = RE_CLAVE_SANT.search(desc.upper())
        if m:
            por_clave[m.group(1)] = (id_, float(dep or 0) or float(ret or 0))

    # Lado Nu: SPEI de la sección cuenta con clave
    cur.execute("""
        SELECT id, UPPER(clave_rastreo), ABS(monto)
        FROM transacciones_nu
        WHERE clave_rastreo IS NOT NULL AND seccion = 'cuenta'
    """)
    nuevos, encontrados = 0, 0
    for nu_id, clave, monto_nu in cur.fetchall():
        if clave not in por_clave:
            continue
        encontrados += 1
        sant_id, monto_sant = por_clave[clave]
        if abs(float(monto_nu) - monto_sant) > 0.01:
            print(f"  AVISO: montos difieren para clave {clave}: "
                  f"nu=${monto_nu} santander=${monto_sant}")
        cur.execute(SQL_INSERT_PAR, {
            "santander_id": sant_id,
            "nu_id":        nu_id,
            "clave_rastreo": clave,
            "monto":        monto_nu,
        })
        nuevos += cur.rowcount
    return nuevos, encontrados


def main() -> int:
    with conexion() as conn:
        with conn.cursor() as cur:
            nuevas, total = cargar_movimientos(cur)
            print(f"transacciones_nu: {nuevas} filas nuevas de {total} "
                  f"extraídas ({total - nuevas} ya existían)")

            pares_nuevos, pares = materializar_reconciliacion(cur)
            print(f"transferencias_reconciliadas: {pares_nuevos} pares nuevos "
                  f"de {pares} emparejados ({pares - pares_nuevos} ya existían)")

            cur.execute("SELECT COUNT(*) FROM transacciones_nu")
            print(f"\nTotal en transacciones_nu: {cur.fetchone()[0]}")
            cur.execute("SELECT COUNT(*) FROM transferencias_reconciliadas")
            print(f"Total en transferencias_reconciliadas: {cur.fetchone()[0]}")
            cur.execute("SELECT COUNT(*) FROM flujo_consolidado")
            print(f"Filas en vista flujo_consolidado: {cur.fetchone()[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
