import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.ocr import cargar_cache

cache = "data/ocr_cache/Estado_de_cuenta_mayo_2026_(1).json"
anots = cargar_cache(cache)

# Filtrar solo página 1 (donde están las primeras transacciones rotas)
pag = [a for a in anots if a["pagina"] == 1]

# Ordenar por y descendente
pag.sort(key=lambda a: -a["y"])

print(f"Página 1 — {len(pag)} anotaciones\n")
print(f"{'y':>6} {'x':>6} {'conf':>5}  texto")
print("-" * 90)
for a in pag:
    print(f"{a['y']:.3f} {a['x']:.3f} {a['confidence']:.2f}  {a['texto'][:70]}")