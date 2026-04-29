# Glass Factory

A [Frappe](https://frappeframework.com) / [ERPNext](https://erpnext.com) app that manages the end-to-end glass cutting workflow — from customer quotation through COP (Cutting Optimization Pro) optimization to stock posting and delivery.

---

## Features

### Cutting Job
The central document that orchestrates the COP round-trip:

| Step | Action | Result |
|------|--------|--------|
| 1 | **Pull from Sales Orders** | Populates the pieces child table from all linked SO cut-piece rows |
| 2 | **Generate COP Files** | Produces `pieces.xlsx` (pieces to cut) and `stock_pre.xlsx` (available source sheets) |
| 3 | *(run COP externally)* | Upload `stock_post.xlsx` and tabular layout files back into the document |
| 4 | **Process Result** | Parses the COP output, computes utilization %, scrap area, and remnants |
| 5 | **Confirm & Post** | Submits Stock Entries and creates draft Delivery Notes (one per SO) |

Status flow: `Draft` → `Awaiting Optimization` → `Result Uploaded` → `Completed`

### Quotation & Sales Order Hooks
- **`compute_cut_pieces`** — fires on `before_save` for both Quotation and Sales Order; mirrors the `cut_pieces` child table to standard line items with automatic pricing (material cost + edge polish + bevels + holes)
- **`copy_cut_pieces_to_so`** — fires on `before_insert` for Sales Order; copies cut pieces from the source Quotation automatically

### Glass Cutting Settings
Single-document configuration for the whole module:

| Section | Fields |
|---------|--------|
| Remnant Settings | Min remnant area (m²), min remnant side (mm), min chargeable area (m²), default kerf (mm) |
| Naming Patterns | Cut piece item naming, remnant item naming, scrap item |
| Warehouses | Raw, cut pieces, remnants, scrap |
| Pricing | Edge polish rate (per m), bevel rate, hole drill rate, cost allocation method |

### Reports
- **Remnant Inventory** — lists all in-stock remnant items with dimensions, area, quantity, valuation, and the originating Cutting Job
- **Layout Visualizer** — visualizes the COP cutting layout

### Serial No Hooks
Automatically computes `area_m2` on Serial No records that carry `length_mm` / `width_mm` custom fields.

---

## Document Types

| DocType | Description |
|---------|-------------|
| `Cutting Job` | Main workflow document |
| `Cutting Job Piece` | Child table — one row per piece to be cut |
| `Cutting Job Source Sheet` | Child table — source glass sheets (Serial Nos) |
| `Cutting Job Linked SO` | Child table — linked Sales Orders |
| `Cutting Job Tabular File` | Child table — COP output tabular files |
| `Glass Cut Piece` | Cut-piece specification on Quotation / Sales Order |
| `Glass Cutting Settings` | Global configuration (single) |

---

## Requirements

- Frappe >= 16
- ERPNext >= 16
- [openpyxl](https://openpyxl.readthedocs.io/) (listed in `needs.txt`)

---

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app glass_factory
```

---

## Contributing

This app uses `pre-commit` for code formatting and linting. Install and enable it:

```bash
cd apps/glass_factory
pre-commit install
```

Configured tools:

- **ruff** — Python linting & formatting
- **eslint** — JavaScript linting
- **prettier** — JS/CSS/JSON formatting
- **pyupgrade** — modernises Python syntax

---

## License

MIT — see [license.txt](license.txt)
