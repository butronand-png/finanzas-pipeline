"""
tests/diagnostico_reconciliacion_nu.py — Diagnóstico de transferencias SPEI
de Nu que no reconcilian contra Santander por clave de rastreo.

Reconciliación: cada transferencia SPEI de Nu (extraída con
src/extractor_nu.py, con `clave_rastreo`) se busca por match exacto de
clave contra las claves `CLAVE DE RASTREO ...` de las descripciones OCR de
Santander (data/output/transacciones.parquet, banco='santander').

Las no reconciliadas se particionan en dos grupos:

(a) FUERA de la cobertura de Santander — fecha anterior al primer estado
    de cuenta Santander disponible (la cobertura real se calcula de los
    datos: arranca el 2024-06-19, no el 1-jul-2024). Sin estado de cuenta
    contraparte, el no-match es esperado.

(b) DENTRO de la cobertura y aun así sin match. Para cada una se imprime
    fecha, monto, banco contraparte, clave de rastreo Nu, y la clave
    Santander más cercana por distancia de Levenshtein — una distancia
    corta (o una clave que es prefijo de la otra) delata un error/truncado
    de OCR en el lado Santander. OJO: solo las transferencias cuya CLABE
    contraparte es la cuenta Santander PROPIA del usuario pueden aparecer
    en sus estados de Santander; las que van con otros bancos o con
    cuentas Santander de terceros (familia, amigos) son no-match
    estructural, no error de OCR — el reporte las distingue.

Reporta el conteo de cada grupo y la tasa de reconciliación ajustada
excluyendo el grupo (a).

Uso:
    uv run python tests/diagnostico_reconciliacion_nu.py
"""
from __future__ import annotations

import re
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

warnings.filterwarnings("ignore", category=FutureWarning)

import pandas as pd

from src.extractor_nu import extraer_directorio

RAIZ = Path(__file__).resolve().parents[1]
PARQUET_SANTANDER = RAIZ / "data" / "output" / "transacciones.parquet"

# Clave de rastreo en la descripción OCR de Santander (mayúsculas, puede
# venir truncada o con caracteres mal leídos — por eso el Levenshtein)
RE_CLAVE_SANT = re.compile(r"CLAVE DE RASTREO ([A-Z0-9]+)")

# Cuenta Santander propia del usuario: solo las transferencias con esta
# CLABE contraparte pueden reconciliar contra los estados de cuenta de
# Santander disponibles. Viene de data/cuentas_conocidas.json (ignorada
# por git); ver src/config_cuentas.py.
from src.config_cuentas import cargar_config_cuentas

CLABE_PROPIA = cargar_config_cuentas()["clabe_propia_santander"]

AMARILLO = "\033[93m"
VERDE = "\033[92m"
FIN = "\033[0m"


def levenshtein(a: str, b: str) -> int:
    """Distancia de edición clásica (DP de dos renglones, sin deps nuevas)."""
    if len(a) < len(b):
        a, b = b, a
    fila_prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        fila = [i]
        for j, cb in enumerate(b, 1):
            fila.append(min(fila_prev[j] + 1,          # borrado
                            fila[j - 1] + 1,           # inserción
                            fila_prev[j - 1] + (ca != cb)))  # sustitución
        fila_prev = fila
    return fila_prev[-1]


def clave_mas_cercana(clave_nu: str, claves_sant: list[str]) -> tuple[str, int]:
    """La clave Santander con menor distancia de edición a `clave_nu`."""
    mejor, mejor_dist = "", 10 ** 9
    for c in claves_sant:
        d = levenshtein(clave_nu, c)
        if d < mejor_dist:
            mejor, mejor_dist = c, d
    return mejor, mejor_dist


def main() -> int:
    # ---- lado Nu: transferencias SPEI con clave de rastreo -----------------
    nu = extraer_directorio(RAIZ / "data" / "pdfs_nu")
    nu_spei = nu[nu["clave_rastreo"].notna()].copy()
    nu_spei["clave_norm"] = nu_spei["clave_rastreo"].str.upper()

    # ---- lado Santander: claves de rastreo desde la descripción OCR --------
    trans = pd.read_parquet(PARQUET_SANTANDER)
    sant = trans[trans["banco"] == "santander"].copy()
    sant["clave_norm"] = (sant["descripcion_raw"].str.upper()
                          .str.extract(RE_CLAVE_SANT, expand=False))
    claves_sant = sorted(sant["clave_norm"].dropna().unique())
    cobertura_ini = sant["fecha"].min().date()
    cobertura_fin = sant["fecha"].max().date()

    print(f"\nSPEI de Nu con clave de rastreo: {len(nu_spei)}")
    print(f"Claves de rastreo en Santander (OCR): {len(claves_sant)}")
    print(f"Cobertura Santander (calculada de los datos): "
          f"{cobertura_ini} → {cobertura_fin}")

    # ---- reconciliación por match exacto de clave ---------------------------
    nu_spei["match"] = nu_spei["clave_norm"].isin(set(claves_sant))
    n_match = int(nu_spei["match"].sum())
    sin_match = nu_spei[~nu_spei["match"]].copy()
    print(f"\nMatch exacto por clave: {n_match}/{len(nu_spei)} "
          f"({n_match / len(nu_spei):.1%} tasa bruta)")

    # ---- partición de las no reconciliadas ---------------------------------
    fechas = pd.to_datetime(sin_match["fecha"])
    en_rango = ((fechas.dt.date >= cobertura_ini)
                & (fechas.dt.date <= cobertura_fin))
    grupo_a = sin_match[~en_rango]
    grupo_b = sin_match[en_rango].sort_values("fecha")

    print(f"\n(a) Fuera de cobertura Santander (esperadas): {len(grupo_a)}")
    if len(grupo_a):
        print(f"    rango de fechas: {grupo_a['fecha'].min()} → "
              f"{grupo_a['fecha'].max()} | "
              f"monto total: ${grupo_a['monto'].abs().sum():,.2f}")

    print(f"\n(b) Dentro de cobertura y sin match: {len(grupo_b)}")
    n_ocr, n_estructural = 0, 0
    if len(grupo_b):
        print(f"\n{'fecha':<11} {'monto':>11} {'contraparte':<15} "
              f"{'clave Nu':<31} {'clave Santander más cercana':<31} {'dist':>4}")
        print("-" * 110)
        for _, row in grupo_b.iterrows():
            cercana, dist = clave_mas_cercana(row["clave_norm"], claves_sant)
            es_propia = row["clabe_contraparte"] == CLABE_PROPIA
            # dist chica o prefijo = probable error/truncado de OCR en Santander
            es_prefijo = (row["clave_norm"].startswith(cercana)
                          or cercana.startswith(row["clave_norm"]))
            if es_prefijo or dist <= 3 or es_propia:
                n_ocr += 1
                motivo = ("prefijo (OCR truncado)" if es_prefijo
                          else "posible error de OCR" if dist <= 3
                          else "cuenta propia: debería estar en Santander")
                nota = f" {AMARILLO}← {motivo}{FIN}"
            else:
                n_estructural += 1
                nota = ""  # contraparte de terceros: no-match estructural
            print(f"{row['fecha']!s:<11} {row['monto']:>11,.2f} "
                  f"{(row['banco_contraparte'] or '?'):<15} "
                  f"{row['clave_norm']:<31} {cercana:<31} {dist:>4}{nota}")

        # Con contraparte de terceros (otros bancos o cuentas Santander
        # ajenas) el no-match es estructural: esas transferencias no pasan
        # por los estados de cuenta Santander del usuario
        print(f"\n    Candidatos a error de OCR: {n_ocr} | "
              f"no-match estructural (contraparte de terceros): {n_estructural}")
        print(f"    Por contraparte: "
              f"{grupo_b['banco_contraparte'].value_counts().to_dict()}")

    # ---- tasa ajustada ------------------------------------------------------
    base_ajustada = len(nu_spei) - len(grupo_a)
    print(f"\n{'-' * 60}")
    print(f"Tasa bruta:     {n_match}/{len(nu_spei)} "
          f"= {n_match / len(nu_spei):.1%}")
    print(f"Tasa ajustada (excluyendo grupo a): {n_match}/{base_ajustada} "
          f"= {n_match / base_ajustada:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
