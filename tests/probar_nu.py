"""
tests/probar_nu.py — Validador batch de todos los PDFs de Nu.

Corre el extractor sobre cada PDF en data/pdfs_nu/, aplica el invariante
contable de Nu (header vs suma extraída ajustada por cajitas), valida
schema con pandera, e imprime un reporte tabular. Si algún mes falla,
lo marca en rojo para auditoría manual.

Uso:
    cd ~/finanzas-personales
    source .venv/bin/activate
    python -m tests.probar_nu

Salida esperada: tabla ordenada por fecha con columnas
    pdf | n_tx | dep_diff | gas_diff | ok
Y al final: cobertura global (X/29 pasaron).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.extractors import nu
from src.schema import validar_df


COLOR_OK   = "\033[92m"
COLOR_BAD  = "\033[91m"
COLOR_DIM  = "\033[90m"
COLOR_END  = "\033[0m"


def _fmt_diff(x: float) -> str:
    if abs(x) < 0.01:
        return f"{COLOR_DIM}   0.00{COLOR_END}"
    color = COLOR_BAD if abs(x) > 0.5 else ""
    return f"{color}{x:+8.2f}{COLOR_END}"


def main() -> int:
    dir_pdfs = Path("data/pdfs_nu")
    if not dir_pdfs.exists():
        print(f"ERROR: no existe {dir_pdfs.resolve()}", file=sys.stderr)
        return 1

    pdfs = sorted(dir_pdfs.glob("*.pdf"))
    if not pdfs:
        print(f"ERROR: no hay PDFs en {dir_pdfs}", file=sys.stderr)
        return 1

    print(f"\nProcesando {len(pdfs)} PDFs de Nu desde {dir_pdfs}\n")
    print(f"{'PDF':<30s} {'n_tx':>5} {'dep_diff':>12} {'gas_diff':>12} {'intereses':>10}  {'extract':>7}  {'header':>6}  {'schema':>6}")
    print("-" * 110)

    resultados: list[dict] = []
    pandera_ok = 0

    for path in pdfs:
        try:
            df = nu.extraer_pdf(path)
        except Exception as e:
            print(f"{path.name:<30s} {COLOR_BAD}EXTRACT ERROR: {e}{COLOR_END}")
            continue

        # Validación pandera (schema)
        schema_ok = True
        try:
            validar_df(df, lazy=True)
        except Exception as e:
            schema_ok = False
            print(f"  {COLOR_BAD}[schema fail] {path.name}: {str(e)[:200]}{COLOR_END}")
        else:
            pandera_ok += 1

        # Validación contable (dos niveles)
        val = nu.validar_pdf(df, path)

        m_extract = f"{COLOR_OK}✓{COLOR_END}" if val["extraccion_ok"] else f"{COLOR_BAD}✗{COLOR_END}"
        if val["header_ok"]:
            m_header = f"{COLOR_OK}✓{COLOR_END}"
        elif val["nu_anomaly"]:
            m_header = f"\033[93m!{COLOR_END}"   # amarillo = anomalía de Nu, no bug
        else:
            m_header = f"{COLOR_BAD}✗{COLOR_END}"
        m_schema = f"{COLOR_OK}✓{COLOR_END}" if schema_ok else f"{COLOR_BAD}✗{COLOR_END}"
        print(f"{path.name:<30s} {val['n_tx']:>5d} "
              f"{_fmt_diff(val['diff_depositos']):>21} "
              f"{_fmt_diff(val['diff_gastos']):>21} "
              f"{(val['intereses'] or 0):>10.2f}  "
              f"    {m_extract}      {m_header}       {m_schema}")

        resultados.append({
            "pdf":            path.name,
            "periodo":        val["periodo"],
            "n_tx":           val["n_tx"],
            "diff_dep":       val["diff_depositos"],
            "diff_gas":       val["diff_gastos"],
            "intereses":      val["intereses"],
            "header_dep":     val["header_depositos"],
            "header_gas":     val["header_gastos"],
            "extraccion_ok":  val["extraccion_ok"],
            "header_ok":      val["header_ok"],
            "nu_anomaly":     val["nu_anomaly"],
            "schema_ok":      schema_ok,
        })

    # ---- resumen -----------------------------------------------------------
    df_res = pd.DataFrame(resultados)
    extract_ok = int(df_res["extraccion_ok"].sum())
    header_ok  = int(df_res["header_ok"].sum())
    anomalias  = int(df_res["nu_anomaly"].sum())
    total = len(df_res)
    print("-" * 110)
    print(f"\nEXTRACCIÓN: {extract_ok}/{total} PDFs con balance interno preservado")
    print(f"HEADER:     {header_ok}/{total} PDFs también coinciden con header de Nu")
    print(f"ANOMALÍAS:  {anomalias}/{total} PDFs con inconsistencia en header de Nu (no es bug del extractor)")
    print(f"SCHEMA:     {pandera_ok}/{total} PDFs cumplen pandera")

    if extract_ok < total:
        print(f"\n{COLOR_BAD}PDFs con balance interno roto (BUG del extractor — auditar):{COLOR_END}")
        malos = df_res[~df_res["extraccion_ok"]]
        print(malos[["pdf", "n_tx", "diff_dep", "diff_gas"]].to_string(index=False))
    elif anomalias > 0:
        print(f"\n\033[93mPDFs con anomalía de header de Nu (extracción OK, cargar a BD igual):{COLOR_END}")
        print(df_res[df_res["nu_anomaly"]][["pdf", "diff_dep", "diff_gas"]].to_string(index=False))
    else:
        print(f"\n{COLOR_OK}TODOS los PDFs pasan validación completa (extracción + header + schema).{COLOR_END}")

    # Estadísticas útiles
    print(f"\nTotal transacciones detectadas: {df_res['n_tx'].sum()}")
    print(f"Total intereses generados 2 años: ${df_res['intereses'].sum():,.2f}")
    print(f"Total ingresos externos (SPEI + refunds): ${df_res['header_dep'].sum():,.2f}")
    print(f"Total gastos externos (compras + comisiones): ${df_res['header_gas'].sum():,.2f}")

    # Guardar CSV para postmortem
    csv_out = Path("data/output/nu_validacion.csv")
    csv_out.parent.mkdir(parents=True, exist_ok=True)
    df_res.to_csv(csv_out, index=False)
    print(f"\nReporte completo en: {csv_out}")

    # Exit 0 sólo si extracción y schema pasan (anomalías de Nu son OK)
    return 0 if (extract_ok == total and pandera_ok == total) else 2


if __name__ == "__main__":
    raise SystemExit(main())
