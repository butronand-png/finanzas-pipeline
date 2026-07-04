"""
extractors/santander.py — Extractor de estados de cuenta de Santander.

Traslado del antiguo `src/extractor.py` a la subcarpeta `extractors/` con
la misma lógica interna (agrupar_por_fila, clasificar_filas, ...), más una
función `extraer_pdf(path)` que unifica el contrato con Nu.

Diferencias vs el módulo original:
- Se agrega `extraer_pdf(pdf_path) -> DataFrame` conforme a
  `src.schema.TransaccionSchema`, replicando el contrato que también cumple
  `src.extractors.nu.extraer_pdf`.
- Se emite la columna `descripcion_raw` (no `descripcion`) para consistencia
  con Nu y con la tabla `transacciones` en BD.
- Se agregan las nuevas columnas del schema: `banco`, `moneda_original`,
  `monto_original`, `tipo_cambio` (los últimos tres siempre NULL en
  Santander — no vimos compras en moneda extranjera).

Los helpers internos (agrupar_por_fila, clasificar_filas, parsear_transacciones,
_parsear_inicio, _asignar_monto, _agregar_continuacion) preservan la firma
original para no romper tests que los importen. `src/extractor.py` queda
como shim que re-exporta desde acá.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.ocr import cachear_pdf, cargar_cache
from src.validador import validar_saldos


# =============================================================================
# CONSTANTES DE PARSING (idénticas al módulo original)
# =============================================================================
PATRON_FECHA = re.compile(r"^\d{2}-[A-Z0-9]{3}-\d{4}")
PATRON_INICIO = re.compile(r"^(\d{2})-([A-Z0-9]{3})-(\d{4})\s+(\d+)\s+(.+)$")

MESES = {
    "ENE": "01", "FEB": "02", "MAR": "03", "ABR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AGO": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DIC": "12",
}

# Variantes comunes que produce Apple Vision (E↔F, O↔0)
VARIANTES_MES = {
    "SFP": "SEP", "FEP": "FEB", "ABP": "ABR", "FNE": "ENE", "FBR": "FEB",
    "0CT": "OCT", "0NV": "NOV", "N0V": "NOV", "0EP": "SEP",
    "AG0": "AGO", "MAY0": "MAY",
}

# Coordenadas x aproximadas de las columnas del PDF Santander (normalizadas 0-1)
X_DEPOSITO = 0.609
X_RETIRO = 0.727
X_SALDO = 0.852
TOLERANCIA_X = 0.08


# =============================================================================
# HELPERS DE PARSING (lógica original preservada)
# =============================================================================
def _limpiar_monto(texto):
    """Convierte '16,145.54' o '$16,235.54' a float."""
    limpio = texto.replace("$", "").replace(",", "").strip()
    return float(limpio)


def _fecha_a_iso(dia, mes_abrev, anio):
    """Convierte ('17', 'MAR', '2026') a '2026-03-17'.

    Normaliza variantes comunes de OCR (SFP→SEP, etc). None si no reconoce.
    """
    mes_norm = VARIANTES_MES.get(mes_abrev, mes_abrev)
    if mes_norm not in MESES:
        return None
    return f"{anio}-{MESES[mes_norm]}-{dia}"


def agrupar_por_fila(anotaciones, umbral_y=0.005):
    """Agrupa anotaciones que están en la misma fila visual del PDF."""
    if not anotaciones:
        return []

    ordenadas = sorted(anotaciones, key=lambda a: (a["pagina"], -a["y"]))
    filas = []
    fila_actual = [ordenadas[0]]
    y_referencia = ordenadas[0]["y"]
    pagina_referencia = ordenadas[0]["pagina"]

    for anot in ordenadas[1:]:
        misma_pagina = anot["pagina"] == pagina_referencia
        misma_y = abs(anot["y"] - y_referencia) < umbral_y
        if misma_pagina and misma_y:
            fila_actual.append(anot)
        else:
            fila_actual.sort(key=lambda a: a["x"])
            filas.append(fila_actual)
            fila_actual = [anot]
            y_referencia = anot["y"]
            pagina_referencia = anot["pagina"]

    fila_actual.sort(key=lambda a: a["x"])
    filas.append(fila_actual)
    return filas


def clasificar_filas(filas):
    """Clasifica cada fila como inicio de transacción o continuación."""
    resultado = []
    for fila in filas:
        if not fila:
            continue
        primer_texto = fila[0]["texto"]
        es_inicio = bool(PATRON_FECHA.match(primer_texto))
        resultado.append({"es_inicio": es_inicio, "anotaciones": fila})
    return resultado


def parsear_transacciones(filas_clasificadas):
    """Convierte filas clasificadas en transacciones estructuradas."""
    transacciones = []
    actual = None
    for entrada in filas_clasificadas:
        anots = entrada["anotaciones"]
        if entrada["es_inicio"]:
            if actual is not None:
                transacciones.append(actual)
            nueva = _parsear_inicio(anots)
            actual = nueva if nueva is not None else None
        else:
            if actual is not None:
                _agregar_continuacion(actual, anots)
    if actual is not None:
        transacciones.append(actual)
    return transacciones


def _parsear_inicio(anotaciones):
    """Parsea la primera fila de una transacción (con fecha + folio)."""
    primera = anotaciones[0]
    match = PATRON_INICIO.match(primera["texto"])
    if not match:
        return None
    dia, mes, anio, folio, descripcion = match.groups()
    fecha = _fecha_a_iso(dia, mes, anio)
    if fecha is None:
        return None
    transaccion = {
        "fecha": fecha,
        "folio": folio,
        "descripcion": descripcion,
        "deposito": 0.0,
        "retiro": 0.0,
        "saldo": None,
        "pagina": primera["pagina"],
        "confidence_min": primera["confidence"],
    }
    for anot in anotaciones[1:]:
        _asignar_monto(transaccion, anot)
    return transaccion


def _asignar_monto(transaccion, anotacion):
    """Asigna un monto a deposito/retiro/saldo según la x de la anotación."""
    x = anotacion["x"]
    try:
        monto = _limpiar_monto(anotacion["texto"])
    except ValueError:
        return
    if abs(x - X_DEPOSITO) < TOLERANCIA_X:
        transaccion["deposito"] = monto
    elif abs(x - X_RETIRO) < TOLERANCIA_X:
        transaccion["retiro"] = monto
    elif abs(x - X_SALDO) < TOLERANCIA_X:
        transaccion["saldo"] = monto
    transaccion["confidence_min"] = min(
        transaccion["confidence_min"], anotacion["confidence"]
    )


def _agregar_continuacion(transaccion, anotaciones):
    """Agrega texto de una continuación a la descripción (separado por ' | ')."""
    for anot in anotaciones:
        transaccion["descripcion"] += " | " + anot["texto"]
        transaccion["confidence_min"] = min(
            transaccion["confidence_min"], anot["confidence"]
        )


# =============================================================================
# API PÚBLICA UNIFICADA CON NU
# =============================================================================
def extraer_pdf(pdf_path: str | Path) -> pd.DataFrame:
    """
    Pipeline completo: PDF Santander → cache OCR → parseo → validación
    de saldos → DataFrame conforme a `src.schema.TransaccionSchema`.

    Si ya existe la cache OCR en `data/ocr_cache/{stem}.json`, la reusa.
    Si no, corre `cachear_pdf` para generarla (Apple Vision OCR).
    """
    pdf_path = Path(pdf_path)
    cache_file = cachear_pdf(pdf_path)
    anots = cargar_cache(cache_file)

    filas = agrupar_por_fila(anots)
    clasificadas = clasificar_filas(filas)
    transacciones = parsear_transacciones(clasificadas)
    transacciones, _resumen = validar_saldos(transacciones)

    # Convertir al schema unificado (mismo contrato que src.extractors.nu)
    rows = []
    for t in transacciones:
        saldo = t.get("saldo")
        error_saldo = t.get("error_saldo")
        rows.append({
            "banco":           "santander",
            "fecha":           pd.to_datetime(t["fecha"]),
            "folio":           str(t["folio"]),
            "descripcion_raw": t["descripcion"],
            "deposito":        float(t["deposito"]),
            "retiro":          float(t["retiro"]),
            "saldo":           float(saldo) if saldo is not None else float("nan"),
            "moneda_original": None,   # Santander MXN siempre
            "monto_original":  None,
            "tipo_cambio":     None,
            "pdf_origen":      pdf_path.stem,
            "pagina":          int(t["pagina"]),
            "confidence_min":  float(t.get("confidence_min", 1.0)),
            # `valido` puede ser None (primera tx de PDF, sin baseline);
            # el schema no permite NULL, así que lo mapeo a True (extraído sin
            # error, aunque no se pudo validar contra un saldo anterior).
            "valido":          bool(t["valido"]) if t.get("valido") is not None else True,
            "error_saldo":     float(error_saldo) if error_saldo is not None else float("nan"),
        })
    return pd.DataFrame(rows)


# =============================================================================
# CLI standalone (para debug de un solo PDF)
# =============================================================================
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python -m src.extractors.santander <ruta.pdf>")
        sys.exit(1)
    df = extraer_pdf(sys.argv[1])
    print(df.to_string(max_colwidth=60))
