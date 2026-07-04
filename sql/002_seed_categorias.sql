-- ============================================================
-- Seed: catálogo inicial de categorías
-- Version: 002
-- Fecha: 2026-06-29
-- ============================================================

INSERT INTO categorias (nombre, tipo, descripcion) VALUES
    -- Ingresos
    ('ingreso_nomina',       'ingreso', 'Sueldo neto de nómina (HSBC / Azteca)'),
    ('ingreso_terceros',     'ingreso', 'Familia, regalos, depósitos en efectivo'),
    
    -- Gastos: comida (la categoría con más fragmentación)
    ('comida_itam',          'gasto', 'Comida dentro del ITAM (cafetería Extra K)'),
    ('comida_restaurante',   'gasto', 'Restaurantes sentados'),
    ('comida_rapida',        'gasto', 'Fast food y delivery (Rappi, McDonalds, BPK)'),
    ('comida_otros',         'gasto', 'Comercios sin código reconocido (asumido comida)'),
    
    -- Gastos: tiendas
    ('tienda_conveniencia',  'gasto', 'OXXO, 7-Eleven, La Estación, abarrotes'),
    
    -- Gastos: ITAM
    ('itam_colegiatura',     'gasto', 'Pago de colegiatura y servicios principales ITAM'),
    ('itam_otros',           'gasto', 'Librería ITAM y otros menores'),
    
    -- Gastos: salud
    ('salud_farmacia',       'gasto', 'Farmacias (Santa Rita, Unión, etc)'),
    
    -- Gastos: entretenimiento
    ('entretenimiento',      'gasto', 'Cines, eventos'),
    
    -- Gastos: transporte
    ('transporte_gasolina',  'gasto', 'Gasolinerías'),
    ('transporte_uber',      'gasto', 'Uber y similares'),
    ('transporte_otros',     'gasto', 'Refacciones, taxis, otros'),
    
    -- Gastos: personal
    ('personal_servicios',   'gasto', 'Peluquería, servicios personales'),
    
    -- Comisiones bancarias
    ('comisiones',           'gasto', 'Comisiones del banco'),
    
    -- Retiros (no es gasto en sí pero consume cuenta)
    ('retiro_efectivo',      'gasto', 'ATM — el destino real del efectivo es desconocido'),
    
    -- Transferencias entre cuentas propias (NO son gasto real)
    ('transferencia_nu',     'transferencia', 'Movimiento entre Santander y Nu'),
    ('transferencia_otros',  'transferencia', 'SPEI a otras cuentas propias o de terceros'),
    
    -- Sin clasificar
    ('sin_categoria',        'gasto',         'Transacciones que el categorizador no resolvió');