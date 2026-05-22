# LOC Guard Exceptions

## 2026-05-22 NotebookLM Text Upload Hotfix

- File: `packages/memdex/scripts/memdex.py`
- Current size: 2523 LOC, above the default 1500 LOC block budget.
- Exception: allow the minimal upload-path hotfix already applied in this file.
- Rationale: NotebookLM `source add --type file` currently creates `UNKNOWN`
  sources that settle into `status:error`; `source add --type text` via stdin
  creates ready, queryable sources. The failing upload path is centralized in
  the current monolithic CLI script, so the smallest functional fix touches that
  file.
- Scope limit: no further feature work should be added to this file before
  extracting NotebookLM source upload/source state code into a smaller module.

## 2026-05-22 Default Chunk Target Adjustment

- File: `packages/memdex/scripts/memdex.py`
- Current size: 2523 LOC, above the default 1500 LOC block budget.
- Exception: allow changing the built-in `target_chunk_bytes` default from
  716800 to 524288.
- Rationale: the change is a narrow default-value adjustment requested after the
  NotebookLM stdin-text upload verification. Moving this constant to a new module
  first would create more churn than the value change itself.
- Scope limit: keep `max_chunk_bytes` unchanged for now so single source files
  larger than 512KiB still have an escape hatch instead of making planning fail.

## 2026-05-22 Sticky Chunk Threshold Alignment

- File: `packages/memdex/scripts/memdex.py`
- Current size: 2522 LOC, above the default 1500 LOC block budget.
- Exception: align sticky chunk reuse with `target_chunk_bytes` so lowering the
  default to 524288 affects existing repos instead of preserving old 716800-ish
  multi-file chunks forever.
- Rationale: the default-value change alone updates new configs but does not
  reduce existing repos because old active chunk membership was reused whenever
  it fit under `max_chunk_bytes`. This is part of the same chunk-size migration.
- Scope limit: keep the change inside chunk planning; do not add new upload or
  provider behavior here before extracting the monolithic script.
