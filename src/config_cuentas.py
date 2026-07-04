"""
src/config_cuentas.py — Carga de datos personales de cuentas desde config.

Los datos que identifican personas (CLABEs con etiqueta, patrones regex con
nombres propios, la CLABE propia para reconciliación) NO viven en el código:
se cargan en runtime desde `data/cuentas_conocidas.json`, que está ignorado
por git. El repo trackea solo `data/cuentas_conocidas.example.json` con
valores ficticios que documentan el formato.

Resolución (primera que exista):
  1. data/cuentas_conocidas.json          — datos reales (ignorado por git)
  2. data/cuentas_conocidas.example.json  — ficticio (permite correr el
     pipeline recién clonado, aunque la categorización por CLABE/persona
     no matcheará nada real)

Formato del JSON:
{
  "clabe_propia_santander": "018 dígitos",
  "cuentas": { "<clabe o nº cuenta>": ["etiqueta", "categoria"], ... },
  "patrones_personas": [ ["<regex>", "etiqueta", "categoria"], ... ]
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
_RUTAS = (
    _RAIZ / "data" / "cuentas_conocidas.json",
    _RAIZ / "data" / "cuentas_conocidas.example.json",
)


def cargar_config_cuentas() -> dict:
    """
    Devuelve el dict de configuración con los patrones ya compilados:
    {"clabe_propia_santander": str,
     "cuentas": {clabe: (etiqueta, categoria)},
     "patrones_personas": [(re.Pattern, (etiqueta, categoria)), ...]}
    """
    for ruta in _RUTAS:
        if ruta.exists():
            crudo = json.loads(ruta.read_text(encoding="utf-8"))
            return {
                "origen": ruta.name,
                "clabe_propia_santander": crudo.get("clabe_propia_santander", ""),
                "cuentas": {clabe: tuple(info)
                            for clabe, info in crudo.get("cuentas", {}).items()},
                "patrones_personas": [
                    (re.compile(patron, re.I), (etiqueta, categoria))
                    for patron, etiqueta, categoria
                    in crudo.get("patrones_personas", [])],
            }
    raise FileNotFoundError(
        f"No existe {_RUTAS[0]} ni {_RUTAS[1]}. Copia el .example.json y "
        "ajústalo con tus cuentas (queda ignorado por git).")
