-- ============================================================================
-- Migración 003: soporte para banco Nu
-- ============================================================================
-- Cambios:
--   1. Columna `banco` en transacciones (santander | nu)
--   2. Columnas de moneda extranjera (para compras en USD como Anthropic/OpenAI)
--   3. Constraint UNIQUE actualizado (incluir banco para permitir duplicados
--      legítimos entre bancos)
--   4. Nuevas categorías: transferencia_cajita, ingreso_intereses, subs_ia, subs_streaming
--
-- Idempotente: cada bloque valida existencia antes de aplicar.
-- Uso: docker exec -i finanzas-pg psql -U finanzas -d finanzas < sql/003_migracion_nu.sql
-- ============================================================================

BEGIN;

-- ---------- 1. Columna `banco` ---------------------------------------------
-- Default 'santander' porque toda la data existente viene de ahí. Después de
-- esta migración, cada insert nuevo debe especificar banco explícito.
ALTER TABLE transacciones
    ADD COLUMN IF NOT EXISTS banco VARCHAR(20) NOT NULL DEFAULT 'santander'
    CHECK (banco IN ('santander', 'nu'));

CREATE INDEX IF NOT EXISTS idx_transacciones_banco ON transacciones(banco);

-- ---------- 2. Columnas de moneda extranjera -------------------------------
-- Todas nullable: solo se llenan cuando la compra fue en USD (u otra moneda).
-- `monto` sigue siendo en MXN (el cargo efectivo). `monto_original` es en la
-- moneda original (por ejemplo USD 10.00) y tipo_cambio permite reconstruir.
ALTER TABLE transacciones
    ADD COLUMN IF NOT EXISTS moneda_original CHAR(3);

ALTER TABLE transacciones
    ADD COLUMN IF NOT EXISTS monto_original NUMERIC(12, 2);

ALTER TABLE transacciones
    ADD COLUMN IF NOT EXISTS tipo_cambio NUMERIC(10, 4);

-- Constraint: si hay moneda_original, deben existir los otros dos campos
ALTER TABLE transacciones
    DROP CONSTRAINT IF EXISTS chk_moneda_completa;
ALTER TABLE transacciones
    ADD CONSTRAINT chk_moneda_completa
    CHECK (
        (moneda_original IS NULL AND monto_original IS NULL AND tipo_cambio IS NULL)
        OR
        (moneda_original IS NOT NULL AND monto_original IS NOT NULL AND tipo_cambio IS NOT NULL)
    );

-- ---------- 3. UNIQUE constraint con banco ---------------------------------
-- Sin banco, un depósito de $500 el mismo día con mismo folio en Santander y
-- en Nu chocaría. Con banco incluido, cada banco tiene su propio espacio.
-- Postgres requiere DROP + ADD porque no hay ALTER CONSTRAINT.
ALTER TABLE transacciones
    DROP CONSTRAINT IF EXISTS transacciones_fecha_folio_deposito_retiro_saldo_key;

ALTER TABLE transacciones
    ADD CONSTRAINT uq_transacciones_banco_fecha_folio_monto
    UNIQUE (banco, fecha, folio, deposito, retiro, saldo);

-- ---------- 4. Nuevas categorías -------------------------------------------
INSERT INTO categorias (nombre, tipo, descripcion) VALUES
    ('transferencia_cajita', 'transferencia',
     'Movimiento entre cuenta principal Nu y cajita del mismo usuario (interno)'),
    ('ingreso_intereses', 'ingreso',
     'Rendimientos generados por cajitas Nu (GAT). Se sintetiza como tx virtual mensual.'),
    ('subs_ia', 'gasto',
     'Suscripciones a IA generativa: Anthropic, Claude.ai, OpenAI/ChatGPT'),
    ('subs_streaming', 'gasto',
     'Suscripciones de streaming: Netflix, Spotify, Disney+, HBO, YouTube Premium'),
    ('subs_google', 'gasto',
     'Servicios de Google recurrentes (Google One, YouTube Premium via Google)')
ON CONFLICT (nombre) DO NOTHING;

-- ---------- 5. Actualizar vista transacciones_completas --------------------
-- Regenerar la vista para que exponga los nuevos campos.
DROP VIEW IF EXISTS transacciones_completas;
CREATE VIEW transacciones_completas AS
SELECT
    t.id,
    t.banco,
    t.fecha,
    t.folio,
    t.descripcion_raw,
    t.deposito,
    t.retiro,
    t.saldo,
    t.moneda_original,
    t.monto_original,
    t.tipo_cambio,
    c.nombre        AS comercio,
    cat.nombre      AS categoria,
    cat.tipo        AS tipo_categoria,
    t.pdf_origen,
    t.pagina,
    t.valido,
    t.error_saldo,
    t.cargado_en
FROM transacciones t
LEFT JOIN comercios  c   ON c.id = t.comercio_id
LEFT JOIN categorias cat ON cat.id = c.categoria_id;

COMMIT;

-- ============================================================================
-- Verificación post-migración
-- ============================================================================
-- Correr después de aplicar para confirmar:
--   SELECT column_name, data_type, is_nullable
--   FROM information_schema.columns
--   WHERE table_name = 'transacciones'
--   ORDER BY ordinal_position;
--
--   SELECT nombre, tipo FROM categorias WHERE nombre IN
--     ('transferencia_cajita', 'ingreso_intereses', 'subs_ia', 'subs_streaming', 'subs_google');
