"""
main.py — Pipeline unificado: Santander + Nu → DataFrame validado → CSV/Parquet.

Diseño:
- Santander: procesa desde `data/ocr_cache/*.json` (ya existentes) usando el
  extractor `src.extractors.santander`. Los PDFs a los que corresponden esos
  caches viven en `data/pdfs/`.
- Nu: procesa PDFs directamente desde `data/pdfs_nu/*.pdf` usando
  `src.extractors.nu` (sin OCR, `pdfplumber` directo).
- Ambos extractores producen DataFrames con el mismo esquema
  (`src.schema.TransaccionSchema`).
- Se concatenan, se aplica categorización, se valida con pandera, se guardan
  CSV y Parquet.

Uso:
    cd ~/finanzas-personales
    source .venv/bin/activate
    python -m src.main

Output:
    data/output/transacciones.csv
    data/output/transacciones.parquet
"""
from __future__ import annotations

import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.extractors import nu, santander
from src.ocr import cargar_cache
from src.extractor import (
    agrupar_por_fila,
    clasificar_filas,
    parsear_transacciones,
)
from src.validador import validar_saldos
from src.categorizador import categorizar
from src.schema import validar_df


CACHE_DIR       = Path("data/ocr_cache")
PDF_DIR_NU      = Path("data/pdfs_nu")
OUTPUT_DIR      = Path("data/output")


# =============================================================================
# EXTRACCIÓN POR BANCO
# =============================================================================
def procesar_santander_desde_cache() -> tuple[pd.DataFrame, dict]:
    """
    Procesa Santander leyendo caches OCR pre-existentes en `data/ocr_cache/`.

    Se prefiere leer del cache (en vez de disparar OCR desde PDF) porque:
    - Los caches ya existen y OCR es lento (~40s por PDF con Apple Vision).
    - La misma lógica sirve para reprocesar sin re-invocar `ocrmac`.
    - Si un cache existe, el PDF original ya se procesó antes; no hay razón
      de volver a correr Vision.
    """
    caches = sorted(CACHE_DIR.glob("*.json"))
    print(f"[Santander] Procesando {len(caches)} caches de OCR")

    todas = []
    resumen_global = {"total": 0, "validas": 0, "invalidas": 0, "no_validables": 0}

    for cache in caches:
        anots = cargar_cache(cache)
        filas = agrupar_por_fila(anots)
        clasificadas = clasificar_filas(filas)
        transacciones = parsear_transacciones(clasificadas)
        transacciones, resumen = validar_saldos(transacciones)

        stem = cache.stem
        for t in transacciones:
            t["pdf_origen"] = stem

        todas.extend(transacciones)
        for k in resumen_global:
            resumen_global[k] += resumen[k]

    # Convertir al schema unificado
    rows = []
    for t in todas:
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
            "moneda_original": None,
            "monto_original":  None,
            "tipo_cambio":     None,
            "pdf_origen":      t["pdf_origen"],
            "pagina":          int(t["pagina"]),
            "confidence_min":  float(t.get("confidence_min", 1.0)),
            "valido":          bool(t["valido"]) if t.get("valido") is not None else True,
            "error_saldo":     float(error_saldo) if error_saldo is not None else float("nan"),
        })
    df = pd.DataFrame(rows)
    return df, resumen_global


def procesar_nu_desde_pdfs() -> tuple[pd.DataFrame, list[dict]]:
    """
    Procesa Nu leyendo directamente los PDFs en `data/pdfs_nu/`.
    Devuelve el DataFrame concatenado y una lista con la validación mensual
    (invariante contable de Nu) por PDF, para reporte.
    """
    pdfs = sorted(PDF_DIR_NU.glob("*.pdf"))
    print(f"[Nu] Procesando {len(pdfs)} PDFs directos")

    dfs = []
    validaciones = []
    for pdf in pdfs:
        df = nu.extraer_pdf(pdf)
        dfs.append(df)
        validaciones.append(nu.validar_pdf(df, pdf))

    if not dfs:
        return pd.DataFrame(), []

    df_all = pd.concat(dfs, ignore_index=True)
    return df_all, validaciones


# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================
def procesar_todos() -> pd.DataFrame:
    """
    Pipeline completo:
      1. Extracción Santander (desde cache OCR)
      2. Extracción Nu (desde PDFs)
      3. Concatenación
      4. Categorización
      5. Validación de schema (pandera)
      6. Persistencia CSV + Parquet
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    df_s, resumen_s = procesar_santander_desde_cache()
    df_n, validaciones_n = procesar_nu_desde_pdfs()

    df = pd.concat([df_s, df_n], ignore_index=True)
    df = df.sort_values(["banco", "fecha", "folio"]).reset_index(drop=True)

    # Categorización (funciona para ambos bancos gracias a categorizador unificado)
    df[["comercio", "categoria"]] = df["descripcion_raw"].apply(
        lambda d: pd.Series(categorizar(d))
    )

    # Validación de schema — el pipeline se aborta si el DataFrame no cumple.
    # Nota: pandera valida columnas del schema base; `comercio`/`categoria`
    # son columnas extra y se ignoran por `strict="filter"`.
    df = validar_df(df, lazy=True)
    # `validar_df` con `strict="filter"` descarta columnas extra, así que
    # volvemos a agregar comercio/categoria si se fueron.
    if "comercio" not in df.columns:
        df[["comercio", "categoria"]] = df["descripcion_raw"].apply(
            lambda d: pd.Series(categorizar(d))
        )

    # --- Reporte Santander ---------------------------------------------------
    print("\n=== Resumen Santander ===")
    total_s = resumen_s["total"]
    if total_s > 0:
        pct = 100 * resumen_s["validas"] / total_s
        print(f"  Total tx:      {total_s}")
        print(f"  Válidas:       {resumen_s['validas']} ({pct:.1f}%)")
        print(f"  Inválidas:     {resumen_s['invalidas']}")
        print(f"  No validables: {resumen_s['no_validables']}")

    # --- Reporte Nu ----------------------------------------------------------
    print("\n=== Resumen Nu ===")
    print(f"  Total tx:      {len(df_n)}")
    ok_extract = sum(1 for v in validaciones_n if v["extraccion_ok"])
    ok_header  = sum(1 for v in validaciones_n if v["header_ok"])
    anomalias  = sum(1 for v in validaciones_n if v["nu_anomaly"])
    print(f"  Balance interno OK: {ok_extract}/{len(validaciones_n)}")
    print(f"  Header Nu cuadra:   {ok_header}/{len(validaciones_n)}")
    print(f"  Anomalías Nu:       {anomalias}")
    if anomalias > 0:
        for v in validaciones_n:
            if v["nu_anomaly"]:
                print(f"    [anomalía] {v['pdf']} diff_dep={v['diff_depositos']:+.2f} diff_gas={v['diff_gastos']:+.2f}")

    # --- Cobertura categorización -------------------------------------------
    sin_cat = (df["categoria"] == "sin_categoria").sum()
    pct_cat = 100 * (len(df) - sin_cat) / len(df) if len(df) else 0.0
    print("\n=== Resumen categorización ===")
    print(f"  Categorizadas: {len(df) - sin_cat} ({pct_cat:.1f}%)")
    print(f"  Sin categoría: {sin_cat}")
    if sin_cat > 0:
        print("\n  Muestra de las que quedaron sin categoría:")
        muestra = df[df["categoria"] == "sin_categoria"].head(10)
        for _, r in muestra.iterrows():
            print(f"    [{r['banco']}] {r['descripcion_raw'][:80]}")

    # --- Persistencia -------------------------------------------------------
    csv_path = OUTPUT_DIR / "transacciones.csv"
    parquet_path = OUTPUT_DIR / "transacciones.parquet"
    df.to_csv(csv_path, index=False)
    df.to_parquet(parquet_path, index=False)
    print(f"\nGuardado: {csv_path}")
    print(f"Guardado: {parquet_path}")

    return df


if __name__ == "__main__":
    df = procesar_todos()
    print(f"\nDataFrame final: {len(df)} filas, {len(df.columns)} columnas")
    print(f"Distribución por banco:\n{df['banco'].value_counts().to_string()}")
