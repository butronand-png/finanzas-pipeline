-- ============================================================
-- Schema de finanzas personales
-- Version: 001
-- Fecha: 2026-06-29
-- ============================================================

-- Tipos enumerados para integridad de dominio
CREATE TYPE tipo_categoria AS ENUM ('ingreso', 'gasto', 'transferencia');

-- ============================================================
-- TABLA: categorias
-- Catálogo. Una fila por categoría conceptual.
-- ============================================================
CREATE TABLE categorias (
    id          SERIAL PRIMARY KEY,
    nombre      VARCHAR(50) NOT NULL UNIQUE,
    tipo        tipo_categoria NOT NULL,
    descripcion TEXT,
    creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMENT ON TABLE categorias IS 'Catálogo de categorías de transacciones';
COMMENT ON COLUMN categorias.tipo IS 'ingreso/gasto/transferencia — usado para análisis de flujo';

-- ============================================================
-- TABLA: comercios
-- Una fila por comercio normalizado. Múltiples descripciones
-- crudas del PDF pueden mapear al mismo comercio.
-- ============================================================
CREATE TABLE comercios (
    id           SERIAL PRIMARY KEY,
    nombre       VARCHAR(100) NOT NULL UNIQUE,
    categoria_id INTEGER NOT NULL REFERENCES categorias(id) ON DELETE RESTRICT,
    creado_en    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_comercios_categoria ON comercios(categoria_id);

COMMENT ON TABLE comercios IS 'Comercios normalizados con su categoría';

-- ============================================================
-- TABLA: transacciones
-- Una fila por transacción del estado de cuenta.
-- ============================================================
CREATE TABLE transacciones (
    id              SERIAL PRIMARY KEY,
    fecha           DATE NOT NULL,
    folio           VARCHAR(20) NOT NULL,
    descripcion_raw TEXT NOT NULL,
    deposito        NUMERIC(12, 2) NOT NULL DEFAULT 0,
    retiro          NUMERIC(12, 2) NOT NULL DEFAULT 0,
    saldo           NUMERIC(12, 2),
    comercio_id     INTEGER REFERENCES comercios(id) ON DELETE SET NULL,
    
    -- Metadata de extracción/validación (auditoría)
    pdf_origen      VARCHAR(100) NOT NULL,
    pagina          INTEGER,
    confidence_min  NUMERIC(4, 3),
    valido          BOOLEAN,
    error_saldo     NUMERIC(12, 4),
    
    cargado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    
    -- Constraint para evitar duplicados al reprocesar PDFs
    CONSTRAINT uq_transaccion UNIQUE (fecha, folio, deposito, retiro, saldo),
    
    -- Una transacción tiene depósito O retiro, no ambos con valor
    CONSTRAINT chk_dep_o_ret CHECK (
        (deposito > 0 AND retiro = 0) OR
        (retiro > 0 AND deposito = 0) OR
        (deposito = 0 AND retiro = 0)
    )
);

CREATE INDEX idx_transacciones_fecha ON transacciones(fecha);
CREATE INDEX idx_transacciones_comercio ON transacciones(comercio_id);
CREATE INDEX idx_transacciones_pdf ON transacciones(pdf_origen);

COMMENT ON TABLE transacciones IS 'Transacciones extraídas de PDFs de Santander';
COMMENT ON COLUMN transacciones.descripcion_raw IS 'Texto completo del PDF para auditoría';
COMMENT ON COLUMN transacciones.error_saldo IS 'Delta entre saldo esperado y observado';

-- ============================================================
-- VISTA: transacciones_completas
-- Join de transacciones con comercio y categoría para queries cómodas.
-- ============================================================
CREATE VIEW transacciones_completas AS
SELECT
    t.id,
    t.fecha,
    t.folio,
    t.descripcion_raw,
    t.deposito,
    t.retiro,
    t.saldo,
    c.nombre AS comercio,
    cat.nombre AS categoria,
    cat.tipo AS tipo_categoria,
    t.pdf_origen,
    t.valido,
    t.error_saldo
FROM transacciones t
LEFT JOIN comercios c ON t.comercio_id = c.id
LEFT JOIN categorias cat ON c.categoria_id = cat.id;

COMMENT ON VIEW transacciones_completas IS 'Vista plana para análisis sin joins manuales';