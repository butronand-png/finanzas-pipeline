"""
db.py — Persistencia y queries de análisis contra Postgres.

Cambios vs versión previa (post-migración 003_nu):
- El INSERT ahora incluye `banco`, `moneda_original`, `monto_original`,
  `tipo_cambio`.
- Se cambia el nombre de columna en el DataFrame de entrada de `descripcion`
  → `descripcion_raw` (consistente con el schema unificado y con la BD).
- Se usa `ON CONFLICT DO NOTHING` en el INSERT en vez de rollback manual
  después de UniqueViolation. La versión previa hacía `conn.rollback()`
  dentro del loop, lo cual revertía TODAS las inserciones anteriores en la
  misma transacción — bug preexistente. Con `ON CONFLICT` cada duplicado
  se ignora sin afectar filas previas.
- Se elimina `cur.execute("BEGIN")` explícito que no era necesario y podía
  romper la semántica del context manager de psycopg 3.
- Las queries de análisis (`flujo_por_mes`, `gastos_por_categoria`) usan la
  vista regenerada `transacciones_completas` que ahora expone `banco`,
  `moneda_original`, etc.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import pandas as pd
import psycopg


# ============================================================
# CONFIGURACIÓN
# ============================================================
def _cargar_dotenv() -> None:
    """
    Carga .env de la raíz del repo a os.environ (sin pisar variables ya
    exportadas). Parser mínimo KEY=VALUE para no agregar dependencias.
    """
    ruta = Path(__file__).resolve().parents[1] / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text().splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip())


_cargar_dotenv()

# Credenciales SIN default en código: user y password vienen de .env o del
# entorno (ver .env.example). Solo host/puerto/db tienen default local.
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5434)),
    "dbname":   os.getenv("DB_NAME", "finanzas"),
    "user":     os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
}


@contextmanager
def conexion():
    """
    Context manager para conexión a Postgres.

    Uso:
        with conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")

    Commit automático si la ejecución sale limpia; rollback si excepción.
    """
    if not DB_CONFIG["user"] or not DB_CONFIG["password"]:
        raise RuntimeError(
            "Faltan DB_USER/DB_PASSWORD: copia .env.example a .env y define "
            "las credenciales (o exporta las variables de entorno).")
    conn = psycopg.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# CARGA DE DATAFRAME → TABLA transacciones
# ============================================================
def cargar_dataframe(df: pd.DataFrame) -> dict:
    """
    Carga un DataFrame de transacciones a Postgres, creando comercios que
    no existan y respetando el constraint UNIQUE(banco, fecha, folio,
    deposito, retiro, saldo) via ON CONFLICT DO NOTHING.

    El DataFrame debe cumplir `src.schema.TransaccionSchema` más las
    columnas de categorización `comercio` y `categoria`.

    Returns:
        dict con conteos: categorias_existentes, comercios_creados,
        comercios_existentes, transacciones_insertadas,
        transacciones_duplicadas.
    """
    stats = {
        "categorias_existentes":     0,
        "comercios_creados":         0,
        "comercios_existentes":      0,
        "transacciones_insertadas":  0,
        "transacciones_duplicadas":  0,
    }

    with conexion() as conn:
        with conn.cursor() as cur:
            # ---- 1. Mapear categorias existentes ---------------------------
            cur.execute("SELECT nombre, id FROM categorias")
            categorias_map = {n: i for n, i in cur.fetchall()}
            stats["categorias_existentes"] = len(categorias_map)

            # ---- 2. Mapear comercios existentes ----------------------------
            cur.execute("SELECT nombre, id FROM comercios")
            comercios_map = {n: i for n, i in cur.fetchall()}
            stats["comercios_existentes"] = len(comercios_map)

            # ---- 3. Insertar comercios nuevos ------------------------------
            comercios_unicos = df[["comercio", "categoria"]].drop_duplicates()
            for _, row in comercios_unicos.iterrows():
                nombre_com = row["comercio"]
                nombre_cat = row["categoria"]

                if nombre_com in comercios_map:
                    continue
                if nombre_cat not in categorias_map:
                    print(f"  WARN: categoría '{nombre_cat}' no existe, skip {nombre_com}")
                    continue

                cur.execute(
                    "INSERT INTO comercios (nombre, categoria_id) "
                    "VALUES (%s, %s) RETURNING id",
                    (nombre_com, categorias_map[nombre_cat]),
                )
                comercios_map[nombre_com] = cur.fetchone()[0]
                stats["comercios_creados"] += 1

            # ---- 4. Insertar transacciones (con ON CONFLICT) --------------
            insert_sql = """
                INSERT INTO transacciones (
                    banco, fecha, folio, descripcion_raw,
                    deposito, retiro, saldo,
                    moneda_original, monto_original, tipo_cambio,
                    comercio_id, pdf_origen, pagina,
                    confidence_min, valido, error_saldo
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                ON CONFLICT (banco, fecha, folio, deposito, retiro, saldo)
                DO NOTHING
                RETURNING id
            """

            for _, row in df.iterrows():
                comercio_id = comercios_map.get(row["comercio"])

                # Convertir NaN / NaT / None a None (SQL NULL)
                def _nz(v):
                    if v is None:
                        return None
                    if isinstance(v, float) and pd.isna(v):
                        return None
                    return v

                fecha_val = row["fecha"]
                if hasattr(fecha_val, "date"):
                    fecha_val = fecha_val.date()

                cur.execute(insert_sql, (
                    row["banco"],
                    fecha_val,
                    row["folio"],
                    row["descripcion_raw"],
                    float(row["deposito"]),
                    float(row["retiro"]),
                    _nz(row["saldo"]),
                    _nz(row["moneda_original"]),
                    _nz(row["monto_original"]),
                    _nz(row["tipo_cambio"]),
                    comercio_id,
                    row["pdf_origen"],
                    int(row["pagina"]) if not pd.isna(row["pagina"]) else None,
                    float(row["confidence_min"]) if not pd.isna(row["confidence_min"]) else None,
                    bool(row["valido"]) if not pd.isna(row["valido"]) else None,
                    _nz(row["error_saldo"]),
                ))
                if cur.fetchone() is not None:
                    stats["transacciones_insertadas"] += 1
                else:
                    stats["transacciones_duplicadas"] += 1

    return stats


# ============================================================
# QUERIES DE ANÁLISIS
# ============================================================
def flujo_por_mes(banco: Optional[str] = None) -> pd.DataFrame:
    """
    Flujo de caja por mes, excluyendo transferencias entre cuentas propias
    (categorías con tipo_categoria='transferencia' — incluye transferencia_nu,
    transferencia_cajita, transferencia_otros).

    Args:
        banco: filtrar por 'santander' o 'nu'; None = ambos.
    """
    where_banco = ""
    if banco:
        where_banco = f"AND banco = '{banco}'"

    query = f"""
        SELECT
            DATE_TRUNC('month', fecha)::DATE AS mes,
            SUM(deposito) AS ingresos,
            SUM(retiro)   AS gastos,
            SUM(deposito) - SUM(retiro) AS neto
        FROM transacciones_completas
        WHERE tipo_categoria != 'transferencia'
          {where_banco}
        GROUP BY mes
        ORDER BY mes
    """
    with conexion() as conn:
        return pd.read_sql(query, conn)


def gastos_por_categoria(meses: Optional[int] = None,
                         banco: Optional[str] = None) -> pd.DataFrame:
    """
    Gastos totales por categoría.

    Args:
        meses: si se pasa (int), filtra los últimos N meses.
        banco: filtrar por 'santander' o 'nu'; None = ambos.
    """
    where_meses = ""
    if meses:
        where_meses = f"AND fecha >= CURRENT_DATE - INTERVAL '{meses} months'"

    where_banco = ""
    if banco:
        where_banco = f"AND banco = '{banco}'"

    query = f"""
        SELECT
            categoria,
            COUNT(*) AS n_transacciones,
            SUM(retiro) AS gasto_total,
            ROUND(AVG(retiro), 2) AS gasto_promedio
        FROM transacciones_completas
        WHERE tipo_categoria = 'gasto' AND retiro > 0
          {where_meses}
          {where_banco}
        GROUP BY categoria
        ORDER BY gasto_total DESC
    """
    with conexion() as conn:
        return pd.read_sql(query, conn)


def compras_usd() -> pd.DataFrame:
    """
    Todas las compras en USD (Anthropic, Claude.ai, OpenAI, etc.) con el
    tipo de cambio efectivo pagado en cada una. Útil para ver el costo real
    de las subs de IA y otros servicios internacionales.
    """
    query = """
        SELECT
            banco,
            fecha,
            descripcion_raw,
            monto_original,
            tipo_cambio,
            retiro AS costo_mxn,
            comercio,
            categoria
        FROM transacciones_completas
        WHERE moneda_original IS NOT NULL
        ORDER BY fecha DESC
    """
    with conexion() as conn:
        return pd.read_sql(query, conn)


def transferencias_cruzadas() -> pd.DataFrame:
    """
    Diagnóstico: transferencias entre Santander y Nu (agrupadas por mes y
    dirección). Sirve para verificar que los movimientos entre cuentas
    propias cuadran en ambos lados.
    """
    query = """
        WITH tx_transfer_nu AS (
            SELECT
                DATE_TRUNC('month', fecha)::DATE AS mes,
                banco,
                CASE WHEN deposito > 0 THEN 'entrada' ELSE 'salida' END AS direccion,
                SUM(deposito + retiro) AS monto
            FROM transacciones_completas
            WHERE categoria = 'transferencia_nu'
            GROUP BY 1, 2, 3
        )
        SELECT * FROM tx_transfer_nu
        ORDER BY mes, banco, direccion
    """
    with conexion() as conn:
        return pd.read_sql(query, conn)
