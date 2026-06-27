# GlassOptimizer Phase A

Phase A connects ERPNext Cutting Jobs to the external GlassOptimizer console app using file-based JSON export and import. ERPNext does **not** call the Windows executable in this phase.

## Flow

1. Open a saved Cutting Job with source sheets and pieces populated.
2. Click **Export Optimization Job** on the Cutting Job form.
3. ERPNext creates `{cutting_job_name}-optimization-job.json` and attaches it to the document.
4. Copy the file to a Windows machine and run GlassOptimizer manually.
5. Upload the generated result file via **Import Optimization Result**.
6. ERPNext stores used sheets, placed pieces, remnants, and waste area on the Cutting Job.

No Stock Entry is created automatically in Phase A. The imported data is stored for future Repack #1 integration.

## Manual Windows command

```bat
GlassOptimizer.Console.exe optimize --input CJ-0001-optimization-job.json --output CJ-0001-optimization-result.json
```

## job.json schema

| Field | Type | Description |
|-------|------|-------------|
| `cutting_job` | string | Cutting Job name |
| `material` | string | Glass material code, e.g. `CLEAR-8MM` |
| `kerf_mm` | number | Saw kerf in millimetres (from Glass Factory Settings, default 3) |
| `stock_sheets` | array | Available source sheets |
| `pieces` | array | Pieces to cut |

Each `stock_sheets` row:

| Field | Type |
|-------|------|
| `sheet_id` | string |
| `length_mm` | number |
| `width_mm` | number |
| `qty` | number |

Each `pieces` row:

| Field | Type |
|-------|------|
| `piece_id` | string |
| `item_code` | string |
| `length_mm` | number |
| `width_mm` | number |
| `qty` | number |
| `process` | string |

## result.json schema

| Field | Type | Description |
|-------|------|-------------|
| `cutting_job` | string | Must match the Cutting Job name |
| `status` | string | Must be `completed` for import |
| `message` | string | Human-readable result message |
| `used_sheets` | array | Sheets consumed by the layout |
| `placed_pieces` | array | Pieces placed on sheets |
| `remnants` | array | Reusable offcuts (may be empty) |
| `waste_area_m2` | number | Total waste area in square metres |

Each `used_sheets` row: `sheet_id`, `used_qty`.

Each `placed_pieces` row: `piece_id`, `item_code`, `length_mm`, `width_mm`, `qty`, `source_sheet_id`.

Each `remnants` row: `source_sheet_id`, `length_mm`, `width_mm`, `qty`.

## Sample files

See [`samples/CJ-0001-optimization-job.json`](../samples/CJ-0001-optimization-job.json) and [`samples/CJ-0001-optimization-result.json`](../samples/CJ-0001-optimization-result.json).

## ERPNext API

Whitelisted methods in `glass_factory.glass_factory.glass_optimizer`:

- `export_optimization_job(cutting_job_name)`
- `import_optimization_result(cutting_job_name, file_url=None, json_text=None)`
- `get_imported_optimization_result(cutting_job_name)` — normalized result for future Repack #1 use
