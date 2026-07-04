"""
tests/probar_extractor_nu.py — Prueba del extractor de Nu (src/extractor_nu.py).

Corre el extractor sobre todos los PDFs de data/pdfs_nu/, imprime el resumen
de validación por PDF (identidad contable de página 1 + sumas de movimientos
vs resumen) y las primeras/últimas 5 transacciones de cada uno para
inspección visual. Al final, reporte agregado.

Uso:
    uv run python tests/probar_extractor_nu.py            # reporte compacto
    uv run python tests/probar_extractor_nu.py -v         # + primeras/últimas 5 tx por PDF
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.extractor_nu import extraer_pdf_nu

VERDE = "\033[92m"
ROJO = "\033[91m"
AMARILLO = "\033[93m"
FIN = "\033[0m"

COLS_INSPECCION = ["fecha", "descripcion", "monto", "seccion",
                   "es_movimiento_interno", "clave_rastreo"]


def main() -> int:
    verbose = "-v" in sys.argv
    dir_pdfs = Path(__file__).resolve().parents[1] / "data" / "pdfs_nu"
    pdfs = sorted(dir_pdfs.glob("*.pdf"))
    if not pdfs:
        print(f"ERROR: no hay PDFs en {dir_pdfs}", file=sys.stderr)
        return 1

    print(f"\nProcesando {len(pdfs)} PDFs de Nu desde {dir_pdfs}\n")
    print(f"{'PDF':<22s} {'n_mov':>5} {'cuenta':>6} {'cajitas':>7} {'spei':>4} "
          f"{'ident_diff':>10} {'dep_diff':>9} {'gas_diff':>9}  valido")
    print("-" * 95)

    resultados = []
    frames = []
    for path in pdfs:
        r = extraer_pdf_nu(path)
        res, df = r["resumen"], r["movimientos"]
        frames.append(df)

        n_cuenta = int((df["seccion"] == "cuenta").sum())
        n_cajitas = int((df["seccion"] == "cajitas").sum())
        n_spei = int(df["clave_rastreo"].notna().sum())
        if r["valido"] and res["sumas_espejo"]:
            marca = f"{AMARILLO}!{FIN}"  # anomalía del header de Nu, no bug
        elif r["valido"]:
            marca = f"{VERDE}✓{FIN}"
        else:
            marca = f"{ROJO}✗{FIN}"
        print(f"{path.name:<22s} {len(df):>5d} {n_cuenta:>6d} {n_cajitas:>7d} "
              f"{n_spei:>4d} {res['identidad_diff']:>10.2f} "
              f"{res['diff_depositos']:>9.2f} {res['diff_gastos']:>9.2f}    {marca}")
        for e in r["errores"]:
            print(f"    {ROJO}{e}{FIN}")

        if verbose and len(df) > 0:
            print(f"\n  Primeras 5 de {path.name}:")
            print(df.head(5)[COLS_INSPECCION].to_string(max_colwidth=50))
            print(f"  Últimas 5 de {path.name}:")
            print(df.tail(5)[COLS_INSPECCION].to_string(max_colwidth=50))
            print()

        resultados.append({
            "pdf": path.name,
            "periodo": f"{res['periodo_anio']}-{res['periodo_mes']:02d}",
            "n_mov": len(df),
            "identidad_ok": res["identidad_ok"],
            "identidad_diff": res["identidad_diff"],
            "sumas_ok": res["sumas_ok"],
            "sumas_espejo": res["sumas_espejo"],
            "diff_depositos": res["diff_depositos"],
            "diff_gastos": res["diff_gastos"],
            "valido": r["valido"],
        })

    # ---- reporte agregado ---------------------------------------------------
    df_res = pd.DataFrame(resultados)
    df_all = pd.concat(frames, ignore_index=True)
    total = len(df_res)
    n_identidad = int(df_res["identidad_ok"].sum())
    n_sumas = int(df_res["sumas_ok"].sum())
    n_validos = int(df_res["valido"].sum())

    n_espejo = int(df_res["sumas_espejo"].sum())

    print("-" * 95)
    print(f"\nPDFs procesados:        {total}")
    print(f"Movimientos extraídos:  {len(df_all)} "
          f"(cuenta: {(df_all['seccion'] == 'cuenta').sum()}, "
          f"cajitas: {(df_all['seccion'] == 'cajitas').sum()})")
    print(f"Identidad de página 1:  {n_identidad}/{total} ({n_identidad / total:.1%})")
    print(f"Sumas vs resumen:       {n_sumas}/{total} ({n_sumas / total:.1%})"
          + (f" + {n_espejo} con anomalía espejo del header de Nu" if n_espejo else ""))
    print(f"Validación completa:    {n_validos}/{total} ({n_validos / total:.1%})")
    print(f"Movimientos internos:   {df_all['es_movimiento_interno'].sum()} "
          f"(cajitas, no cuentan como ingreso/gasto real)")
    print(f"Con clave de rastreo:   {df_all['clave_rastreo'].notna().sum()} SPEI")
    print(f"Con detalle USD:        {df_all['monto_usd'].notna().sum()}")

    if n_espejo:
        print(f"\n{AMARILLO}PDFs con anomalía espejo (header de Nu inconsistente "
              f"con su detalle; extracción OK):{FIN}")
        print(df_res[df_res["sumas_espejo"]][
            ["pdf", "diff_depositos", "diff_gastos"]].to_string(index=False))
    if n_validos < total:
        print(f"\n{ROJO}PDFs que NO validan:{FIN}")
        malos = df_res[~df_res["valido"]]
        print(malos[["pdf", "identidad_diff", "diff_depositos",
                     "diff_gastos"]].to_string(index=False))
    else:
        print(f"\n{VERDE}TODOS los PDFs validan (identidad + sumas).{FIN}")

    return 0 if n_validos == total else 2


if __name__ == "__main__":
    raise SystemExit(main())
