import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from src.categorizador import categorizar

df = pd.read_parquet("data/output/transacciones.parquet")

# Aplicar categorizador igual que en el script de prueba
df[["comercio", "categoria"]] = df["descripcion"].apply(
    lambda d: pd.Series(categorizar(d))
)

# Replicar exactamente la extracción del "codigo" del script de prueba
sin_cat_df = df[df["categoria"] == "sin_categoria"].copy()

def extraer_codigo_corto(desc):
    if "|" in desc:
        return desc.split("|", 1)[1].strip()[:50]
    return desc[:50]

sin_cat_df["codigo"] = sin_cat_df["descripcion"].apply(extraer_codigo_corto)

# Buscar exactamente el codigo que aparece en el top: 'CIUDAD DE MEX'
match = sin_cat_df[sin_cat_df["codigo"] == "CIUDAD DE MEX"]
print(f"Transacciones con codigo exacto 'CIUDAD DE MEX': {len(match)}\n")

for _, row in match.iterrows():
    print(f"fecha={row['fecha'].date()}  folio={row['folio']}  pdf={row['pdf_origen']}")
    print(f"  dep={row['deposito']}  ret={row['retiro']}  saldo={row['saldo']}")
    print(f"  pagina={row['pagina']}")
    print(f"  DESCRIPCIÓN COMPLETA:")
    print(f"  {repr(row['descripcion'])}")
    print()