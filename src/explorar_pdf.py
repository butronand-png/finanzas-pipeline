import fitz  # pymupdf
from pathlib import Path
from ocrmac import ocrmac

PDF_PATH = Path("data/pdfs/Estado_de_cuenta_abril_2026.pdf")

# Abrir PDF y convertir página 2 a imagen
doc = fitz.open(PDF_PATH)
pagina = doc[1]  # índice 0 = página 1, índice 1 = página 2

# Renderizar a imagen con resolución alta (2x = 144 dpi)
mat = fitz.Matrix(2, 2)
pix = pagina.get_pixmap(matrix=mat)

# Guardar imagen temporal
img_path = Path("data/pagina_2_temp.png")
pix.save(img_path)
print(f"Imagen guardada: {img_path} ({pix.width}x{pix.height}px)")

# OCR sobre la imagen
resultado = ocrmac.OCR(
    str(img_path),
    language_preference=["es-ES", "en-US"],
    recognition_level="accurate"
).recognize()

print(f"\nTotal de anotaciones: {len(resultado)}\n")
for item in resultado:
    print(item)