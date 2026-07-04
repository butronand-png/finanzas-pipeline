import fitz
from pathlib import Path
from ocrmac import ocrmac

def _renderizar_pagina(pdf, indice_pagina, dpi_multiplier=2):
    """Convierte una página del PDF a imagen PNG temporal."""
    pagina = pdf[indice_pagina]
    mat = fitz.Matrix(dpi_multiplier, dpi_multiplier)
    pix = pagina.get_pixmap(matrix=mat)
    return pix

def pdf_a_anotaciones(pdf_path, idiomas=None):
    """
    Convierte un PDF a una lista de anotaciones de OCR.
    
    Cada anotación es un dict con: texto, confidence, x, y, w, h, pagina.
    Las coordenadas son normalizadas (0-1).
    """
    if idiomas is None:
        idiomas = ["es-ES", "en-US"]
    
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)
    anotaciones = []
    
    for idx_pagina in range(len(doc)):
        pix = _renderizar_pagina(doc, idx_pagina)
        
        # Guardar temporal para que ocrmac lo lea
        temp_img = Path("data") / f"_temp_pagina_{idx_pagina}.png"
        pix.save(temp_img)
        
        resultado = ocrmac.OCR(
            str(temp_img),
            language_preference=idiomas,
            recognition_level="accurate"
        ).recognize()
        
        for texto, conf, bbox in resultado:
            anotaciones.append({
                "texto": texto,
                "confidence": conf,
                "x": bbox[0],
                "y": bbox[1],
                "w": bbox[2],
                "h": bbox[3],
                "pagina": idx_pagina,
            })
        
        # Limpiar archivo temporal
        temp_img.unlink()
    
    doc.close()
    return anotaciones

import json


def cachear_pdf(pdf_path, cache_dir="data/ocr_cache", forzar=False):
    """
    Corre OCR sobre el PDF y guarda el resultado como JSON.
    Si ya existe el cache, no re-corre OCR (a menos que forzar=True).
    
    Retorna: path al JSON guardado.
    """
    pdf_path = Path(pdf_path)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    cache_file = cache_dir / f"{pdf_path.stem}.json"
    
    if cache_file.exists() and not forzar:
        print(f"  Cache existe: {cache_file.name} (skip)")
        return cache_file
    
    print(f"  Procesando OCR: {pdf_path.name}...")
    anotaciones = pdf_a_anotaciones(pdf_path)
    
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(anotaciones, f, ensure_ascii=False, indent=2)
    
    print(f"  Guardado: {cache_file.name} ({len(anotaciones)} anotaciones)")
    return cache_file

def cargar_cache(cache_file):
    """Carga las anotaciones desde un JSON cacheado."""
    with open(cache_file, encoding="utf-8") as f:
        return json.load(f)