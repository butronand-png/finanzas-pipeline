import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ocr import cargar_cache
from src.extractor import (
    agrupar_por_fila,
    clasificar_filas,
    parsear_transacciones,
)
from src.validador import validar_saldos

CACHE_DIR = Path("data/ocr_cache")
caches = sorted(CACHE_DIR.glob("*.json"))

print(f"Procesando {len(caches)} PDFs cacheados\n")
print(f"{'archivo':<45} {'total':>6} {'valid':>6} {'inval':>6} {'n/a':>5}")
print("-" * 75)

totales = {"total": 0, "validas": 0, "invalidas": 0, "no_validables": 0}

for cache in caches:
    anots = cargar_cache(cache)
    filas = agrupar_por_fila(anots)
    clasificadas = clasificar_filas(filas)
    transacciones = parsear_transacciones(clasificadas)
    _, resumen = validar_saldos(transacciones)
    
    nombre = cache.stem.replace("Estado_de_cuenta_", "")
    print(f"{nombre:<45} {resumen['total']:>6} {resumen['validas']:>6} "
          f"{resumen['invalidas']:>6} {resumen['no_validables']:>5}")
    
    for k in totales:
        totales[k] += resumen[k]

print("-" * 75)
print(f"{'TOTAL':<45} {totales['total']:>6} {totales['validas']:>6} "
      f"{totales['invalidas']:>6} {totales['no_validables']:>5}")

pct_validas = 100 * totales['validas'] / totales['total']
print(f"\nValidación: {pct_validas:.1f}% de las transacciones cuadran.")