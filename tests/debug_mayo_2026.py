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

cache = "data/ocr_cache/Estado_de_cuenta_noviembre_2025.json"
anots = cargar_cache(cache)

filas = agrupar_por_fila(anots)
clasificadas = clasificar_filas(filas)
transacciones = parsear_transacciones(clasificadas)
transacciones, resumen = validar_saldos(transacciones)

print(f"Total: {resumen['total']}, válidas: {resumen['validas']}, "
      f"inválidas: {resumen['invalidas']}, n/a: {resumen['no_validables']}\n")

# Las INVÁLIDAS son las interesantes esta vez (la cuenta no cuadra)
print("=== Transacciones INVÁLIDAS ===\n")
for i, t in enumerate(transacciones):
    if t["valido"] is False:
        # Mostrar también la transacción anterior para contexto
        prev = transacciones[i - 1]
        print(f"  ÍNDICE {i}")
        print(f"  Anterior: fecha={prev['fecha']} saldo={prev['saldo']:.2f}")
        print(f"  Actual:   fecha={t['fecha']} folio={t['folio']} pag={t['pagina']}")
        print(f"            dep={t['deposito']:.2f} ret={t['retiro']:.2f} saldo={t['saldo']:.2f}")
        esperado = prev["saldo"] + t["deposito"] - t["retiro"]
        print(f"            saldo esperado: {esperado:.2f}, error: {t['error_saldo']:+.2f}")
        print(f"  desc: {t['descripcion'][:100]}")
        print()