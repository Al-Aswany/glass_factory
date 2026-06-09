# Manual UAT Checklist — Glass Processing MVP

Use this checklist for client UAT and internal sign-off. Test with a **Glass Sales User** account (not System Manager) unless the step explicitly requires setup access.

## Required setup

- [ ] **Glass Factory Settings** is complete (Setup > Glass Factory > Glass Factory Settings)
  - [ ] Allowed Glass Types lists demo values (e.g. `CLEAR`, `BRONZE`, `TINTED`)
  - [ ] Warehouses: Raw, Cut WIP, Final Goods, Remnants, Scrap
  - [ ] Item Groups: Raw, Cut WIP, Final, Remnants, Scrap (+ Default Item Group)
  - [ ] Default UOM = `Nos`
  - [ ] Scrap Item exists
- [ ] Raw sheet stock exists in **Glass Raw Stock** (e.g. `GLS-CLEAR-8MM-3210X2250`)
- [ ] User has role **Glass Sales User** (or Production/Stock roles for later steps)
- [ ] Selling Price List has glass rates configured (optional; rates can be entered manually)

### Negative setup tests

- [ ] Clear **Raw Warehouse** in settings → saving shows a clear validation error (no traceback)
- [ ] Clear **Allowed Glass Types** → saving shows a clear validation error
- [ ] Enter glass piece with type not in allowed list → error lists available types
- [ ] Clear **Default UOM** → saving shows a clear validation error

---

## Quotation cycle

1. [ ] Create **Quotation** for a customer
2. [ ] Add **Glass Pieces** rows:
   - Raw sheet item (Raw Sheet role)
   - Length, width, thickness, qty
   - Processing flags (e.g. Polish, Holes, Temper)
3. [ ] Confirm **Items** table is auto-built from glass pieces
4. [ ] Confirm item rows are **locked except Rate**
5. [ ] Adjust rate on a glass line → save succeeds
6. [ ] Submit Quotation
7. [ ] **Create > Sales Order** from Quotation
8. [ ] Confirm glass pieces and items copied correctly

### Quotation negative tests

- [ ] Cannot add/delete item rows when glass pieces exist
- [ ] Cannot change item code, qty, or UOM on generated glass lines
- [ ] No custom workflow buttons before submit

---

## Sales Order direct cycle

1. [ ] Create **Sales Order** directly (without Quotation)
2. [ ] Add glass pieces and verify item sync
3. [ ] Submit Sales Order
4. [ ] Confirm **Create > Cutting Job** appears **only after submit**
5. [ ] Confirm no other custom actions before submit

### Sales Order negative tests

- [ ] Draft SO has no Cutting Job button
- [ ] Item table locked except rate (same as Quotation)

---

## Cutting movement submit

1. [ ] From submitted SO, click **Create > Cutting Job**
2. [ ] Cutting Job auto-pulls glass pieces and source sheets
3. [ ] Confirm source sheet warehouse = Raw Warehouse from settings
4. [ ] Submit Cutting Job
5. [ ] Click **Actions > Create Cutting Stock Movement**
6. [ ] Review Stock Entry (Repack, Raw to Cut WIP flow)
7. [ ] Click **Actions > Submit Cutting Stock Movement**
8. [ ] Cutting Job status → **Cut Stock Posted**
9. [ ] SO item rows show `gf_cutting_job` and `gf_cut_qty` updated

### Cutting negative tests

- [ ] No custom actions on draft Cutting Job
- [ ] Cannot create movement without source sheets and pieces
- [ ] Missing warehouse in settings → clear error when pulling/creating movement

---

## Start Processing behavior

1. [ ] On submitted Cutting Job with **Cut Stock Posted**, click **Actions > Start Processing**
2. [ ] Glass Processing Job opens (or is linked on Cutting Job)
3. [ ] Inputs show Cut WIP items; outputs show Final items
4. [ ] Operations list matches processing flags on pieces

### Alternative entry

- [ ] From submitted cutting Stock Entry, **Start Processing** opens the Processing Job

---

## Processing Job dynamic actions

Test each step in order on a submitted Processing Job:

1. [ ] **Start {Operation}** — first pending operation only
2. [ ] **Complete {Operation}** — while operation is In Progress
3. [ ] Repeat until all operations completed
4. [ ] **Create Final Stock Movement** — Cut WIP to Final flow
5. [ ] **Submit Final Stock Movement**
6. [ ] Status → **Final Stock Posted**
7. [ ] **Complete Job**
8. [ ] SO items show `gf_processing_job` and `gf_processed_qty`

### Processing negative tests

- [ ] No actions before Processing Job submit
- [ ] Completed/Cancelled jobs show no further actions

---

## Delivery flow

1. [ ] Create **Delivery Note** from submitted Sales Order
2. [ ] Glass lines use Final items from SO
3. [ ] Submit Delivery Note
4. [ ] SO `gf_delivered_qty` updated on glass rows
5. [ ] Create **Sales Invoice** from SO or DN
6. [ ] Invoice uses Final glass items

### Delivery negative tests

- [ ] Cannot deliver Raw Sheet or Cut WIP items on glass SO lines
- [ ] Glass DN row must link to SO line

---

## Expected links and statuses

| Document | Key status / link |
|----------|-------------------|
| Quotation | Glass pieces → synced Final items |
| Sales Order | `gf_cutting_job`, `gf_processing_job`, qty trace fields |
| Cutting Job | `linked_stock_entry`, `linked_processing_job`, status progression |
| Stock Entry (cutting) | `gf_glass_stock_flow` = Raw to Cut WIP, `gf_cutting_job` |
| Glass Processing Job | `linked_stock_entry`, operation statuses |
| Stock Entry (final) | `gf_glass_stock_flow` = Cut WIP to Final, `gf_processing_job` |
| Delivery Note | `gf_cutting_job`, `gf_processing_job` on glass rows |

---

## Sign-off

| Tester | Role | Date | Pass / Fail | Notes |
|--------|------|------|-------------|-------|
| | Glass Sales User | | | |
| | Production Planner | | | |
| | Glass Manager | | | |
