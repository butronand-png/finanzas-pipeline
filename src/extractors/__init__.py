"""
Extractores por banco. Cada módulo expone `extraer_pdf(path) -> pd.DataFrame`
que produce un DataFrame cumpliendo `src.schema.TransaccionSchema`.
"""
from src.extractors import nu, santander

__all__ = ["nu", "santander"]
