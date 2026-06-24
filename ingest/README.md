# Ingest

Offline pipeline: lender matrix PDFs → **MySQL** (structured eligibility) and guideline PDFs → **Qdrant** (RAG). Not imported by the runtime API.

## Layout

```
ingest/
├── schema.sql              # Initial DB schema
├── migrations/             # Incremental SQL (001–014)
├── lenders/                # Per-lender re-ingest (production path)
│   ├── *_flow_to_mysql_qdrant.py      # Track A: matrix → MySQL (+ matrix vectors)
│   └── *_guidelines_qdrant.py         # Track B: guidelines → Qdrant (TOC chunks)
└── tools/                  # One-off maintenance (not part of normal re-ingest)
    ├── sync_ltv_matrix_loan_amt_min.py
    ├── sync_dim_programs_second_lien_details.py
    ├── load_products_fthb.py          # migration 004 helper
    └── cleanup_programs.sql             # manual program whitelist cleanup
```

Source PDFs live in repo-root `input/<Investor>/Matrices/` and `input/<Investor>/Guidelines/`.

## Database setup

```bash
mysql -u root -p newpoint_mortgage < ingest/schema.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/009_intake_tables.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/010_form_history_scenario.sql
mysql -u root -p newpoint_mortgage < ingest/migrations/011_form_history_client_broker.sql
# Apply 001–014 as needed for your environment (see migrations/ comments)
```

## Re-ingest all lenders

From repo root with venv active:

```bash
# MySQL matrix + structured data
python ingest/lenders/denali_flow_to_mysql_qdrant.py --apply
python ingest/lenders/everest_deephaven_flow_to_mysql_qdrant.py --apply
python ingest/lenders/summit_verus_flow_to_mysql_qdrant.py --apply

# Qdrant guideline collections (matches backend/config.py GUIDELINE_COLLECTIONS)
python ingest/lenders/denali_nqm_guidelines_qdrant.py --apply
python ingest/lenders/everest_deephaven_guidelines_qdrant.py --apply
python ingest/lenders/summit_verus_guidelines_qdrant.py --apply
```

Use `--dry-run` on matrix scripts to preview without writing.

## Removed (superseded)

- `ingest.py` — legacy bulk indexer over `./Newpoint/`
- `ingest_matrix.py` / `ingest_guidelines.py` — generic GPT pipeline; replaced by per-lender scripts
- `denali_matrices_pdf_to_csv.py` — dev CSV export; logic lives in `denali_flow_to_mysql_qdrant.py`
