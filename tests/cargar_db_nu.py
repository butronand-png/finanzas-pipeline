"""
tests/cargar_db_nu.py — Carga el DataFrame combinado (Santander + Nu) a Postgres.

Lee `data/output/transacciones.parquet` (producido por `python -m src.main`)
y lo inserta en la tabla `transacciones` usando `db.cargar_dataframe`.

Idempotente: el constraint UNIQUE(banco, fecha, folio, deposito, retiro,
saldo) + ON CONFLICT DO NOTHING garantiza que correr esto dos veces no
duplica filas.

Uso:
    python -m src.main            # produce transacciones.parquet
    python tests/cargar_db_nu.py  # carga a Postgres
"""
from __future__ import annotations

import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.db import cargar_dataframe


PARQUET_PATH = Path("data/output/transacciones.parquet")


def main() -> int:
    if not PARQUET_PATH.exists():
        print(f"ERROR: no existe {PARQUET_PATH}. Corre primero: python -m src.main")
        return 1

    df = pd.read_parquet(PARQUET_PATH)
    print(f"Leído: {len(df)} filas desde {PARQUET_PATH}")
    print(f"Distribución por banco:\n{df['banco'].value_counts().to_string()}")
    print()

    stats = cargar_dataframe(df)

    print("Resultado de la carga:")
    for k, v in stats.items():
        print(f"  {k:30s}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
