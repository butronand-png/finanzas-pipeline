"""
schema.py — Contrato de DataFrame de transacciones (pandera).

Cada extractor (Santander, Nu) debe producir un DataFrame que cumpla
`TransaccionSchema.validate(df)`. Si no cumple, revienta antes de tocar BD.

Este es el "contrato de interfaz" entre los extractores y el resto del
pipeline. Cualquier cambio aquí obliga a actualizar TODOS los extractores.

Concepto: pandera hace validación declarativa de DataFrames. En vez de
escribir asserts sueltos por todo el código, se define el schema una vez
y se llama `.validate(df)` al final del extractor. Un DataFrame que no
cumpla revienta con un mensaje detallado (qué columna, qué fila, qué regla).

Alternativa considerada: pydantic + iteración por fila. Descartada porque
es 100x más lento para DataFrames de miles de filas y no vale la pena para
validaciones tan simples.

Doc: https://pandera.readthedocs.io/en/stable/dataframe_models.html
"""
from __future__ import annotations

import pandera.pandas as pa
from pandera.typing import Series
import pandas as pd


BANCOS_VALIDOS = ["santander", "nu"]
MONEDAS_VALIDAS = ["USD"]  # ampliar si aparecen EUR, GBP, etc.


class TransaccionSchema(pa.DataFrameModel):
    """
    Schema de salida común para ambos extractores (Santander, Nu).

    Reglas invariantes:
    - `deposito` y `retiro` nunca ambos > 0 al mismo tiempo (una tx es lo uno
      o lo otro).
    - `saldo` puede ser NULL (Nu no lo imprime por línea).
    - `moneda_original`, `monto_original`, `tipo_cambio`: o los 3 NULL o los 3
      llenos (consistencia).
    - `fecha` debe ser tipo datetime (no string).
    """

    # --- identidad ---------------------------------------------------------
    banco:           Series[str]           = pa.Field(isin=BANCOS_VALIDOS)
    fecha:           Series[pa.DateTime]   = pa.Field()
    folio:           Series[str]           = pa.Field(nullable=True)
    descripcion_raw: Series[str]           = pa.Field()

    # --- montos (siempre en MXN) -------------------------------------------
    deposito:        Series[float]         = pa.Field(ge=0, nullable=False)
    retiro:          Series[float]         = pa.Field(ge=0, nullable=False)
    saldo:           Series[float]         = pa.Field(nullable=True)

    # --- moneda extranjera (nullable en trio) ------------------------------
    moneda_original: Series[str]           = pa.Field(nullable=True, isin=MONEDAS_VALIDAS)
    monto_original:  Series[float]         = pa.Field(nullable=True, ge=0)
    tipo_cambio:     Series[float]         = pa.Field(nullable=True, gt=0)

    # --- metadata OCR/parsing ----------------------------------------------
    pdf_origen:      Series[str]           = pa.Field()
    # pagina: 0 = tx sintética (ej. intereses); 1+ = página real del PDF
    pagina:          Series[int]           = pa.Field(ge=0)
    confidence_min:  Series[float]         = pa.Field(ge=0, le=1)
    valido:          Series[bool]          = pa.Field()
    error_saldo:     Series[float]         = pa.Field(nullable=True)

    class Config:
        strict = "filter"     # columnas extra se descartan, no revientan
        coerce = True         # convierte tipos automáticamente si es seguro

    # ---- reglas cross-column ----------------------------------------------
    @pa.dataframe_check
    def deposito_xor_retiro(cls, df: pd.DataFrame) -> pd.Series:
        """Cada fila tiene depósito XOR retiro (o ambos 0 en casos raros)."""
        return ~((df["deposito"] > 0) & (df["retiro"] > 0))

    @pa.dataframe_check
    def moneda_trio_consistente(cls, df: pd.DataFrame) -> pd.Series:
        """Los 3 campos de moneda extranjera están todos NULL o todos llenos."""
        m = df["moneda_original"].notna()
        n = df["monto_original"].notna()
        t = df["tipo_cambio"].notna()
        return (m == n) & (n == t)


def validar_df(df: pd.DataFrame, lazy: bool = True) -> pd.DataFrame:
    """
    Aplica el schema. `lazy=True` colecta TODOS los errores antes de reventar
    (útil para debug). `lazy=False` para en el primer error.

    Devuelve el DataFrame ya coercionado (útil porque `coerce=True` puede
    convertir strings de fecha a datetime).
    """
    return TransaccionSchema.validate(df, lazy=lazy)
