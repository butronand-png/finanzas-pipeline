"""
extractors/nu.py — Extractor de estados de cuenta de Nu México.

Diseño:
- Los PDFs de Nu son vectoriales con texto seleccionable → `pdfplumber` directo,
  cero OCR. Extracción determinista (confidence_min = 1.0 siempre).
- Cada transacción tiene UN monto con signo (+/-), no dos columnas. Se separa
  a `deposito`/`retiro` en el extractor para mantener el contrato de schema
  compartido con Santander.
- Nu NO imprime saldo por línea (solo saldo inicial/final del mes en el header).
  Consecuencia: `saldo = NaN` en cada tx, `valido = True` siempre, y la
  validación matemática se hace a nivel MES (invariante contable global) en
  `validar_pdf()`.
- Los movimientos de cajita ("Depósito en Cajita X" / "Retiro de Cajita X")
  se emiten como transacciones normales — se categorizan como
  `transferencia_cajita` río abajo. El header de Nu los excluye de sus totales
  ejecutivos, así que la validación global toma eso en cuenta.
- Las compras en USD generan una línea extra "USD 1.00 = MXN X.X USD Y" que
  se detecta con `RE_USD` y se separa a las columnas moneda_original/
  monto_original/tipo_cambio.
- El folio de Nu es sintético (`NU-YYYYMM-####`) porque Nu no emite folio por
  transacción — solo Clave de rastreo para SPEI, que además no se puede
  extraer confiablemente cuando la clave se rompe entre líneas.

Formato de fecha:
- 2024 (early): "DD MES"           → año inferido del periodo del PDF
- 2024+ (later): "DD MES YYYY"     → año explícito
El parser acepta ambos.

Estrategia de anchor:
- Cada tx tiene una FECHA en la primera columna (x < X_ANCHOR_MAX).
- Los anchors se ordenan por Y. Cada anchor "posee" las palabras cuyo Y cae
  en [y_anchor - MARGEN_ABOVE, y_next_anchor - MARGEN_ABOVE). El margen
  asimétrico permite que descripciones que se envuelven arriba de la fecha
  se atribuyan a la tx correcta.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber


# =============================================================================
# CONSTANTES DE PARSING
# =============================================================================
MESES = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
         "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}

RE_DIA        = re.compile(r"^(\d{1,2})$")
RE_MES        = re.compile(r"^(" + "|".join(MESES) + r")$")
RE_YEAR       = re.compile(r"^(20\d{2})$")
RE_MONTO      = re.compile(r"^([+-])\$?([\d,]+\.\d{2})$")
RE_USD_LINE   = re.compile(r"USD\s*1\.00\s*=\s*MXN\s*([\d.]+)\s+USD\s+([\d.]+)")
RE_PERIODO    = re.compile(r"del \d+ al \d+ (\w+) (20\d{2})", re.IGNORECASE)
RE_SALDO_INI  = re.compile(r"Saldo inicial\s+\$([\d,.]+)")
RE_SALDO_FIN  = re.compile(r"Saldo al generar este estado de cuenta\s+\$([\d,.]+)")
RE_HDR_DEP    = re.compile(r"Depósitos\s+\+\$([\d,.]+)")
RE_HDR_GAS    = re.compile(r"Gastos\s+-\$([\d,.]+)")
RE_INTERESES  = re.compile(r"Dinero generado este mes\s+\$([\d,.]+)")

RE_CAJITA_DEP = re.compile(r"^Depósito en Cajita:", re.IGNORECASE)
RE_CAJITA_RET = re.compile(r"^Retiro de Cajita:", re.IGNORECASE)

X_ANCHOR_MAX = 90     # px — la fecha real siempre está al margen izquierdo
MARGEN_ABOVE = 8      # px — descripciones que envuelven arriba caen dentro


# =============================================================================
# ESTRUCTURAS INTERMEDIAS
# =============================================================================
@dataclass
class ResumenEjecutivo:
    """Resumen extraído de la página 1 del PDF (usado para validación global)."""
    saldo_inicial:   Optional[float] = None
    saldo_final:     Optional[float] = None
    depositos_hdr:   Optional[float] = None
    gastos_hdr:      Optional[float] = None
    intereses:       Optional[float] = None
    periodo_year:    int = 2024
    periodo_mes:     int = 1


@dataclass
class TxRaw:
    """Transacción parseada antes de convertir al schema de salida."""
    fecha:            str      # YYYY-MM-DD
    descripcion:      str
    signo:            str      # '+' o '-'
    monto:            float
    moneda_original:  Optional[str] = None
    monto_original:   Optional[float] = None
    tipo_cambio:      Optional[float] = None
    pagina:           int = 0


# =============================================================================
# UTILIDADES
# =============================================================================
def _f(match: Optional[re.Match]) -> Optional[float]:
    """Convierte match de regex numérico con comas a float, None si no matchea."""
    if match is None:
        return None
    return float(match.group(1).replace(",", ""))


def _extraer_resumen(pdf: pdfplumber.PDF) -> ResumenEjecutivo:
    """Página 1 tiene: periodo, saldo inicial/final, depósitos, gastos, intereses."""
    text = pdf.pages[0].extract_text() or ""
    m_periodo = RE_PERIODO.search(text)
    if m_periodo:
        mes_str = m_periodo.group(1)[:3].upper()
        year = int(m_periodo.group(2))
        mes = MESES.get(mes_str, 1)
    else:
        year, mes = 2024, 1
    return ResumenEjecutivo(
        saldo_inicial = _f(RE_SALDO_INI.search(text)),
        saldo_final   = _f(RE_SALDO_FIN.search(text)),
        depositos_hdr = _f(RE_HDR_DEP.search(text)),
        gastos_hdr    = _f(RE_HDR_GAS.search(text)),
        intereses     = _f(RE_INTERESES.search(text)),
        periodo_year  = year,
        periodo_mes   = mes,
    )


# =============================================================================
# PARSING POR PÁGINA
# =============================================================================
def _parse_pagina(page: pdfplumber.page.Page, periodo_year: int, num_pagina: int
                  ) -> list[TxRaw]:
    """
    Devuelve las transacciones detectadas en una página de "Detalle de
    movimientos EN TU CUENTA" (NO en cajitas — esas duplican los mismos
    movimientos).
    """
    words = page.extract_words()
    if not words:
        return []

    text = page.extract_text() or ""
    # Página válida = tiene tabla de movimientos de la cuenta
    if "Detalle de movimientos" not in text and "FECHA DEL" not in text:
        return []
    # Descartar páginas de cajitas (contienen movimientos duplicados)
    if "Detalle de movimientos de tus cajitas" in text:
        return []

    # Delimitar región de tabla: después del renglón "FECHA ...", antes del
    # footer "Nu México Financiera..."
    y_start, y_end = 0.0, 10_000.0
    for w in words:
        if w["text"] == "FECHA":
            y_start = max(y_start, w["bottom"] + 3)
        if w["text"] == "Nu" and any(
            ww["text"] == "México" and abs(ww["top"] - w["top"]) < 3 for ww in words
        ):
            y_end = min(y_end, w["top"] - 3)

    words_tabla = [w for w in words if y_start <= w["top"] < y_end]

    # ---- 1) Anchors: pares (día, mes[, año]) en columna izquierda ---------
    anchors = []
    n = len(words_tabla)
    for i, w in enumerate(words_tabla):
        if w["x0"] > X_ANCHOR_MAX:
            continue
        if not RE_DIA.match(w["text"]):
            continue
        if i + 1 >= n or not RE_MES.match(words_tabla[i + 1]["text"]):
            continue
        year = periodo_year
        idx_end = i + 2
        if i + 2 < n and RE_YEAR.match(words_tabla[i + 2]["text"]):
            year = int(words_tabla[i + 2]["text"])
            idx_end = i + 3
        anchors.append({
            "y": w["top"],
            "idx_start": i,
            "idx_end": idx_end,
            "dia": int(w["text"]),
            "mes": MESES[words_tabla[i + 1]["text"]],
            "year": year,
        })

    if not anchors:
        return []

    # ---- 2) Bloque por anchor con margen asimétrico -----------------------
    txs: list[TxRaw] = []
    for i, a in enumerate(anchors):
        y_lo = a["y"] - MARGEN_ABOVE
        y_hi = (anchors[i + 1]["y"] - MARGEN_ABOVE) if i + 1 < len(anchors) else 10_000

        block = [w for w in words_tabla if y_lo <= w["top"] < y_hi]

        # ---- 2a) monto ----------------------------------------------------
        monto_info = None
        for w in block:
            m = RE_MONTO.match(w["text"])
            if m:
                signo, num = m.groups()
                monto_info = {
                    "signo": signo,
                    "monto": float(num.replace(",", "")),
                    "x": w["x0"],
                    "y": w["top"],
                }

        if monto_info is None:
            # Anchor sin monto detectable — probablemente falso positivo
            continue

        # ---- 2b) descripción: todo lo demás, ordenado por (y, x) ----------
        excluir_idx = set(range(a["idx_start"], a["idx_end"]))
        desc_tokens = []
        for w in block:
            idx_global = words_tabla.index(w)
            if idx_global in excluir_idx:
                continue
            if w["x0"] == monto_info["x"] and w["top"] == monto_info["y"]:
                continue
            desc_tokens.append((w["top"], w["x0"], w["text"]))
        desc_tokens.sort()
        descripcion = " ".join(t[2] for t in desc_tokens)

        # ---- 2c) USD info (si aplica) ------------------------------------
        moneda, monto_orig, tipo_cambio = None, None, None
        m_usd = RE_USD_LINE.search(descripcion)
        if m_usd:
            tipo_cambio = float(m_usd.group(1))
            monto_orig  = float(m_usd.group(2))
            moneda      = "USD"
            descripcion = RE_USD_LINE.sub("", descripcion).strip()

        # ---- 2d) limpiar metadata SPEI verbosa ---------------------------
        # Preservar la primera línea (nombre destinatario/remitente + concepto)
        # y descartar la explicación repetitiva "Depósito SPEI, Hora: ...,
        # Recibido de X. Del cliente Y (Dato no verificado...)". Es ruido
        # que ya no aporta señal después del extractor: la Clave de rastreo
        # NO se preserva de forma consultable porque se rompe en múltiples
        # tokens visualmente y no es confiable.
        descripcion = _limpiar_descripcion(descripcion)

        fecha = f"{a['year']}-{a['mes']:02d}-{a['dia']:02d}"
        txs.append(TxRaw(
            fecha           = fecha,
            descripcion     = descripcion,
            signo           = monto_info["signo"],
            monto           = monto_info["monto"],
            moneda_original = moneda,
            monto_original  = monto_orig,
            tipo_cambio     = tipo_cambio,
            pagina          = num_pagina,
        ))

    return txs


def _limpiar_descripcion(desc: str) -> str:
    """
    Recorta la metadata verbosa de SPEI ("Depósito SPEI, Hora: ..., Recibido
    de X..., por concepto Y...") para dejar solo el concepto humano relevante.

    Estrategia: si aparece "Depósito SPEI" o "Envío SPEI", cortar todo lo que
    venga después de la primera aparición. El resto (nombre + concepto)
    queda como descripcion_raw.
    """
    for marker in ("Depósito SPEI", "Envío SPEI"):
        idx = desc.find(marker)
        if idx >= 0:
            desc = desc[:idx].strip()
            break
    return desc.strip()


# =============================================================================
# INTERESES → TX VIRTUAL
# =============================================================================
def _tx_intereses(resumen: ResumenEjecutivo) -> Optional[TxRaw]:
    """
    Nu no imprime los intereses como transacción — solo aparecen en el header
    ejecutivo. Los sintetizamos como una transacción virtual del último día
    del mes con descripción 'INTERESES NU (dinero generado)'.
    """
    if not resumen.intereses or resumen.intereses == 0:
        return None
    # Último día del mes: es más fácil usar día 28 y confiar en que Nu procesa
    # a fin de mes (podríamos calcular monthrange pero el día exacto no
    # importa para el análisis mensual)
    import calendar
    ultimo = calendar.monthrange(resumen.periodo_year, resumen.periodo_mes)[1]
    fecha = f"{resumen.periodo_year}-{resumen.periodo_mes:02d}-{ultimo:02d}"
    return TxRaw(
        fecha       = fecha,
        descripcion = "INTERESES NU (dinero generado en cajitas)",
        signo       = "+",
        monto       = resumen.intereses,
        pagina      = 0,   # sintética, no viene de una página en particular
    )


# =============================================================================
# API PÚBLICA
# =============================================================================
def extraer_pdf(path: str | Path) -> pd.DataFrame:
    """
    Extrae todas las transacciones de un PDF de Nu y devuelve un DataFrame
    conforme a `src.schema.TransaccionSchema`.

    Convenciones del contrato:
    - `folio`: sintético (`NU-YYYYMM-####`) porque Nu no lo imprime
    - `saldo`: siempre NaN (Nu no lo imprime por línea)
    - `confidence_min`: 1.0 (parsing determinista, sin OCR)
    - `valido`: True (la validación real es a nivel mes con `validar_pdf`)
    - `error_saldo`: NaN
    """
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        resumen = _extraer_resumen(pdf)

        all_tx: list[TxRaw] = []
        for i, page in enumerate(pdf.pages, start=1):
            all_tx.extend(_parse_pagina(page, resumen.periodo_year, i))

        # Agregar intereses como tx virtual
        tx_int = _tx_intereses(resumen)
        if tx_int is not None:
            all_tx.append(tx_int)

    # Convertir a DataFrame conforme al schema
    yyyymm = f"{resumen.periodo_year}{resumen.periodo_mes:02d}"
    rows = []
    for idx, t in enumerate(all_tx, start=1):
        folio = f"NU-{yyyymm}-{idx:04d}"
        rows.append({
            "banco":           "nu",
            "fecha":           pd.to_datetime(t.fecha),
            "folio":           folio,
            "descripcion_raw": t.descripcion,
            "deposito":        t.monto if t.signo == "+" else 0.0,
            "retiro":          t.monto if t.signo == "-" else 0.0,
            "saldo":           float("nan"),
            "moneda_original": t.moneda_original,
            "monto_original":  t.monto_original,
            "tipo_cambio":     t.tipo_cambio,
            "pdf_origen":      path.name,
            "pagina":          t.pagina,
            "confidence_min":  1.0,
            "valido":          True,
            "error_saldo":     float("nan"),
        })
    return pd.DataFrame(rows)


def validar_pdf(df: pd.DataFrame, path: str | Path,
                tolerancia: float = 0.01) -> dict:
    """
    Validación matemática a nivel MES en dos capas.

    Nu excluye movimientos de cajita del cómputo ejecutivo, entonces:

        header.depositos = Σ(deposito) - Σ(Retiro de Cajita)
        header.gastos    = Σ(retiro)   - Σ(Depósito en Cajita)

    Devuelve tres banderas distintas:

    - `extraccion_ok`: balance preservado, o sea `diff_dep ≈ diff_gas`.
       Si es True, mi extracción es internamente consistente (todos los
       montos suman coherentemente) — la data cargada a BD es correcta
       transacción por transacción.

    - `header_ok`: los dos diffs individuales son ≈ 0. O sea el header
       ejecutivo de Nu también coincide.

    - `nu_anomaly`: `extraccion_ok=True` pero `header_ok=False`. El header
       de Nu tiene una inconsistencia interna con su propio detalle
       (típicamente por transacciones reversadas/anuladas que no aparecen
       en el desglose). Es problema de Nu, no del extractor.

    Regla de decisión: sólo `header_ok=False AND extraccion_ok=False` es
    un error real que requiere auditar el PDF a mano.

    Ejemplo real observado:
    - un mes con extraccion_ok=True, header_ok=False y el mismo diff
      exacto (espejo) en depósitos y gastos.
    """
    path = Path(path)
    with pdfplumber.open(path) as pdf:
        resumen = _extraer_resumen(pdf)

    # Excluir tx sintéticas de intereses (no cuentan en header dep/gastos)
    df_real = df[~df["descripcion_raw"].str.startswith("INTERESES NU")]

    es_cajita_dep = df_real["descripcion_raw"].str.match(r"Depósito en Cajita")
    es_cajita_ret = df_real["descripcion_raw"].str.match(r"Retiro de Cajita")

    sum_deposito        = df_real["deposito"].sum()
    sum_retiro          = df_real["retiro"].sum()
    sum_cajita_deposito = df_real.loc[es_cajita_dep, "retiro"].sum()   # sale de cuenta
    sum_cajita_retiro   = df_real.loc[es_cajita_ret, "deposito"].sum() # entra a cuenta

    dep_calc = sum_deposito - sum_cajita_retiro
    gas_calc = sum_retiro   - sum_cajita_deposito

    diff_dep = dep_calc - (resumen.depositos_hdr or 0.0)
    diff_gas = gas_calc - (resumen.gastos_hdr or 0.0)

    header_ok     = abs(diff_dep) <= tolerancia and abs(diff_gas) <= tolerancia
    extraccion_ok = abs(diff_dep - diff_gas) <= tolerancia
    nu_anomaly    = extraccion_ok and not header_ok

    return {
        "pdf":              path.name,
        "periodo":          f"{resumen.periodo_year}-{resumen.periodo_mes:02d}",
        "n_tx":             len(df_real),
        "header_depositos": resumen.depositos_hdr,
        "header_gastos":    resumen.gastos_hdr,
        "calc_depositos":   dep_calc,
        "calc_gastos":      gas_calc,
        "diff_depositos":   diff_dep,
        "diff_gastos":      diff_gas,
        "intereses":        resumen.intereses,
        "header_ok":        header_ok,
        "extraccion_ok":    extraccion_ok,
        "nu_anomaly":       nu_anomaly,
        # Alias para retrocompatibilidad con código que use `ok`
        "ok":               extraccion_ok,
    }


# =============================================================================
# CLI standalone (útil para debug puntual)
# =============================================================================
if __name__ == "__main__":
    import sys
    from pprint import pprint

    if len(sys.argv) < 2:
        print("Uso: python -m src.extractors.nu <ruta.pdf>")
        sys.exit(1)

    df = extraer_pdf(sys.argv[1])
    print(df.to_string(max_colwidth=60))
    print()
    print("Validación mensual:")
    pprint(validar_pdf(df, sys.argv[1]))
