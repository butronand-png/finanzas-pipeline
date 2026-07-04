"""
categorizador.py — Reglas de categorización unificadas para Santander + Nu.

Arquitectura:
- PATRONES_TEXTO_SANTANDER: reglas específicas del formato Santander (con
  códigos de comercio, SPEI con "ENVIADO A / RECIBIDO DE", etc.)
- PATRONES_TEXTO_NU: reglas específicas de Nu (compras con nombre de comercio
  como texto legible: "OXXO HOLBEIN", "DLO*UBER EATS", "Depósito en Cajita:",
  "ANTHROPIC", etc.)
- CUENTAS: mapeo de números de cuenta CLABE conocidos → contraparte
- COMERCIOS: mapeo de prefijos de código Santander (TEX, CCO, etc.)
- PROCESADORES_GENERICOS: cuando Santander pone "0000000000000" y solo tenemos
  el nombre del procesador (LA ESTACION, ABTS MAPACHINOS...)

Orden de precedencia en `categorizar()`:
  1. Patrones específicos de Nu (aplican solo a descripciones Nu)
  2. Patrones específicos de Santander (aplican solo a Santander)
  3. Cuenta CLABE (aplica a SPEI en cualquier banco si el nombre matchea)
  4. Procesadores genéricos (Santander legacy)
  5. Prefijo de código (Santander legacy)
  6. Fallback: CONSUMO LOCAL AJENO → comida_otros; else sin_categoria
"""
import re

from src.config_cuentas import cargar_config_cuentas


# ============================================================
# CUENTAS DESTINO/ORIGEN CONOCIDAS (SPEI) — desde config ignorada
# ============================================================
# Las CLABEs reales con nombres de personas viven en
# data/cuentas_conocidas.json (fuera de git); ver src/config_cuentas.py.
_CONFIG = cargar_config_cuentas()
CUENTAS = _CONFIG["cuentas"]


# ============================================================
# COMERCIOS SANTANDER (prefijo de código en descripción OCR)
# ============================================================
COMERCIOS = {
    # Comida — restaurantes
    "TEX":  ("Extra K ITAM", "comida_itam"),
    "ITA":  ("ITAM (varios)", "itam_otros"),
    "RAD":  ("McDonald's", "comida_rapida"),
    "SAMF": ("Rest El Cactus", "comida_restaurante"),
    "OFF":  ("Panda Express", "comida_rapida"),
    "CRI":  ("BPK*Fastfood CA", "comida_rapida"),
    "PLA":  ("BPK*Fastfood Zapopan", "comida_rapida"),
    "RYO":  ("Rest Daruma", "comida_restaurante"),
    "RHO":  ("Taquitos Las Ce", "comida_restaurante"),
    "VMA":  ("Rest Vecchio", "comida_restaurante"),
    "OMQ":  ("Rest Foro Versal", "comida_restaurante"),
    "OPM":  ("Cafe Parabien", "comida_restaurante"),
    "SRO":  ("Sushi Roll", "comida_restaurante"),
    "TDP":  ("T Don Polo Parque", "comida_restaurante"),
    "SAS":  ("BPK*Legalcudill", "comida_restaurante"),
    "MAG":  ("Peluquería (Merpago)", "personal_servicios"),
    "CAMC": ("Pan Para Todos", "comida_restaurante"),
    "LEN":  ("Lenigas 3", "comida_restaurante"),
    "BLI":  ("Clip MX*Rest", "comida_restaurante"),
    "OPQ":  ("TCCF Mitikah", "comida_restaurante"),
    "CHU":  ("Rest Palomilla", "comida_restaurante"),
    "HEBC": ("Fonda S", "comida_restaurante"),
    "YTR":  ("Rest Yeccan", "comida_restaurante"),
    "OPG":  ("Clip MX*Rest PI", "comida_restaurante"),
    "PHI":  ("El Palacio Coy", "comida_restaurante"),
    "SHE":  ("Sanborns", "tienda_conveniencia"),
    "RBM":  ("Rest Bar Montejo", "comida_restaurante"),
    "YAAC": ("Tda Yamamoto", "comida_restaurante"),
    "JRL":  ("Johnny Rockets", "comida_rapida"),
    "LOVD": ("Minisuper La Ba", "tienda_conveniencia"),
    "AME":  ("AutoZone", "transporte_otros"),
    "IDF":  ("BP*Traslomita", "comida_restaurante"),
    # Tiendas/Conveniencia
    "CCO":  ("OXXO", "tienda_conveniencia"),
    "SEM":  ("7-Eleven", "tienda_conveniencia"),
    "SEMS": ("La Estación", "tienda_conveniencia"),
    "SEMA": ("Rest Las Tortugas", "comida_restaurante"),
    "BURL": ("PSM*La Tienda D", "tienda_conveniencia"),
    "AURF": ("PSM*Abarrotes", "tienda_conveniencia"),
    "ROBJ": ("Abarrotes San P", "tienda_conveniencia"),
    # Rappi / apps
    "OCS":  ("Rappi / Café Tierra", "comida_rapida"),
    # Entretenimiento
    "BNO":  ("Cinetec", "entretenimiento"),
    "TCI":  ("Cinépolis", "entretenimiento"),
    # Salud
    "AATF": ("Farm Santa Rita", "salud_farmacia"),
    "DISJ": ("Farms Union", "salud_farmacia"),
    "UFD":  ("Union Farmacéutica", "salud_farmacia"),
    # Transporte
    "SJG":  ("Gasolinera Serv J GT", "transporte_gasolina"),
}


PROCESADORES_GENERICOS = {
    "LA ESTACION":     ("La Estación", "tienda_conveniencia"),
    "ABTS MAPACHINOS": ("Abarrotes Mapachinos", "tienda_conveniencia"),
    "RESCACTUS":       ("Rescactus", "comida_restaurante"),
    "STARBUCKS":       ("Starbucks", "comida_restaurante"),
}


# ============================================================
# PATRONES ESPECÍFICOS DE NU
# ============================================================
# Se evalúan ANTES que los patrones Santander para que las descripciones Nu
# (que no tienen "TERMINACION" ni códigos de comercio) capturen aquí primero.
PATRONES_TEXTO_NU = [
    # Movimientos internos de cajitas — SIEMPRE transferencia_cajita
    (re.compile(r"^Depósito en Cajita:", re.I),
        ("Cajita (depósito)", "transferencia_cajita")),
    (re.compile(r"^Retiro de Cajita:", re.I),
        ("Cajita (retiro)", "transferencia_cajita")),
    # Intereses sintéticos (tx virtual del último día del mes)
    (re.compile(r"^INTERESES NU", re.I),
        ("Nu (intereses cajitas)", "ingreso_intereses")),
    # Suscripciones IA (compras USD)
    (re.compile(r"^ANTHROPIC\s+Compra", re.I),
        ("Anthropic API", "subs_ia")),
    (re.compile(r"^CLAUDE\.?AI SUBSCRIPTION", re.I),
        ("Claude.ai", "subs_ia")),
    (re.compile(r"^OPENAI\s*\*?\s*CHATGPT", re.I),
        ("ChatGPT (OpenAI)", "subs_ia")),
    # Suscripciones streaming / contenido
    (re.compile(r"^Google YouTubePremium", re.I),
        ("YouTube Premium", "subs_google")),
    (re.compile(r"^GOOGLE\s+Compra", re.I),
        ("Google Play/otros", "subs_google")),
    (re.compile(r"^Kindle Svcs", re.I),
        ("Kindle (Amazon)", "subs_streaming")),
    (re.compile(r"^Patreon", re.I),
        ("Patreon", "subs_streaming")),
    (re.compile(r"^KSK\*VID ONLYFANS", re.I),
        ("OnlyFans", "entretenimiento")),
    # Uber (Nu usa DLO como gateway)
    (re.compile(r"^DLO\*UBER\s*EATS", re.I),
        ("Uber Eats", "comida_rapida")),
    (re.compile(r"^DLO\*Uber\s*eats", re.I),
        ("Uber Eats", "comida_rapida")),
    (re.compile(r"^DLO\*UBER\s*RIDE", re.I),
        ("Uber Ride", "transporte_uber")),
    (re.compile(r"^DLO\*Uber\b", re.I),
        ("Uber", "transporte_uber")),
    (re.compile(r"^UBER\*\s*TRIP", re.I),
        ("Uber Trip", "transporte_uber")),
    (re.compile(r"^UBER\*\s*PENDING\s+Devolución", re.I),
        ("Uber (devolución)", "transporte_uber")),
    (re.compile(r"^UBER\*\s*PENDING\s+Compra", re.I),
        ("Uber (pending)", "transporte_uber")),
    # Tiendas y supermercados
    (re.compile(r"^OXXO", re.I),
        ("OXXO", "tienda_conveniencia")),
    (re.compile(r"^7\s*ELEVEN", re.I),
        ("7-Eleven", "tienda_conveniencia")),
    (re.compile(r"^SORIANA", re.I),
        ("Soriana", "tienda_conveniencia")),
    # ITAM Nu (compras con "EXTRA K ITAM")
    (re.compile(r"^EXTRA\s*K\s*ITAM", re.I),
        ("Extra K ITAM", "comida_itam")),
    (re.compile(r"^PAYUMEX\*MCGRAW HILL", re.I),
        ("McGraw Hill (libros ITAM)", "itam_otros")),
    # Salud
    (re.compile(r"^FARM SANTA RITA", re.I),
        ("Farm Santa Rita", "salud_farmacia")),
    (re.compile(r"^SANATORIO", re.I),
        ("Sanatorio", "salud_farmacia")),
    # Servicios personales
    (re.compile(r"^MERPAGO\*HAIRSTUDIO", re.I),
        ("Hair Studio (Merpago)", "personal_servicios")),
    # Transporte / movilidad
    (re.compile(r"^MEXPAGO\*MUEVECIUDAD", re.I),
        ("MueveCiudad", "transporte_otros")),
    # Entretenimiento y bares
    (re.compile(r"^CINETEC MU", re.I),
        ("Cineteca", "entretenimiento")),
    (re.compile(r"^NETPAY\s*\*?\s*FORO", re.I),
        ("Foro Versalles", "entretenimiento")),
    (re.compile(r"^BPK\*SEMPRE", re.I),
        ("Sempre Cantina/Botanero", "comida_restaurante")),
    (re.compile(r"^BPK\*", re.I),
        ("BPK (varios)", "comida_restaurante")),
    (re.compile(r"^CLIP MX\*ATRACUNIONVE", re.I),
        ("Atraccion Union", "entretenimiento")),
    (re.compile(r"^CLIP MX\*HOT VOLGA HOTE", re.I),
        ("Hot Volga (hospedaje)", "entretenimiento")),
    (re.compile(r"^CLIP MX\*", re.I),
        ("Clip MX (varios)", "comida_restaurante")),
    (re.compile(r"^EBW\*AGEN RESIDENT", re.I),
        ("Resident Advisor", "entretenimiento")),
    (re.compile(r"^OROPEL BAR", re.I),
        ("Oropel Bar", "comida_restaurante")),
    (re.compile(r"^ZTL\*AGORABARRADECAFE", re.I),
        ("Agora Barra de Café", "comida_restaurante")),
    (re.compile(r"^DARI CHA", re.I),
        ("Dari Cha (té)", "comida_restaurante")),
    (re.compile(r"^LENIGAS", re.I),
        ("Lenigas", "comida_restaurante")),
    (re.compile(r"^EL PALACIOH COYOAC", re.I),
        ("El Palacio Coyoacán", "comida_restaurante")),
    # MercadoPago Nu (formato "MERCADOPAGO *XXX")
    (re.compile(r"^MERCADOPAGO\s*\*PALETERI", re.I),
        ("MercadoPago (paletería)", "comida_restaurante")),
    (re.compile(r"^MERCADOPAGO\s*\*PAPELERI", re.I),
        ("MercadoPago (papelería)", "itam_otros")),
    (re.compile(r"^MERCADOPAGO\s*\*TIENDACH", re.I),
        ("MercadoPago (tienda)", "tienda_conveniencia")),
    (re.compile(r"^MERCADOPAGO\s*\*LITROLOG", re.I),
        ("MercadoPago (Litrolog)", "comida_restaurante")),
    (re.compile(r"^MERCADOPAGO\s*\*ROCKSOLI", re.I),
        ("MercadoPago (Rocksoli)", "comida_restaurante")),
    (re.compile(r"^MERCADOPAGO", re.I),
        ("MercadoPago (otros)", "comida_restaurante")),
    # Otros
    (re.compile(r"^Decathlon", re.I),
        ("Decathlon (deportes)", "personal_servicios")),
    (re.compile(r"^IKANO RETAIL", re.I),
        ("Ikea", "personal_servicios")),
    (re.compile(r"^HAO FERRETERIA", re.I),
        ("Ferretería HAO", "personal_servicios")),
]

# Patrones SPEI con nombres de personas (propios/familia): vienen de la
# config ignorada. Se anteponen porque son los más específicos — un nombre
# completo no colisiona con ningún patrón de comercio.
PATRONES_TEXTO_NU = _CONFIG["patrones_personas"] + PATRONES_TEXTO_NU


# ============================================================
# PATRONES SANTANDER (originales, preservados)
# ============================================================
PATRONES_TEXTO_SANTANDER = [
    # Tarjeta secundaria 6843 = ITAM
    (re.compile(r"TERMINACION 6843", re.I),
        ("ITAM (tarjeta 6843)", "itam_colegiatura")),
    # SPEI / Transferencias por contraparte
    (re.compile(r"ENVIADO A NU MEXICO", re.I),
        ("Transferencia a Nu", "transferencia_nu")),
    (re.compile(r"RECIBIDO DE NU MEXICO", re.I),
        ("Recibido de Nu", "transferencia_nu")),
    (re.compile(r"DEVUELTO POR NU MEXICO", re.I),
        ("Devuelto Nu", "transferencia_nu")),
    (re.compile(r"RECIBIDO DE AZTECA", re.I),
        ("Nómina (Azteca)", "ingreso_nomina")),
    (re.compile(r"RECIBIDO DE HSBC", re.I),
        ("Nómina (HSBC)", "ingreso_nomina")),
    (re.compile(r"RECIBIDO DE BBVA MEXICO", re.I),
        ("Recibido de BBVA (familia)", "ingreso_terceros")),
    (re.compile(r"RECIBIDO DE SCOTIABANK", re.I),
        ("Recibido de Scotiabank", "ingreso_terceros")),
    (re.compile(r"DEVUELTO POR BBVA", re.I),
        ("Devuelto BBVA", "transferencia_otros")),
    (re.compile(r"ENVIADO A BANCOPPEL", re.I),
        ("Enviado a Bancoppel", "transferencia_otros")),
    # ITAM
    (re.compile(r"ITAM MU", re.I),
        ("ITAM Colegiatura", "itam_colegiatura")),
    (re.compile(r"ITAM LIBRERIA", re.I),
        ("ITAM Librería", "itam_otros")),
    # ATM
    (re.compile(r"RETIRO EFEC SIN TARJETA", re.I),
        ("Retiro ATM", "retiro_efectivo")),
    (re.compile(r"DISP ATM PROPIO", re.I),
        ("Retiro ATM propio", "retiro_efectivo")),
    (re.compile(r"DEPOSITO EN EFECTIVO ATM", re.I),
        ("Depósito ATM", "ingreso_terceros")),
    # SPEI rápido
    (re.compile(r"PAGO TRANSF RAPIDA SPEI TRANSFERENCIA A", re.I),
        ("SPEI rápido enviado", "transferencia_otros")),
    # MercadoPago Santander (formato con OCR fallible Tl↔TI)
    (re.compile(r"MERCADOPAGO \*PA", re.I),
        ("MercadoPago (PA)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*T[IL]", re.I),
        ("MercadoPago (TI)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*JA", re.I),
        ("MercadoPago (JA)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*HA", re.I),
        ("MercadoPago (HA)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*EL", re.I),
        ("MercadoPago (EL)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*BR", re.I),
        ("MercadoPago (BR)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO \*FE", re.I),
        ("MercadoPago (FE)", "comida_restaurante")),
    (re.compile(r"MERCADOPAGO", re.I),
        ("MercadoPago (otros)", "comida_restaurante")),
    # Procesadores y comisiones
    (re.compile(r"STARBUCKS", re.I),
        ("Starbucks", "comida_restaurante")),
    (re.compile(r"UBER\*", re.I),
        ("Uber", "transporte_uber")),
    (re.compile(r"COM REPOSICION TARJETA", re.I),
        ("Comisión reposición tarjeta", "comisiones")),
    (re.compile(r"IV A POR COMISION", re.I),
        ("IVA por comisión", "comisiones")),
]


# ============================================================
# UTILIDADES
# ============================================================
PATRON_CUENTA = re.compile(r"(?:A LA CUENTA|DE LA CUENTA|A la cuenta|De la cuenta)\s+(\d+)", re.I)


def _extraer_prefijo(texto_post_pipe):
    """Extrae el prefijo del comercio (las letras iniciales)."""
    match = re.match(r"^([A-Z]+)", texto_post_pipe.strip())
    if match:
        return match.group(1)
    return None


def _buscar_procesador_generico(texto):
    """Busca comercios con código '0000000000000' por palabra clave."""
    for clave, info in PROCESADORES_GENERICOS.items():
        if clave in texto.upper():
            return info
    return None


# ============================================================
# API PÚBLICA
# ============================================================
def categorizar(descripcion: str) -> tuple[str, str]:
    """
    Devuelve (comercio, categoria) para una descripción.
    Si no se reconoce, devuelve ('Sin categoría', 'sin_categoria').

    El orden de evaluación importa:
    - Patrones Nu primero (más específicos, tienden a matchear al inicio de línea).
    - Patrones Santander después (los legacy que usan "ENVIADO A", "TERMINACION", etc.)
    - Cuentas CLABE (aplica a ambos si el número está en el texto).
    - Reglas de código de comercio (Santander only).
    """
    if not descripcion:
        return "Sin categoría", "sin_categoria"

    # 1. Patrones Nu
    for patron, info in PATRONES_TEXTO_NU:
        if patron.search(descripcion):
            return info

    # 2. Patrones Santander
    for patron, info in PATRONES_TEXTO_SANTANDER:
        if patron.search(descripcion):
            return info

    # 3. SPEI por número de cuenta (aplica a ambos bancos)
    match_cuenta = PATRON_CUENTA.search(descripcion)
    if match_cuenta:
        cuenta = match_cuenta.group(1)
        if cuenta in CUENTAS:
            return CUENTAS[cuenta]

    # 4. Procesadores genéricos (Santander)
    resultado = _buscar_procesador_generico(descripcion)
    if resultado is not None:
        return resultado

    # 5. Código de comercio después del "|" (Santander)
    if "|" in descripcion:
        despues_pipe = descripcion.split("|", 1)[1].strip()
        prefijo = _extraer_prefijo(despues_pipe)
        if prefijo and prefijo in COMERCIOS:
            return COMERCIOS[prefijo]

    # 6. Código al inicio (Santander)
    prefijo = _extraer_prefijo(descripcion)
    if prefijo and prefijo in COMERCIOS:
        return COMERCIOS[prefijo]

    # 7. Fallback Santander: CONSUMO LOCAL AJENO → comida_otros
    if "CONSUMO LOCAL AJENO" in descripcion:
        return ("Otro comercio", "comida_otros")

    return "Sin categoría", "sin_categoria"
