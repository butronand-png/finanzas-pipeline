# finanzas-pipeline — bank statement extraction & reconciliation

A personal-finance data pipeline that turns raw bank statement PDFs from two
Mexican banks into a validated, normalized PostgreSQL database with
cross-bank reconciliation — built as an exercise in applied data
engineering: OCR, parser design, accounting invariants, and schema design.

**No financial data lives in this repo.** All personal identifiers (account
numbers, counterparty names) load at runtime from a gitignored config file;
a fictitious `data/cuentas_conocidas.example.json` documents the format.

## What it does

```
PDFs (Santander, scanned)──► OCR (Apple Vision) ──► coordinate-anchored parser ──┐
                                                                                 ├─► rule-based categorizer ──► PostgreSQL
PDFs (Nu, vector text) ─────► pdfplumber ─────────► line state-machine parser ───┘         (normalized schema + views)
```

1. **Two extractors, two strategies.**
   - *Santander*: statements are scanned images. Pages go through Apple
     Vision OCR (`ocrmac`), then a coordinate-based parser groups words into
     rows and classifies deposit/withdrawal/balance columns by x-position.
   - *Nu*: statements are vector PDFs with selectable text. A line-oriented
     state machine parses transactions, multi-line SPEI detail blocks
     (counterparty bank, CLABE, tracking key), USD purchase details, and the
     savings-pockets section — handling **three date-layout variants**, two
     of them undocumented and discovered empirically (wrapped titles and
     "sandwiched" date cells split across lines).

2. **Validation as a first-class citizen.**
   - Santander: every balance is validated against the running chain
     `balance[i] = balance[i-1] + deposit - withdrawal` (**96.5%** of ~1,200
     OCR'd transactions pass at ±$0.01).
   - Nu: each statement's page-1 accounting identity
     (`final = initial + deposits − expenses + interest − fees`) validates
     **29/29 statements to the cent**, and extracted movement sums are
     cross-checked against the statement header — including one month where
     the *bank's own header* is internally inconsistent (detected as a
     mirrored diff, confirmed with two independent implementations).
   - Savings-pockets flows satisfy `Δbalance = net flow + interest` to the
     cent across all 28 comparable months.

3. **Cross-bank SPEI reconciliation.** Transfers between the two banks are
   matched by SPEI tracking key (Nu's parsed field vs. keys extracted from
   Santander's OCR text). **100% of own-account transfers within the
   covered period reconcile exactly**; the remainder are provably
   structural (third-party counterparties or months without a counterpart
   statement), with a Levenshtein-based diagnostic to rule out OCR errors.

4. **Consolidated view without double counting.** A PostgreSQL view unifies
   both banks under one sign convention, excludes internal pocket
   reallocations, and collapses each reconciled transfer to a single row
   flagged `es_transferencia_interna` — so income/expense queries never
   count the same peso twice. Loads are idempotent (`ON CONFLICT` on a
   deterministic per-PDF ordering key that survives legitimate duplicate
   transactions).

5. **Rule-based categorization** (5 layers: text patterns → known accounts →
   generic processors → merchant-code prefixes → fallback), **98.9%
   coverage** on the Santander corpus. No ML — rules are auditable and the
   corpus is small.

6. **Empirical analysis scripts** (`tests/analisis_*.py`): monthly flow
   distributions split by spending regime, recurring-expense detection via
   coefficient-of-variation on inter-purchase gaps and amounts (with
   active/ended recurrence tracking), and savings-drain runway estimation.
   Every methodological choice — thresholds, exclusions, their arbitrariness
   — is documented in the docstrings.

## Stack

Python 3.13 · [uv](https://docs.astral.sh/uv/) · pandas · pdfplumber ·
PyMuPDF + ocrmac (Apple Vision, macOS) · pandera · psycopg 3 ·
PostgreSQL 17 (Docker)

## Repository layout

```
src/
├── extractors/santander.py  # OCR + coordinate parser
├── extractors/nu.py         # unified-schema Nu extractor
├── extractor_nu.py          # standalone Nu extractor (SPEI metadata, pockets)
├── categorizador.py         # 5-layer rule categorizer
├── config_cuentas.py        # loads personal accounts from gitignored config
├── validador.py             # balance-chain validation
├── schema.py                # pandera contract shared by extractors
└── db.py                    # PostgreSQL persistence (.env credentials)
sql/                         # migrations 001-004 (schema, seed, Nu, views)
tests/                       # validation suites, diagnostics, analyses
data/                        # your PDFs & config — gitignored except the example
```

## Running it with your own data

```bash
# 1. Dependencies (Python 3.13, uv) — OCR requires macOS (Apple Vision)
uv sync

# 2. Credentials
cp .env.example .env          # set POSTGRES_PASSWORD / DB_PASSWORD

# 3. Database
docker compose up -d
for f in sql/00*.sql; do
  docker exec -i finanzas-pg psql -U finanzas -d finanzas < "$f"
done

# 4. Personal accounts config (CLABEs, counterparty names)
cp data/cuentas_conocidas.example.json data/cuentas_conocidas.json
# ...edit with your real accounts (file is gitignored)

# 5. Statements
#    data/pdfs/     ← Santander PDFs
#    data/pdfs_nu/  ← Nu PDFs ("Month Year.pdf")

# 6. Extract, validate, load
uv run python -m src.main                        # both banks → parquet/CSV
uv run python tests/probar_extractor_nu.py       # Nu validation report
uv run python tests/cargar_transacciones_nu.py   # load Nu + reconcile (idempotent)
uv run python tests/sanidad_flujo_consolidado.py # view vs. manual recomputation
```

## Design notes

- **Trust the document, not the spec.** Both banks' real PDFs diverged from
  their documented formats; every parser decision was verified against the
  full corpus, and layout surprises are recorded in comments where they were
  found.
- **Separate tables per bank shape.** Nu's model (single signed amount,
  sections, internal-movement flag, parsed SPEI metadata) differs enough
  from Santander's (deposit/withdrawal/balance columns) that forcing one
  table lost information; a view provides the unified surface instead.
- **Privacy by design.** The repo separates *mechanism* (tracked code) from
  *personal data* (gitignored config + local files), so the pipeline is
  publishable and reusable without leaking a single account number.

Code comments and docstrings are in Spanish.
