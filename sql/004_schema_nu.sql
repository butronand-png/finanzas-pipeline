-- ============================================================================
-- Migración 004: schema dedicado para Nu + reconciliación + flujo consolidado
-- ============================================================================
-- Contexto: la migración 003 preparó `transacciones` para un modelo unificado
-- (columna banco), pero las filas de Nu nunca se cargaron ahí. Este schema
-- toma el camino de TABLA SEPARADA para Nu porque su modelo difiere del de
-- Santander: monto único con signo (no deposito/retiro), secciones
-- cuenta/cajitas, bandera de movimiento interno y metadata SPEI parseada
-- (banco/CLABE/clave de rastreo) que Santander solo tiene como texto OCR.
--
-- Objetos:
--   1. transacciones_nu            — salida de src/extractor_nu.py
--   2. transferencias_reconciliadas — pares Santander<->Nu por clave de rastreo
--   3. flujo_consolidado (vista)   — ambos bancos, sin dobles conteos
--
-- NO toca las tablas existentes de Santander (transacciones, comercios,
-- categorias). Idempotente: IF NOT EXISTS / OR REPLACE en todos los objetos.
-- Uso: docker exec -i finanzas-pg psql -U finanzas -d finanzas < sql/004_schema_nu.sql
--
-- ⚠️ CAMINO VIGENTE PARA NU: esta tabla separada (004) es la fuente de
-- verdad de Nu en BD. La columna `banco` que la migración 003 agregó a
-- `transacciones` queda SOLO para Santander — nunca se cargaron filas de
-- Nu ahí y no deben cargarse, para no duplicar contra transacciones_nu.
-- El análisis multi-banco se hace con la vista flujo_consolidado.
-- ============================================================================

BEGIN;

-- ---------- 1. transacciones_nu ---------------------------------------------
-- `orden_en_pdf` es la posición de la transacción en el orden de extracción
-- de su PDF. Existe porque el par (pdf, fecha, descripcion, monto) NO es
-- único en los datos reales (ej. dos compras idénticas del mismo monto en
-- el mismo comercio el mismo día): sin un discriminador, un UNIQUE colapsaría
-- duplicados legítimos. El orden de extracción es determinista para un
-- mismo PDF y versión del extractor, así que UNIQUE(pdf_origen,
-- orden_en_pdf) previene duplicados al reprocesar sin perder filas reales.
CREATE TABLE IF NOT EXISTS transacciones_nu (
    id                    SERIAL PRIMARY KEY,
    fecha                 DATE NOT NULL,
    descripcion           TEXT NOT NULL,
    monto                 NUMERIC(12, 2) NOT NULL,  -- con signo: + entra, - sale
    seccion               VARCHAR(10) NOT NULL
                          CHECK (seccion IN ('cuenta', 'cajitas')),
    es_movimiento_interno BOOLEAN NOT NULL DEFAULT FALSE,
    detalle_spei          TEXT,
    banco_contraparte     VARCHAR(50),
    clabe_contraparte     VARCHAR(18),
    clave_rastreo         VARCHAR(40),
    monto_usd             NUMERIC(12, 2),   -- compras en USD (extra al spec:
    tipo_cambio           NUMERIC(10, 4),   -- el extractor ya los captura)
    pdf_origen            VARCHAR(100) NOT NULL,
    pagina                INTEGER,
    orden_en_pdf          INTEGER NOT NULL,
    cargado_en            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_transacciones_nu_pdf_orden UNIQUE (pdf_origen, orden_en_pdf)
);

CREATE INDEX IF NOT EXISTS idx_transacciones_nu_fecha
    ON transacciones_nu (fecha);
-- Parcial: solo ~10% de los movimientos son SPEI con clave
CREATE INDEX IF NOT EXISTS idx_transacciones_nu_rastreo
    ON transacciones_nu (clave_rastreo) WHERE clave_rastreo IS NOT NULL;

COMMENT ON TABLE transacciones_nu IS
    'Movimientos de estados de cuenta Nu (src/extractor_nu.py). Incluye ambas secciones: cuenta y cajitas.';
COMMENT ON COLUMN transacciones_nu.es_movimiento_interno IS
    'TRUE = reallocación cuenta<->cajita (aparece en ambas secciones con signo invertido). NO es ingreso ni gasto real.';
COMMENT ON COLUMN transacciones_nu.orden_en_pdf IS
    'Posición en el orden de extracción del PDF. Discriminador del UNIQUE de idempotencia.';

-- ---------- 2. transferencias_reconciliadas ---------------------------------
-- Materializa los pares Santander<->Nu emparejados por clave de rastreo
-- (diagnóstico: tests/diagnostico_reconciliacion_nu.py). Cada lado puede
-- participar en UN solo par (UNIQUE en ambas FKs) — la reconciliación es 1:1.
-- `monto` es el valor absoluto de la transferencia.
CREATE TABLE IF NOT EXISTS transferencias_reconciliadas (
    id            SERIAL PRIMARY KEY,
    santander_id  INTEGER NOT NULL REFERENCES transacciones(id)
                  ON DELETE CASCADE,
    nu_id         INTEGER NOT NULL REFERENCES transacciones_nu(id)
                  ON DELETE CASCADE,
    clave_rastreo VARCHAR(40) NOT NULL,
    monto         NUMERIC(12, 2) NOT NULL,
    cargado_en    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_reconciliada_santander UNIQUE (santander_id),
    CONSTRAINT uq_reconciliada_nu UNIQUE (nu_id)
);

COMMENT ON TABLE transferencias_reconciliadas IS
    'Pares de transferencias Santander<->Nu emparejadas por clave de rastreo SPEI. La misma operación vista desde ambos bancos.';

-- ---------- 3. Vista flujo_consolidado --------------------------------------
-- Une ambos bancos en un solo flujo con convención de signo única
-- (monto = deposito - retiro en Santander). Reglas anti doble conteo:
--
--   a) Nu: solo sección 'cuenta' y sin movimientos internos de cajitas
--      (la sección cajitas es la misma operación con signo invertido).
--   b) Transferencias reconciliadas: la operación aparece UNA sola vez —
--      se conserva el lado Santander marcado `es_transferencia_interna=TRUE`
--      y se excluye el lado Nu. Para ingreso/gasto real filtrar
--      `WHERE NOT es_transferencia_interna`: una transferencia entre
--      cuentas propias no es gasto ni ingreso, y excluir solo un lado
--      distorsionaría el neto.
CREATE OR REPLACE VIEW flujo_consolidado AS
SELECT
    t.banco,
    t.id                    AS transaccion_id,
    t.fecha,
    t.descripcion_raw       AS descripcion,
    (t.deposito - t.retiro) AS monto,
    (tr.santander_id IS NOT NULL) AS es_transferencia_interna
FROM transacciones t
LEFT JOIN transferencias_reconciliadas tr ON tr.santander_id = t.id
WHERE t.banco = 'santander'

UNION ALL

SELECT
    'nu'          AS banco,
    n.id          AS transaccion_id,
    n.fecha,
    n.descripcion,
    n.monto,
    FALSE         AS es_transferencia_interna
FROM transacciones_nu n
WHERE n.seccion = 'cuenta'
  AND NOT n.es_movimiento_interno
  AND NOT EXISTS (SELECT 1 FROM transferencias_reconciliadas tr
                  WHERE tr.nu_id = n.id);

COMMENT ON VIEW flujo_consolidado IS
    'Flujo de ambos bancos sin dobles conteos. Ingreso/gasto real: WHERE NOT es_transferencia_interna.';

COMMIT;
