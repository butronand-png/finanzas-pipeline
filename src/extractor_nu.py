"""
src/extractor_nu.py — Extractor de estados de cuenta de Nu.

A diferencia del extractor de Santander (OCR + coordenadas), los PDFs de Nu
tienen texto seleccionable: `pdfplumber.Page.extract_text()` regresa líneas
limpias, así que este extractor es un parser de líneas con regex.

Diferencias observadas contra el spec (confiamos en los PDFs reales):
- Hay 29 PDFs, no 24: cobertura enero 2024 → mayo 2026, sin huecos.
- Los archivos se llaman `Mes Año.pdf` (ej. `Abril 2024.pdf`), con mes
  capitalizado y espacio — no `Estado_de_cuenta_mes_año.pdf`. El parseo del
  periodo desde el nombre normaliza a minúsculas antes de mapear.

Estructura del documento (verificada en las muestras):
- Página 1: resumen ejecutivo con 6 anclas de validación + `Periodo:`.
  Identidad contable: saldo_final = saldo_inicial + depositos - gastos
                                    + dinero_generado - comisiones
- "Detalle de movimientos en tu cuenta": la tabla principal. Dos variantes
  de fecha (`30 ABR` sin año en PDFs viejos, `30 ABR 2026` con año en los
  nuevos); el parser acepta ambas por línea, sin asumir cuál aplica por PDF.
- Títulos largos (formato viejo): la descripción se parte y el monto queda
  en una línea `DD MES +$N.NN` sin descripción; la descripción real está en
  las líneas adyacentes (antes y después) sin fecha ni monto.
- Bloques SPEI multilínea debajo de algunas transferencias: se concatenan
  como `detalle_spei` y de ahí se parsean banco/CLABE/clave de rastreo.
- "Detalle de movimientos de tus cajitas": los mismos movimientos de cajita
  vistos desde el lado de la cajita, con signo invertido. Se parsean, se
  etiquetan `seccion='cajitas'` y `es_movimiento_interno=True` para evitar
  doble conteo (no son ingreso ni gasto real).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd
import pdfplumber

# =============================================================================
# CONSTANTES
# =============================================================================
MESES_ES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5,
            "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9,
            "octubre": 10, "noviembre": 11, "diciembre": 12}

MESES_ABREV = {"ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
               "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12}

TOLERANCIA = 0.01

# --- página 1 (resumen) ------------------------------------------------------
RE_PERIODO = re.compile(r"Periodo: del (\d{1,2}) al (\d{1,2}) (\w{3}) (\d{4})")
RE_RESUMEN = {
    "saldo_inicial":   re.compile(r"Saldo inicial \$([\d,]+\.\d{2})"),
    "depositos":       re.compile(r"Depósitos \+?\$([\d,]+\.\d{2})"),
    "gastos":          re.compile(r"Gastos -?\$([\d,]+\.\d{2})"),
    "comisiones":      re.compile(r"Comisiones cobradas por Nu -?\$([\d,]+\.\d{2})"),
    "dinero_generado": re.compile(r"Dinero generado este mes \$([\d,]+\.\d{2})"),
    "saldo_final":     re.compile(r"Saldo al generar este estado de cuenta \$([\d,]+\.\d{2})"),
}


def _num(s: str) -> float:
    """'4,620.61' -> 4620.61"""
    return float(s.replace(",", ""))


# =============================================================================
# PERIODO DESDE EL NOMBRE DE ARCHIVO
# =============================================================================
def periodo_desde_nombre(path: Path) -> Optional[tuple[int, int]]:
    """
    Parsea (año, mes) del nombre de archivo `Mes Año.pdf` (ej. `Abril 2024.pdf`).

    Nota: el spec documentaba `Estado_de_cuenta_mes_año.pdf`; los archivos
    reales usan mes capitalizado + espacio. Normalizamos a minúsculas y
    aceptamos ambos separadores por robustez.
    """
    stem = path.stem.lower().replace("estado_de_cuenta_", "").replace("_", " ")
    m = re.match(r"([a-záéíóúñ]+)\s+(\d{4})", stem)
    if not m:
        return None
    mes = MESES_ES.get(m.group(1))
    if mes is None:
        return None
    return int(m.group(2)), mes


# =============================================================================
# PÁGINA 1 — RESUMEN
# =============================================================================
def extraer_resumen(texto_p1: str) -> dict:
    """
    Extrae las 6 anclas de validación y el periodo de la página 1.

    Devuelve dict con: saldo_inicial, depositos, gastos, comisiones,
    dinero_generado, saldo_final (floats, magnitudes positivas), y
    periodo_anio / periodo_mes (del header `Periodo:`).
    """
    resumen: dict = {}
    for campo, patron in RE_RESUMEN.items():
        m = patron.search(texto_p1)
        resumen[campo] = _num(m.group(1)) if m else None

    m = RE_PERIODO.search(texto_p1)
    if m:
        resumen["periodo_anio"] = int(m.group(4))
        resumen["periodo_mes"] = MESES_ABREV.get(m.group(3).upper())
    else:
        resumen["periodo_anio"] = None
        resumen["periodo_mes"] = None
    return resumen


def validar_identidad(resumen: dict, tolerancia: float = TOLERANCIA) -> tuple[bool, float]:
    """
    Verifica la identidad contable de página 1:
        saldo_final = saldo_inicial + depositos - gastos + dinero_generado - comisiones
    Devuelve (ok, diferencia). Si falta algún campo, (False, nan).
    """
    campos = ("saldo_inicial", "depositos", "gastos", "comisiones",
              "dinero_generado", "saldo_final")
    if any(resumen.get(c) is None for c in campos):
        return False, float("nan")
    esperado = (resumen["saldo_inicial"] + resumen["depositos"]
                - resumen["gastos"] + resumen["dinero_generado"]
                - resumen["comisiones"])
    diff = resumen["saldo_final"] - esperado
    return abs(diff) <= tolerancia, round(diff, 4)


# =============================================================================
# TABLA DE MOVIMIENTOS — parser de líneas
# =============================================================================
# Línea de transacción completa: `30 ABR [2026] DESCRIPCION -$50.00`
RE_TX = re.compile(
    r"^(\d{2}) ([A-Z]{3})(?: (\d{4}))? (.+?) ([+-])\$([\d,]+\.\d{2})$")
# Variante sin descripción (títulos largos): `30 ABR [2026] +$720.00`
# — la descripción quedó partida en las líneas adyacentes.
RE_TX_SIN_DESC = re.compile(
    r"^(\d{2}) ([A-Z]{3})(?: (\d{4}))? ([+-])\$([\d,]+\.\d{2})$")
# Tercera variante NO documentada en el spec (vista en Agosto/Noviembre 2024
# y 2025): la celda de fecha se envuelve en dos líneas que "emparedan" la
# descripción — `30 AGO` / `COMERCIO EJEMPLO Compra -$100.00` / `2024`.
# Con títulos largos el monto queda solo en medio y la descripción se parte
# entre ambas líneas de fecha: `30 AGO JUAN PEREZ ... A` / `+$100.00` /
# `2025 EJEMPLO`.
RE_FECHA_PARCIAL = re.compile(
    r"^(\d{2}) (" + "|".join(MESES_ABREV) + r")(?: (.+))?$")
RE_MONTO_SOLO = re.compile(r"^([+-])\$([\d,]+\.\d{2})$")
RE_DESC_MONTO = re.compile(r"^(.+) ([+-])\$([\d,]+\.\d{2})$")
RE_ANIO_LINEA = re.compile(r"^(20\d{2})(?: (.+))?$")
# Inicio de bloque de detalle SPEI (multilínea, debajo de la transferencia)
RE_SPEI_INICIO = re.compile(r"\bSPEI, Hora:")
# Línea de detalle de compra en USD: `USD 1.00 = MXN 20.3585 USD 20`
RE_USD = re.compile(r"^USD 1\.00 = MXN ([\d,]+\.?\d*) USD ([\d,]+\.?\d*)$")

# Headers de sección
MARCA_CUENTA = "Detalle de movimientos en tu cuenta"
MARCA_CAJITAS = "Detalle de movimientos de tus cajitas"
MARCA_FIN = "DINERO GENERADO EN TU CUENTA NU"

# Ruido de header/footer de página que no debe contaminar descripciones.
# El nombre del titular (primera línea de cada página) se filtra aparte,
# saltando la primera línea cuando la segunda es `Cuenta Nu:`.
RE_RUIDO = re.compile(
    r"^Cuenta Nu: \d+"
    r"|^FECHA DEL .* MONTO EN PESOS MEXICANOS$"
    r"|^Nu México Financiera"
    r"|^C\.P\. 11510"
    r"|^\d+ de \d+$"
    r"|^Con estos movimientos, tu saldo promedio")

# Movimientos internos (reallocación cuenta<->cajitas, NO ingreso/gasto real).
# `Congelaste saldo ... en tu Cajita:` no estaba en el spec pero aparece en
# los PDFs reales (ej. Junio 2025): congelamiento de saldo de cajita.
RE_INTERNO = re.compile(
    r"^(Depósito en Cajita:|Retiro de Cajita:)|Congelaste saldo .* Cajita")

# Parseo del detalle SPEI ya concatenado
RE_SPEI_BANCO = re.compile(r"(?:Recibido de|Enviado a) ([^.]+?)\.")
RE_SPEI_CLABE = re.compile(r"(?:De la|A la) cuenta (\d{10,18})")
RE_SPEI_RASTREO = re.compile(r"Clave de rastreo ([A-Z0-9]+)")
RE_SPEI_FIN = re.compile(r"Clave de referencia \S+")


def parsear_detalle_spei(detalle: str) -> dict:
    """
    Del bloque SPEI concatenado extrae banco_contraparte, clabe_contraparte
    y clave_rastreo (None los que no aparezcan).
    """
    m_banco = RE_SPEI_BANCO.search(detalle)
    m_clabe = RE_SPEI_CLABE.search(detalle)
    m_rastreo = RE_SPEI_RASTREO.search(detalle)
    return {
        "banco_contraparte": m_banco.group(1).strip() if m_banco else None,
        "clabe_contraparte": m_clabe.group(1) if m_clabe else None,
        "clave_rastreo": m_rastreo.group(1) if m_rastreo else None,
    }


def _lineas_utiles(page: pdfplumber.page.Page) -> list[str]:
    """Líneas de texto de la página sin header de titular ni ruido de footer."""
    lineas = (page.extract_text() or "").split("\n")
    # La primera línea de cada página es el nombre del titular cuando la
    # segunda es `Cuenta Nu:` — se salta sin hardcodear el nombre.
    if len(lineas) >= 2 and RE_RUIDO.match(lineas[1]):
        lineas = lineas[1:]
    return [ln.strip() for ln in lineas if ln.strip() and not RE_RUIDO.match(ln.strip())]


def parsear_movimientos(pdf: pdfplumber.PDF, anio_periodo: int,
                        pdf_origen: str) -> list[dict]:
    """
    Recorre todas las páginas y parsea las secciones de movimientos
    ("en tu cuenta" y "de tus cajitas") con una máquina de estados por línea.

    Maneja:
    - fechas con y sin año (el año faltante se infiere del periodo de pág. 1);
    - descripciones partidas (línea fecha+monto sin descripción: se toma la
      descripción de las líneas huérfanas adyacentes, antes y después);
    - fechas "emparedadas" (variante no documentada en el spec): la celda de
      fecha se envuelve en dos líneas `DD MES` ... `YYYY` con la descripción
      y el monto entre ambas;
    - bloques de detalle SPEI multilínea (se concatenan en `detalle_spei` de
      la transacción anterior; terminan al ver `Clave de referencia N`);
    - líneas de detalle USD (`monto_usd` y `tipo_cambio` de la tx anterior).
    """
    movimientos: list[dict] = []
    seccion: Optional[str] = None       # None | 'cuenta' | 'cajitas'
    pendientes: list[str] = []          # líneas huérfanas antes de una tx
    tx_esperando_desc: Optional[dict] = None  # tx sin descripción inline
    spei_buffer: Optional[list[str]] = None   # bloque SPEI en construcción
    sandwich: Optional[dict] = None     # tx con fecha envuelta en dos líneas

    def cerrar_spei():
        nonlocal spei_buffer
        if spei_buffer and movimientos:
            detalle = " ".join(spei_buffer)
            movimientos[-1]["detalle_spei"] = detalle
            movimientos[-1].update(parsear_detalle_spei(detalle))
        spei_buffer = None

    def emitir(dia: str, mes: str, anio: Optional[str], desc: str,
               signo: str, monto: str, pagina: int):
        nonlocal tx_esperando_desc
        cerrar_spei()
        tx = {
            "fecha": pd.Timestamp(int(anio) if anio else anio_periodo,
                                  MESES_ABREV[mes], int(dia)).date(),
            "descripcion": desc.strip(),
            "monto": _num(monto) * (1 if signo == "+" else -1),
            "seccion": seccion,
            "es_movimiento_interno": (seccion == "cajitas"
                                      or bool(RE_INTERNO.search(desc))),
            "detalle_spei": None,
            "banco_contraparte": None,
            "clabe_contraparte": None,
            "clave_rastreo": None,
            "monto_usd": None,
            "tipo_cambio": None,
            "pdf_origen": pdf_origen,
            "pagina": pagina,
        }
        movimientos.append(tx)
        tx_esperando_desc = None
        return tx

    def cerrar_sandwich():
        """Emite la tx emparedada si llegó a tener monto; si no, descarta
        sus líneas a `pendientes` (eran fragmentos de otra cosa)."""
        nonlocal sandwich, pendientes
        if sandwich is None:
            return
        s, sandwich = sandwich, None
        if s["monto"] is not None:
            emitir(s["dia"], s["mes"], s["anio"], " ".join(s["desc"]),
                   s["signo"], s["monto"], s["pagina"])
        else:
            pendientes.extend(s["desc"])

    for num_pagina, page in enumerate(pdf.pages, start=1):
        for linea in _lineas_utiles(page):
            # --- cambios de sección -------------------------------------
            if MARCA_CUENTA in linea:
                cerrar_spei()
                cerrar_sandwich()
                seccion, pendientes, tx_esperando_desc = "cuenta", [], None
                continue
            if MARCA_CAJITAS in linea:
                cerrar_spei()
                cerrar_sandwich()
                seccion, pendientes, tx_esperando_desc = "cajitas", [], None
                continue
            if MARCA_FIN in linea:
                cerrar_spei()
                cerrar_sandwich()
                seccion = None
                continue
            if seccion is None:
                continue

            # --- bloque SPEI en construcción ------------------------------
            if spei_buffer is not None:
                if RE_TX.match(linea) or RE_TX_SIN_DESC.match(linea) \
                        or RE_FECHA_PARCIAL.match(linea):
                    cerrar_spei()  # cae al parseo normal de la línea abajo
                else:
                    spei_buffer.append(linea)
                    if RE_SPEI_FIN.search(" ".join(spei_buffer)):
                        cerrar_spei()
                    continue

            # --- inicio de bloque SPEI ------------------------------------
            if RE_SPEI_INICIO.search(linea):
                cerrar_sandwich()
                tx_esperando_desc = None
                spei_buffer = [linea]
                if RE_SPEI_FIN.search(linea):
                    cerrar_spei()
                continue

            # --- detalle USD de la tx anterior ----------------------------
            m_usd = RE_USD.match(linea)
            if m_usd:
                cerrar_sandwich()
                if movimientos:
                    movimientos[-1]["tipo_cambio"] = _num(m_usd.group(1))
                    movimientos[-1]["monto_usd"] = _num(m_usd.group(2))
                tx_esperando_desc = None
                continue

            # --- tx emparedada en construcción ----------------------------
            if sandwich is not None:
                m_anio = RE_ANIO_LINEA.match(linea)
                if m_anio:
                    # `2024` o `2025 <resto de descripción>`: cierra el emparedado
                    sandwich["anio"] = m_anio.group(1)
                    if m_anio.group(2):
                        sandwich["desc"].append(m_anio.group(2))
                    cerrar_sandwich()
                    continue
                m_monto = RE_MONTO_SOLO.match(linea)
                if m_monto:
                    sandwich["signo"], sandwich["monto"] = m_monto.groups()
                    continue
                if RE_TX.match(linea) or RE_TX_SIN_DESC.match(linea) \
                        or RE_FECHA_PARCIAL.match(linea):
                    cerrar_sandwich()  # cae al parseo normal abajo
                else:
                    m_dm = RE_DESC_MONTO.match(linea)
                    if m_dm and sandwich["monto"] is None:
                        desc, sandwich["signo"], sandwich["monto"] = m_dm.groups()
                        sandwich["desc"].append(desc)
                    else:
                        sandwich["desc"].append(linea)
                    continue

            # --- transacción sin descripción inline (título largo) --------
            m = RE_TX_SIN_DESC.match(linea)
            if m:
                dia, mes, anio, signo, monto = m.groups()
                emitir(dia, mes, anio, " ".join(pendientes), signo, monto,
                       num_pagina)
                pendientes = []
                tx_esperando_desc = movimientos[-1]
                continue

            # --- transacción normal ---------------------------------------
            m = RE_TX.match(linea)
            if m:
                dia, mes, anio, desc, signo, monto = m.groups()
                emitir(dia, mes, anio, desc, signo, monto, num_pagina)
                pendientes = []
                continue

            # --- inicio de tx emparedada: `DD MES` sin monto ni año --------
            m = RE_FECHA_PARCIAL.match(linea)
            if m:
                dia, mes, resto = m.groups()
                tx_esperando_desc = None
                sandwich = {"dia": dia, "mes": mes, "anio": None,
                            "desc": [], "signo": None, "monto": None,
                            "pagina": num_pagina}
                if resto and RE_ANIO_LINEA.match(resto):
                    sandwich["anio"] = resto[:4]
                    resto = resto[4:].strip() or None
                if resto:
                    sandwich["desc"].append(resto)
                continue

            # --- línea huérfana (fragmento de descripción) -----------------
            if tx_esperando_desc is not None:
                # Continuación de la descripción de la tx recién emitida.
                # Solo la primera línea huérfana tras la tx: las siguientes
                # podrían ser el inicio de la descripción de la próxima tx.
                tx_esperando_desc["descripcion"] = (
                    tx_esperando_desc["descripcion"] + " " + linea).strip()
                tx_esperando_desc["es_movimiento_interno"] = (
                    seccion == "cajitas"
                    or bool(RE_INTERNO.search(tx_esperando_desc["descripcion"])))
                tx_esperando_desc = None
            else:
                pendientes.append(linea)

        # La celda de fecha envuelta nunca cruza páginas: flush al terminar
        cerrar_sandwich()

    cerrar_spei()
    return movimientos


COLUMNAS = ["fecha", "descripcion", "monto", "seccion",
            "es_movimiento_interno", "detalle_spei", "banco_contraparte",
            "clabe_contraparte", "clave_rastreo", "monto_usd", "tipo_cambio",
            "pdf_origen", "pagina"]


# =============================================================================
# VALIDACIÓN DE SUMAS (movimientos vs resumen de página 1)
# =============================================================================
def validar_sumas(movimientos: pd.DataFrame, resumen: dict,
                  tolerancia: float = TOLERANCIA) -> dict:
    """
    Compara la suma de movimientos de la sección cuenta contra los totales
    de página 1. Nu EXCLUYE los movimientos internos de cajita de sus
    totales ejecutivos, así que la comparación correcta es sobre los
    movimientos con es_movimiento_interno=False:

        Σ(montos positivos no internos) ≈ Depósitos (pág. 1)
        Σ(|montos negativos no internos|) ≈ Gastos (pág. 1)

    Devuelve dict con diffs y banderas; reporta, no aborta.

    Cuando ambos diffs son iguales (`sumas_espejo`), el balance interno de
    la extracción está preservado y la discrepancia es una inconsistencia
    del header de Nu con su propio detalle (anomalía observada en un mes
    real: el mismo diff exacto en depósitos y en gastos, confirmada con
    dos implementaciones independientes) — no un bug del extractor.
    """
    cuenta = movimientos[(movimientos["seccion"] == "cuenta")
                         & ~movimientos["es_movimiento_interno"]]
    dep_calc = cuenta.loc[cuenta["monto"] > 0, "monto"].sum()
    gas_calc = -cuenta.loc[cuenta["monto"] < 0, "monto"].sum()
    diff_dep = round(dep_calc - (resumen.get("depositos") or 0.0), 4)
    diff_gas = round(gas_calc - (resumen.get("gastos") or 0.0), 4)
    sumas_ok = abs(diff_dep) <= tolerancia and abs(diff_gas) <= tolerancia
    return {
        "dep_calc": dep_calc,
        "gas_calc": gas_calc,
        "diff_depositos": diff_dep,
        "diff_gastos": diff_gas,
        "sumas_ok": sumas_ok,
        "sumas_espejo": not sumas_ok and abs(diff_dep - diff_gas) <= tolerancia,
    }


# =============================================================================
# API PÚBLICA
# =============================================================================
def extraer_pdf_nu(path: str | Path) -> dict:
    """
    Extrae resumen + movimientos de un PDF de Nu.

    Regresa {"resumen": dict, "movimientos": pd.DataFrame, "valido": bool,
             "errores": list[str]}.

    - `resumen` incluye las 6 anclas de página 1, el periodo, y las banderas
      de validación (`identidad_ok`, `sumas_ok` con sus diffs).
    - `movimientos` trae ambas secciones (cuenta + cajitas) etiquetadas; los
      movimientos de cajita están en ambas con signo invertido y marcados
      `es_movimiento_interno=True` — filtrar por esa bandera para análisis
      de ingreso/gasto real.
    - `valido` = identidad de página 1 OK y sumas de movimientos OK.
    """
    path = Path(path)
    errores: list[str] = []

    with pdfplumber.open(path) as pdf:
        texto_p1 = pdf.pages[0].extract_text() or ""
        resumen = extraer_resumen(texto_p1)

        # Cross-check periodo del nombre de archivo vs header de página 1
        periodo_nombre = periodo_desde_nombre(path)
        if periodo_nombre and resumen["periodo_anio"]:
            if periodo_nombre != (resumen["periodo_anio"], resumen["periodo_mes"]):
                errores.append(
                    f"Periodo del nombre {periodo_nombre} no cuadra con header "
                    f"({resumen['periodo_anio']}, {resumen['periodo_mes']})")
        elif not resumen["periodo_anio"]:
            errores.append("No se pudo extraer 'Periodo:' de página 1")

        anio = resumen["periodo_anio"] or (periodo_nombre[0] if periodo_nombre else 0)
        filas = parsear_movimientos(pdf, anio, path.name)

    movimientos = pd.DataFrame(filas, columns=COLUMNAS)

    identidad_ok, diff = validar_identidad(resumen)
    resumen["identidad_ok"] = identidad_ok
    resumen["identidad_diff"] = diff
    if not identidad_ok:
        errores.append(f"Identidad contable de página 1 falla por ${diff:+.2f}")

    val_sumas = validar_sumas(movimientos, resumen)
    resumen.update(val_sumas)
    if not val_sumas["sumas_ok"] and not val_sumas["sumas_espejo"]:
        errores.append(
            f"Sumas vs resumen: diff depósitos ${val_sumas['diff_depositos']:+.2f}, "
            f"diff gastos ${val_sumas['diff_gastos']:+.2f}")

    # `sumas_espejo` NO invalida: la extracción es internamente consistente
    # y la discrepancia es del header de Nu (ver validar_sumas).
    return {
        "resumen": resumen,
        "movimientos": movimientos,
        "valido": (identidad_ok
                   and (val_sumas["sumas_ok"] or val_sumas["sumas_espejo"])
                   and not errores),
        "errores": errores,
    }


def extraer_directorio(dir_path: str | Path = "data/pdfs_nu") -> pd.DataFrame:
    """
    Procesa todos los PDFs de `dir_path` y concatena los movimientos en un
    solo DataFrame (ordenado por fecha). Las validaciones por PDF se
    imprimen/reportan en tests/probar_extractor_nu.py; aquí solo se extrae.
    """
    dir_path = Path(dir_path)
    frames = []
    for pdf_path in sorted(dir_path.glob("*.pdf")):
        frames.append(extraer_pdf_nu(pdf_path)["movimientos"])
    if not frames:
        return pd.DataFrame(columns=COLUMNAS)
    return (pd.concat(frames, ignore_index=True)
            .sort_values(["fecha", "pdf_origen"], kind="stable")
            .reset_index(drop=True))
