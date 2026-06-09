# Client Demo Script — Glass Processing MVP

**Duration:** ~20 minutes  
**Presenter role:** Glass Manager or System Manager  
**Demo user role:** Glass Sales User (show permissions are safe for normal staff)

## Before the demo

1. Open **Glass Factory Settings** and confirm:
   - Allowed Glass Types: `CLEAR`, `BRONZE`, `TINTED`
   - Warehouses and item groups are populated
2. Ensure raw stock exists: receive `GLS-CLEAR-8MM-3210X2250` into **Glass Raw Stock**
3. Log in as **Glass Sales User** for the selling steps

---

## Part 1 — Quote a glass order (5 min)

1. Go to **Selling > Quotation > New**
2. Select customer and company
3. In **Glass Pieces**, add a row:
   - **Raw Sheet Item:** `GLS-CLEAR-8MM-3210X2250`
   - **Length:** 1200 mm, **Width:** 800 mm, **Qty:** 2
   - Enable **Polish** and **Temper**
4. Point out: the **Items** table fills automatically with the Final glass item
5. Show that only **Rate** is editable on item lines
6. **Save** and **Submit** the Quotation

> Talking point: Sales staff enter pieces, not stock codes. The system resolves Raw → Cut WIP → Final items.

---

## Part 2 — Sales Order and cutting (5 min)

1. **Create > Sales Order** from the Quotation
2. **Submit** the Sales Order
3. Show the **Create > Cutting Job** button appears only after submit
4. Create the Cutting Job — pieces and source sheets pull automatically
5. **Submit** the Cutting Job
6. **Actions > Create Cutting Stock Movement** → review the Repack entry
7. **Actions > Submit Cutting Stock Movement**

> Talking point: Raw sheet moves to Cut WIP; valuation follows consumed material.

---

## Part 3 — Processing (5 min)

1. On the Cutting Job: **Actions > Start Processing**
2. Open the **Glass Processing Job**
3. Walk through dynamic actions:
   - **Start Polishing** → **Complete Polishing**
   - **Start Tempering** → **Complete Tempering**
4. **Create Final Stock Movement** → review Cut WIP → Final Goods
5. **Submit Final Stock Movement**
6. **Complete Job**

> Talking point: Operations follow the flags chosen on the quote. Each step is one clear action.

---

## Part 4 — Traceability and delivery (5 min)

1. Open the **Sales Order** and show trace fields:
   - Cutting Job, Processing Job, cut/processed quantities
2. Open linked **Stock Entries** from the jobs
3. **Create > Delivery Note** from the Sales Order
4. Submit delivery — show Final glass items ship from **Glass Final Goods**
5. Optionally create **Sales Invoice**

> Talking point: Full traceability from quote piece → cut → process → deliver, without BOMs.

---

## Quick alternative path (Sales Order only)

If time is short, skip the Quotation and create a **Sales Order** directly with glass pieces. The cutting → processing → delivery flow is identical after submit.

---

## Demo recovery tips

| Issue | Fix |
|-------|-----|
| "Glass Factory is not configured" | Complete Glass Factory Settings |
| Invalid glass type | Use CLEAR, BRONZE, or TINTED |
| No valuation on stock movement | Receive raw stock first |
| Cutting Job button missing | Submit the Sales Order first |
