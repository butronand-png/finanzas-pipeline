"""
src/extractor.py — DEPRECATED.

El módulo real vive en `src.extractors.santander`. Este archivo se mantiene
como shim de compatibilidad para no romper tests/scripts que importaban
funciones directamente de `src.extractor`.

Nuevas imports deben usar:
    from src.extractors import santander
    df = santander.extraer_pdf(pdf_path)
"""
from src.extractors.santander import (  # noqa: F401
    PATRON_FECHA,
    PATRON_INICIO,
    MESES,
    VARIANTES_MES,
    X_DEPOSITO,
    X_RETIRO,
    X_SALDO,
    TOLERANCIA_X,
    agrupar_por_fila,
    clasificar_filas,
    parsear_transacciones,
    extraer_pdf,
)
