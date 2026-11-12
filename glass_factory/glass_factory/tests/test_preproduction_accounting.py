"""Committed accounting and valuation integration checks for pre-production.

Run on a disposable site only:
bench --site glass-factory-validation.local execute glass_factory.glass_factory.tests.test_preproduction_accounting.run

Scenarios commit real documents. Run one at a time by name, for example
...test_preproduction_accounting.run --kwargs "{'only': 'repack2_valuation_capitalization_off'}"
The evidence (document names, ledger rows, balances) is printed as JSON at the end.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, flt, today

from glass_factory.glass_factory.doctype.cutting_job.cutting_job import make_cutting_job
from glass_factory.glass_factory.spec_transaction import add_spec_to_transaction, map_spec_to_transaction_row
from glass_factory.glass_factory.tests import test_preproduction_validation as v

COMPANY = v.COMPANY
RESULTS = v.RESULTS
EVIDENCE: dict[str, dict] = {}
check = v.check
expect = v.expect
expect_failure = v.expect_failure
near = v.near


# --------------------------------------------------------------------------- evidence helpers


def accounts() -> dict:
	"""Company accounts the scenarios read, looked up once from the Company master."""
	fields = (
		"default_inventory_account",
		"stock_received_but_not_billed",
		"default_payable_account",
		"default_receivable_account",
		"default_income_account",
		"default_expense_account",
		"stock_adjustment_account",
		"default_bank_account",
		"default_cash_account",
		"exchange_gain_loss_account",
	)
	values = frappe.db.get_value("Company", COMPANY, list(fields), as_dict=True)
	values["expenses_included_in_valuation"] = frappe.db.get_value(
		"Account", {"company": COMPANY, "account_type": "Expenses Included In Valuation", "is_group": 0}, "name"
	)
	return values


def gl_rows(voucher_type: str, voucher_no: str) -> list[dict]:
	return frappe.db.sql(
		"""select account, round(debit, 2) debit, round(credit, 2) credit, party, against_voucher
		from `tabGL Entry` where voucher_type=%s and voucher_no=%s and is_cancelled=0
		order by account, debit, credit""",
		(voucher_type, voucher_no),
		as_dict=True,
	)


def gl_by_account(voucher_type: str, voucher_no: str) -> dict[str, float]:
	"""Net debit minus credit per account for one voucher."""
	out: dict[str, float] = {}
	for row in gl_rows(voucher_type, voucher_no):
		out[row.account] = flt(out.get(row.account, 0) + flt(row.debit) - flt(row.credit), 2)
	return {account: amount for account, amount in out.items() if abs(amount) > 0.004}


def sle_rows(voucher_type: str, voucher_no: str) -> list[dict]:
	return frappe.db.sql(
		"""select item_code, warehouse, batch_no, actual_qty, round(incoming_rate, 4) incoming_rate,
		round(valuation_rate, 4) valuation_rate, round(stock_value_difference, 2) value_diff
		from `tabStock Ledger Entry` where voucher_type=%s and voucher_no=%s and is_cancelled=0
		order by creation, name""",
		(voucher_type, voucher_no),
		as_dict=True,
	)


def sle_value(voucher_type: str, voucher_no: str) -> float:
	return flt(
		frappe.db.sql(
			"select ifnull(sum(stock_value_difference),0) from `tabStock Ledger Entry` "
			"where voucher_type=%s and voucher_no=%s and is_cancelled=0",
			(voucher_type, voucher_no),
		)[0][0],
		2,
	)


def account_balance(account: str, *, party: str | None = None) -> float:
	filters = "account=%s and is_cancelled=0"
	args = [account]
	if party:
		filters += " and party=%s"
		args.append(party)
	return flt(frappe.db.sql(f"select ifnull(sum(debit-credit),0) from `tabGL Entry` where {filters}", args)[0][0], 2)


def balances() -> dict[str, float]:
	"""Net debit minus credit on every account of the operating company that has any posting."""
	rows = frappe.db.sql(
		"select account, ifnull(sum(debit-credit),0) bal from `tabGL Entry` where company=%s and is_cancelled=0 group by account",
		COMPANY,
		as_dict=True,
	)
	return {row.account: flt(row.bal, 2) for row in rows}


def balance_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
	keys = set(before) | set(after)
	delta = {key: flt(after.get(key, 0) - before.get(key, 0), 2) for key in keys}
	return {key: value for key, value in delta.items() if abs(value) > 0.004}


def stock_value_total() -> float:
	return flt(
		frappe.db.sql(
			"select ifnull(sum(stock_value_difference),0) from `tabStock Ledger Entry` where company=%s and is_cancelled=0",
			COMPANY,
		)[0][0],
		2,
	)


def stock_reconciliation_gap() -> float:
	"""Inventory GL balance minus the Stock Ledger value; zero when the two agree."""
	inventory = accounts()["default_inventory_account"]
	return flt(account_balance(inventory) - stock_value_total(), 2)


def sum_by_item(rows: list[dict], key: str) -> dict[str, float]:
	out: dict[str, float] = {}
	for row in rows:
		out[row["item_code"]] = flt(out.get(row["item_code"], 0) + flt(row[key]), 2)
	return out


# --------------------------------------------------------------------------- fixtures


def ensure_cost_rate(operation: str, basis: str, cost_rate: float, *, rate: float = 40, specified=None) -> None:
	for currency in (v.company_currency(),):
		v._ensure_operation_rate(operation, currency, basis, rate, cost_rate, specified=specified)


def make_spec_ops(currency, price_list, pieces, *, length, width, polish=1, temper=1, manual_rate=500, buying=600, customer="GFV Customer"):
	spec = frappe.new_doc("Glass Product Specification")
	spec.company, spec.customer, spec.currency, spec.price_list = COMPANY, customer, currency, price_list
	spec.raw_sheet_item, spec.length_mm, spec.width_mm, spec.qty = v.RAW, length, width, pieces
	spec.polish, spec.temper = polish, temper
	spec.manual_selling_rate_per_m2 = manual_rate
	spec.raw_sheet_rate_per_piece = buying
	spec.insert(ignore_permissions=True)
	spec.refresh_pricing()
	spec.reload()
	spec.generate_items()
	spec.reload()
	return spec


def set_capitalization(enabled: bool) -> None:
	doc = v.settings()
	doc.capitalize_processing_costs = 1 if enabled else 0
	if enabled:
		doc.processing_cost_expense_account = accounts()["expenses_included_in_valuation"]
	doc.save(ignore_permissions=True)
	frappe.db.commit()
	frappe.clear_cache()


def two_line_order(specs, *, currency=None, price_list="GFV Selling"):
	"""One submitted Sales Order holding one m² row per specification."""
	first, *rest = specs
	so = frappe.get_doc("Sales Order", add_spec_to_transaction(first.name, "Sales Order")["name"])
	for spec in rest:
		so.append("items", map_spec_to_transaction_row(spec))
	so.delivery_date = add_days(today(), 7)
	for row in so.items:
		row.delivery_date = so.delivery_date
	so.save()
	so.submit()
	so.reload()
	return so


def cutting_job_with_rates(so, batch_no, rates_by_final_item: dict[str, float] | None = None, *, sheets=1):
	job = make_cutting_job(so.name)
	job.reload()
	for piece in job.pieces:
		if rates_by_final_item and piece.final_item in rates_by_final_item:
			piece.basic_rate = rates_by_final_item[piece.final_item]
	job.source_sheets[0].batch_no = batch_no
	job.source_sheets[0].qty_consumed = sheets
	job.save(ignore_permissions=True)
	job.submit()
	job.reload()
	return job


def finished_batches(entry_name: str) -> dict[str, str]:
	entry = frappe.get_doc("Stock Entry", entry_name)
	return {row.item_code: row.batch_no for row in entry.items if row.t_warehouse and row.is_finished_item}


def deliver_line(so, item_code: str, batch_no: str, *, pieces=None, typed_qty=None):
	"""Delivery Note for one Sales Order row only."""
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = frappe.get_doc(make_delivery_note(so.name))
	dn.items = [row for row in dn.items if row.item_code == item_code]
	row = dn.items[0]
	if typed_qty is not None:
		row.qty = typed_qty
	elif pieces is not None:
		row.qty = flt(pieces / flt(row.conversion_factor), 9)
	row.batch_no = batch_no
	row.use_serial_batch_fields = 1
	dn.insert(ignore_permissions=True)
	dn.submit()
	dn.reload()
	return dn


def entry_rows(entry_name: str) -> dict:
	entry = frappe.get_doc("Stock Entry", entry_name)
	return {
		"incoming": [
			{
				"item": row.item_code,
				"qty": flt(row.qty),
				"basic_rate": flt(row.basic_rate, 4),
				"additional_cost": flt(row.additional_cost, 2),
				"valuation_rate": flt(row.valuation_rate, 4),
				"amount": flt(row.amount, 2),
			}
			for row in entry.items
			if row.t_warehouse
		],
		"outgoing": [
			{"item": row.item_code, "qty": flt(row.qty), "basic_rate": flt(row.basic_rate, 4), "amount": flt(row.amount, 2)}
			for row in entry.items
			if row.s_warehouse
		],
		"additional_costs": [{"account": c.expense_account, "amount": flt(c.amount, 2)} for c in entry.additional_costs],
		"total_additional_costs": flt(entry.total_additional_costs, 2),
	}


# --------------------------------------------------------------------------- buying, selling and payment helpers

SUPPLIER = "GFV Supplier"
FREIGHT_ITEM = "GFV-FREIGHT"


def ensure_supplier() -> str:
	if not frappe.db.exists("Supplier", SUPPLIER):
		supplier = frappe.new_doc("Supplier")
		supplier.supplier_name = SUPPLIER
		supplier.supplier_group = "All Supplier Groups"
		supplier.default_currency = v.company_currency()
		supplier.insert(ignore_permissions=True)
	if not frappe.db.exists("Item", FREIGHT_ITEM):
		item = frappe.new_doc("Item")
		item.item_code = item.item_name = FREIGHT_ITEM
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 0
		item.insert(ignore_permissions=True)
	frappe.db.commit()
	return SUPPLIER


def _copy_row(row) -> dict:
	data = row.as_dict()
	for key in ("name", "parent", "parentfield", "parenttype", "idx", "creation", "modified", "owner", "modified_by", "docstatus"):
		data.pop(key, None)
	return data


def purchase_cycle(rows: list[tuple[float, float]], *, bill: bool = True, pay: bool = False, on_step=None) -> dict:
	"""Purchase Order -> batched Purchase Receipt (one new batch per row) -> Purchase Invoice -> Payment Entry."""
	from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

	ensure_supplier()
	warehouse = v.settings().raw_warehouse
	po = frappe.new_doc("Purchase Order")
	po.company, po.supplier, po.transaction_date = COMPANY, SUPPLIER, today()
	po.currency, po.conversion_rate, po.buying_price_list = v.company_currency(), 1, "GFV Buying"
	po.schedule_date = add_days(today(), 3)
	for qty, rate in rows:
		po.append("items", {"item_code": v.RAW, "qty": qty, "rate": rate, "warehouse": warehouse, "schedule_date": po.schedule_date})
	po.insert(ignore_permissions=True)
	po.submit()

	pr = make_purchase_receipt(po.name)
	template = [_copy_row(row) for row in pr.items]
	pr.items = []
	batches = []
	for data in template:
		batch_no = v._batch(v.RAW, "PUR")
		batches.append(batch_no)
		data.update({"batch_no": batch_no, "use_serial_batch_fields": 1})
		pr.append("items", data)
	pr.insert(ignore_permissions=True)
	pr.submit()
	pr.reload()
	result = {"po": po.name, "pr": pr.name, "batches": batches, "receipt_value": flt(pr.grand_total, 2)}
	if on_step:
		on_step("purchase receipt posted")
	if bill:
		pi = make_purchase_invoice(pr.name)
		pi.bill_no = f"SUP-{pr.name}"
		pi.bill_date = today()
		pi.insert(ignore_permissions=True)
		pi.submit()
		pi.reload()
		result["pi"] = pi.name
		if on_step:
			on_step("purchase invoice posted")
		if pay:
			result["supplier_payment"] = supplier_payment("Purchase Invoice", pi.name)
			if on_step:
				on_step("supplier payment posted")
	frappe.db.commit()
	return result


def supplier_payment(doctype: str, name: str, amount: float | None = None) -> str:
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry(doctype, name, party_amount=amount)
	pe.reference_no = f"PAY-{name}"
	pe.reference_date = today()
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe.name


def customer_payment(invoice_name: str, amount: float | None = None, *, bank_account: str | None = None) -> str:
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	pe = get_payment_entry("Sales Invoice", invoice_name, party_amount=amount, bank_account=bank_account)
	pe.reference_no = f"RCPT-{invoice_name}"
	pe.reference_date = today()
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe.name


def landed_cost(pr_name: str, amount: float, *, distribute: str = "Amount") -> str:
	"""Landed Cost Voucher crediting Expenses Included In Valuation, plus the supplier's freight invoice."""
	lcv = frappe.new_doc("Landed Cost Voucher")
	lcv.company = COMPANY
	lcv.posting_date = today()
	lcv.distribute_charges_based_on = distribute
	pr = frappe.get_doc("Purchase Receipt", pr_name)
	lcv.append(
		"purchase_receipts",
		{
			"receipt_document_type": "Purchase Receipt",
			"receipt_document": pr_name,
			"supplier": pr.supplier,
			"posting_date": pr.posting_date,
			"grand_total": pr.grand_total,
		},
	)
	lcv.get_items_from_purchase_receipts()
	lcv.append(
		"taxes",
		{"description": "Freight and clearing", "expense_account": accounts()["expenses_included_in_valuation"], "amount": amount},
	)
	lcv.insert(ignore_permissions=True)
	lcv.submit()
	frappe.db.commit()
	return lcv.name


def freight_invoice(amount: float) -> str:
	"""The supplier's invoice for the landed cost: expense side of the Landed Cost Voucher."""
	ensure_supplier()
	pi = frappe.new_doc("Purchase Invoice")
	pi.company, pi.supplier, pi.posting_date = COMPANY, SUPPLIER, today()
	pi.currency, pi.conversion_rate = v.company_currency(), 1
	pi.bill_no, pi.bill_date = f"FRT-{frappe.generate_hash(length=6)}", today()
	pi.append(
		"items",
		{"item_code": FREIGHT_ITEM, "qty": 1, "rate": amount, "expense_account": accounts()["expenses_included_in_valuation"]},
	)
	pi.insert(ignore_permissions=True)
	pi.submit()
	frappe.db.commit()
	return pi.name


def outstanding(doctype: str, name: str) -> float:
	return flt(frappe.db.get_value(doctype, name, "outstanding_amount"), 2)


def batch_balance(batch_no: str, warehouse: str) -> dict:
	"""Quantity and value of one batch in one warehouse, from the Serial and Batch Entry ledger."""
	row = frappe.db.sql(
		"""select ifnull(sum(e.qty),0) qty, ifnull(sum(e.stock_value_difference),0) value
		from `tabSerial and Batch Entry` e join `tabSerial and Batch Bundle` b on b.name = e.parent
		where e.batch_no=%s and e.warehouse=%s and b.docstatus=1 and b.is_cancelled=0""",
		(batch_no, warehouse),
		as_dict=True,
	)[0]
	return {"qty": flt(row.qty, 3), "value": flt(row.value, 2)}


def bin_state(item_code: str, warehouse: str) -> dict:
	row = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, ["actual_qty", "stock_value"], as_dict=True)
	return {"qty": flt(row.actual_qty, 3), "value": flt(row.stock_value, 2)} if row else {"qty": 0.0, "value": 0.0}


def run_pending_reposts() -> list[str]:
	"""Run queued Repost Item Valuation entries now. On a live site the hourly scheduler job does this."""
	from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import execute_reposting_entry

	names = frappe.get_all(
		"Repost Item Valuation",
		filters={"status": ["in", ["Queued", "In Progress"]], "docstatus": 1},
		pluck="name",
		order_by="creation asc",
	)
	for name in names:
		execute_reposting_entry(name)
	frappe.db.commit()
	return names


# --------------------------------------------------------------------------- scenario 1: Repack #2 valuation


def _two_equal_area_lines(label: str, *, ops_a, ops_b, cut_rates: tuple[float, float], capitalization: bool):
	"""Two equal-area finished items, Cut WIP worth cut_rates, one Repack #2, separate deliveries."""
	currency = v.company_currency()
	set_capitalization(capitalization)
	ensure_cost_rate("Polish", "Per Edge Meter", 5)
	ensure_cost_rate("Temper", "Per Square Meter", 20)
	batch_no = v._batch(v.RAW, "VAL")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, sum(cut_rates), batch_no)

	spec_a = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=ops_a[0], temper=ops_a[1])
	spec_b = make_spec_ops(currency, "GFV Selling", 1, length=1000, width=960, polish=ops_b[0], temper=ops_b[1])
	expect(near(spec_a.area_m2, spec_b.area_m2), "the two finished items must have equal area")
	expect(spec_a.final_item_code != spec_b.final_item_code, "the two lines must be different finished items")
	so = two_line_order([spec_a, spec_b])
	items = {spec_a.final_item_code: cut_rates[0], spec_b.final_item_code: cut_rates[1]}

	job = cutting_job_with_rates(so, batch_no, items)
	v.cut(job)
	cutting_entry = frappe.get_doc("Stock Entry", job.linked_stock_entry)
	cut_in = {row.item_code: flt(row.valuation_rate, 4) for row in cutting_entry.items if row.t_warehouse and row.is_finished_item}
	cut_wip = {
		spec_a.final_item_code: next(piece.cut_wip_item for piece in job.pieces if piece.final_item == spec_a.final_item_code),
		spec_b.final_item_code: next(piece.cut_wip_item for piece in job.pieces if piece.final_item == spec_b.final_item_code),
	}
	expect(
		near(cut_in[cut_wip[spec_a.final_item_code]], cut_rates[0], 2) and near(cut_in[cut_wip[spec_b.final_item_code]], cut_rates[1], 2),
		f"{label}: Cut WIP batches not valued {cut_rates}: {cut_in}",
	)

	before = balances()
	processing = v.process(job)
	entry_name = processing.linked_stock_entry
	rows = entry_rows(entry_name)
	return {
		"specs": (spec_a, spec_b),
		"so": so,
		"job": job,
		"processing": processing,
		"entry": entry_name,
		"rows": rows,
		"cut_in": cut_in,
		"balances_before": before,
		"batches": finished_batches(entry_name),
		"company_currency": currency,
	}


def _deliver_each_line(ctx) -> dict:
	"""Deliver the two lines in separate Delivery Notes and read each COGS from its own GL entries."""
	spec_a, spec_b = ctx["specs"]
	acc = accounts()
	out = {}
	for label, spec in (("a", spec_a), ("b", spec_b)):
		dn = deliver_line(ctx["so"], spec.final_item_code, ctx["batches"][spec.final_item_code], pieces=1)
		out[label] = {
			"delivery_note": dn.name,
			"cogs": flt(gl_by_account("Delivery Note", dn.name).get(acc["default_expense_account"], 0), 2),
			"sle": sle_rows("Delivery Note", dn.name),
		}
	return out


def repack2_valuation_capitalization_off() -> str:
	ctx = _two_equal_area_lines("cap off", ops_a=(1, 0), ops_b=(1, 1), cut_rates=(50, 150), capitalization=False)
	spec_a, spec_b = ctx["specs"]
	rows = ctx["rows"]
	finished = {row["item"]: row for row in rows["incoming"]}
	value_a = finished[spec_a.final_item_code]["valuation_rate"] * finished[spec_a.final_item_code]["qty"]
	value_b = finished[spec_b.final_item_code]["valuation_rate"] * finished[spec_b.final_item_code]["qty"]
	expect(near(value_a, 50, 2) and near(value_b, 150, 2), f"finished values {value_a}/{value_b}, expected 50/150: {rows}")
	expect(not rows["additional_costs"], "capitalization is off but the movement carries additional costs")
	expect(abs(sle_value("Stock Entry", ctx["entry"])) < 0.01, "Repack #2 does not net to zero in the Stock Ledger")
	delivered = _deliver_each_line(ctx)
	EVIDENCE["repack2_capitalization_off"] = {
		"specs": [spec_a.final_item_code, spec_b.final_item_code],
		"sales_order": ctx["so"].name,
		"cutting_job": ctx["job"].name,
		"processing_job": ctx["processing"].name,
		"repack2_entry": ctx["entry"],
		"cut_wip_value_in": ctx["cut_in"],
		"repack2": rows,
		"repack2_sle": sle_rows("Stock Entry", ctx["entry"]),
		"repack2_gl": gl_by_account("Stock Entry", ctx["entry"]),
		"deliveries": delivered,
	}
	expect(near(delivered["a"]["cogs"], 50, 2) and near(delivered["b"]["cogs"], 150, 2), f"COGS {delivered['a']['cogs']}/{delivered['b']['cogs']}, expected 50/150")
	expect(not gl_by_account("Stock Entry", ctx["entry"]), f"Repack #2 should not move any account balance: {gl_by_account('Stock Entry', ctx['entry'])}")
	return f"{ctx['entry']}: finished {value_a} / {value_b}; COGS {delivered['a']['cogs']} / {delivered['b']['cogs']}"


def _edge_metres(length: float, width: float) -> float:
	return 2 * (length + width) / 1000


def _capitalized_two_lines(label: str, *, ops_a, ops_b, cost_a: float, cost_b: float) -> str:
	"""Cut WIP worth 50 and 150, Polish at 5 per edge metre, Temper at 20 per m2; check where each cost lands."""
	ctx = _two_equal_area_lines(label, ops_a=ops_a, ops_b=ops_b, cut_rates=(50, 150), capitalization=True)
	spec_a, spec_b = ctx["specs"]
	acc = accounts()
	rows = ctx["rows"]
	finished = {row["item"]: row for row in rows["incoming"]}
	row_a, row_b = finished[spec_a.final_item_code], finished[spec_b.final_item_code]
	total = flt(cost_a + cost_b, 2)
	# What ERPNext alone does with the same entry: its own allocator shares one pool by material value.
	from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry as ERPNextStockEntry

	posted = frappe.get_doc("Stock Entry", ctx["entry"])
	incoming, basis, total_basis = ERPNextStockEntry.get_additional_cost_allocation(posted)
	pooled_by_item = {
		row.item_code: flt(posted.total_additional_costs * flt(row.get(basis)) / total_basis, 2) for row in incoming
	}
	pooled = {"a": pooled_by_item[spec_a.final_item_code], "b": pooled_by_item[spec_b.final_item_code], "basis": basis}
	expect(near(rows["total_additional_costs"], total, 2), f"additional costs {rows['total_additional_costs']} != {total}")
	expect(near(row_a["additional_cost"], cost_a, 2) and near(row_b["additional_cost"], cost_b, 2), f"additional cost per line {row_a['additional_cost']}/{row_b['additional_cost']}, expected {cost_a}/{cost_b}: {rows}")
	expect(near(row_a["amount"], 50 + cost_a, 2) and near(row_b["amount"], 150 + cost_b, 2), f"finished values {row_a['amount']}/{row_b['amount']}")
	gl = gl_by_account("Stock Entry", ctx["entry"])
	expect(
		near(gl.get(acc["default_inventory_account"], 0), total, 2)
		and near(gl.get(acc["expenses_included_in_valuation"], 0), -total, 2)
		and len(gl) == 2,
		f"Repack #2 GL should only capitalize {total} into inventory: {gl}",
	)
	expect(abs(flt(sle_value("Stock Entry", ctx["entry"])) - total) < 0.01, "Stock Ledger value added is not the processing cost")
	delivered = _deliver_each_line(ctx)
	expect(near(delivered["a"]["cogs"], 50 + cost_a, 2) and near(delivered["b"]["cogs"], 150 + cost_b, 2), f"COGS {delivered['a']['cogs']}/{delivered['b']['cogs']}")
	EVIDENCE[f"repack2_{label}"] = {
		"sales_order": ctx["so"].name,
		"processing_job": ctx["processing"].name,
		"repack2_entry": ctx["entry"],
		"line_costs_expected": {"a": cost_a, "b": cost_b},
		"erpnext_pooled_split_would_be": pooled,
		"repack2": rows,
		"repack2_sle": sle_rows("Stock Entry", ctx["entry"]),
		"repack2_gl": gl,
		"deliveries": delivered,
	}
	set_capitalization(False)
	return (
		f"{ctx['entry']}: line costs {row_a['additional_cost']}/{row_b['additional_cost']} (pooled would be {pooled['a']}/{pooled['b']}); "
		f"finished {row_a['amount']}/{row_b['amount']}; COGS {delivered['a']['cogs']}/{delivered['b']['cogs']}"
	)


def repack2_capitalization_on() -> str:
	expect(flt(v.settings().get("min_chargeable_area_m2")) < 0.96, "a minimum chargeable area would change the expected costs")
	cost_a = flt(5 * _edge_metres(1200, 800), 2)
	cost_b = flt(5 * _edge_metres(1000, 960) + 20 * 1000 * 960 / 1_000_000, 2)
	return _capitalized_two_lines("capitalization_on", ops_a=(1, 0), ops_b=(1, 1), cost_a=cost_a, cost_b=cost_b)


def repack2_capitalization_line_without_operations() -> str:
	"""Only one line is polished: the other must receive no processing cost at all."""
	cost_a = flt(5 * _edge_metres(1200, 800), 2)
	return _capitalized_two_lines("capitalization_one_line_unprocessed", ops_a=(1, 0), ops_b=(0, 0), cost_a=cost_a, cost_b=0.0)



# --------------------------------------------------------------------------- scenario 5: agreed accounting example


def agreed_example(label: str, *, currency: str, price_list: str, lcv: str = "before", pay_customer: bool = True, customer: str = "GFV Customer") -> dict:
	"""Material 100, landed cost 20, capitalized processing 30; two equal pieces; deliver and invoice one.

	lcv: "before" applies the Landed Cost Voucher before production, "after" once one piece is delivered.
	"""
	set_capitalization(True)
	try:
		return _agreed_example(label, currency=currency, price_list=price_list, lcv=lcv, pay_customer=pay_customer, customer=customer)
	finally:
		set_capitalization(False)


def _agreed_example(label: str, *, currency: str, price_list: str, lcv: str, pay_customer: bool, customer: str) -> dict:
	# 1200 x 800 mm polished on four edges: 4.0 edge metres per piece, 8.0 for two pieces, at 3.75 = 30.00.
	ensure_cost_rate("Polish", "Per Edge Meter", 3.75)
	acc = accounts()
	before = balances()
	stock_before = stock_value_total()
	timeline: list[dict] = []
	watched = [
		acc["default_inventory_account"],
		acc["stock_received_but_not_billed"],
		acc["default_payable_account"],
		acc["default_receivable_account"],
		acc["default_income_account"],
		acc["default_expense_account"],
		acc["expenses_included_in_valuation"],
		acc["stock_adjustment_account"],
		acc["default_bank_account"],
	]

	def step(name: str) -> None:
		delta = balance_delta(before, balances())
		timeline.append({"step": name, "balance_change": {account: delta.get(account, 0) for account in watched if abs(delta.get(account, 0)) > 0.004}})

	purchase = purchase_cycle([(1, 100)], bill=True, pay=True, on_step=step)
	documents = dict(purchase)
	if lcv == "before":
		documents["lcv"] = landed_cost(purchase["pr"], 20)
		documents["freight_invoice"] = freight_invoice(20)
		step("landed cost voucher and freight invoice posted (before production)")

	spec = make_spec_ops(currency, price_list, 2, length=1200, width=800, polish=1, temper=0, buying=100, customer=customer)
	_quotation, so = v.quote_and_order(spec)
	job = v.cut(v.cutting_job_for(so, purchase["batches"][0]))
	step("Repack 1 posted (sheet to Cut WIP)")
	processing = v.process(job)
	step("Repack 2 posted (Cut WIP to finished, processing cost capitalized)")
	final_item = spec.final_item_code
	final_batch_no = finished_batches(processing.linked_stock_entry)[final_item]
	final_warehouse = v.settings().final_goods_warehouse

	dn = deliver_line(so, final_item, final_batch_no, pieces=1)
	step("delivery note posted (1 of 2 pieces)")
	invoice = v.invoice_for(dn)
	step("sales invoice posted")
	cogs_before_landed_cost = flt(gl_by_account("Delivery Note", dn.name).get(acc["default_expense_account"], 0), 2)
	if lcv == "after":
		documents["lcv"] = landed_cost(purchase["pr"], 20)
		documents["freight_invoice"] = freight_invoice(20)
		documents["queued_reposts_before_run"] = frappe.get_all(
			"Repost Item Valuation", filters={"status": "Queued", "docstatus": 1}, pluck="name"
		)
		documents["reposts_run"] = run_pending_reposts()
		dn.reload()
		invoice.reload()
		step("landed cost voucher and freight invoice posted after delivery, reposts run")
	if pay_customer:
		documents["customer_payment"] = customer_payment(invoice.name)
		step("customer payment posted")
	frappe.db.commit()

	cutting_entry, final_entry = job.linked_stock_entry, processing.linked_stock_entry
	documents.update(
		{
			"specification": spec.name,
			"sales_order": so.name,
			"cutting_job": job.name,
			"cutting_entry": cutting_entry,
			"processing_job": processing.name,
			"repack2_entry": final_entry,
			"delivery_note": dn.name,
			"sales_invoice": invoice.name,
		}
	)
	vouchers = {
		"Purchase Receipt": [purchase["pr"]],
		"Purchase Invoice": [purchase["pi"], documents["freight_invoice"]],
		"Landed Cost Voucher": [documents["lcv"]],
		"Stock Entry": [cutting_entry, final_entry],
		"Delivery Note": [dn.name],
		"Sales Invoice": [invoice.name],
	}
	if documents.get("supplier_payment"):
		vouchers["Payment Entry"] = [documents["supplier_payment"]]
	if documents.get("customer_payment"):
		vouchers.setdefault("Payment Entry", []).append(documents["customer_payment"])
	ledgers = {}
	for voucher_type, names in vouchers.items():
		for name in names:
			ledgers[f"{voucher_type} {name}"] = {"gl": gl_by_account(voucher_type, name), "sle": sle_rows(voucher_type, name)}

	delta = balance_delta(before, balances())
	sheet_batch = purchase["batches"][0]
	state = {
		"final_stock_remaining": batch_balance(final_batch_no, final_warehouse),
		"cogs": flt(gl_by_account("Delivery Note", dn.name).get(acc["default_expense_account"], 0), 2),
		"revenue_before_tax": flt(invoice.net_total, 2),
		"revenue_gl": flt(-gl_by_account("Sales Invoice", invoice.name).get(acc["default_income_account"], 0), 2),
		"invoice_total": flt(invoice.grand_total, 2),
		"invoice_base_total": flt(invoice.base_grand_total, 2),
		"outstanding": {
			"sales_invoice": outstanding("Sales Invoice", invoice.name),
			"purchase_invoice_goods": outstanding("Purchase Invoice", purchase["pi"]),
			"purchase_invoice_freight": outstanding("Purchase Invoice", documents["freight_invoice"]),
		},
		"account_deltas": delta,
		"inventory_gl_minus_stock_ledger_delta": flt(delta.get(acc["default_inventory_account"], 0) - (stock_value_total() - stock_before), 2),
		"gl_vs_stock_ledger_gap_overall": stock_reconciliation_gap(),
		"srbnb_delta": delta.get(acc["stock_received_but_not_billed"], 0),
		"stock_adjustment_delta": delta.get(acc["stock_adjustment_account"], 0),
		"batch": sheet_batch,
		"cogs_before_landed_cost": cogs_before_landed_cost,
	}
	EVIDENCE[label] = {"documents": documents, "state": state, "ledgers": ledgers, "timeline": timeline}
	return EVIDENCE[label]


def accounting_example_lcv_after_delivery() -> str:
	result = agreed_example("agreed_example_tzs_lcv_after_delivery", currency=v.company_currency(), price_list="GFV Selling", lcv="after")
	state = result["state"]
	expect(near(state["cogs_before_landed_cost"], 65, 2), f"before the landed cost, COGS should be (100+30)/2: {state['cogs_before_landed_cost']}")
	expect(near(state["final_stock_remaining"]["value"], 75, 2) and near(state["final_stock_remaining"]["qty"], 1), f"finished stock remaining {state['final_stock_remaining']}")
	expect(near(state["cogs"], 75, 2), f"COGS {state['cogs']}")
	expect(near(state["revenue_before_tax"], 480, 2), f"revenue {state['revenue_before_tax']}")
	expect(abs(state["stock_adjustment_delta"]) < 0.01, f"landed cost leaked to stock adjustment {state['stock_adjustment_delta']}")
	expect(abs(state["inventory_gl_minus_stock_ledger_delta"]) < 0.01, "inventory GL and Stock Ledger disagree")
	return json.dumps({"stock": state["final_stock_remaining"], "cogs": state["cogs"], "cogs_before": state["cogs_before_landed_cost"]})


def accounting_example_tzs() -> str:
	result = agreed_example("agreed_example_tzs_lcv_before", currency=v.company_currency(), price_list="GFV Selling")
	state = result["state"]
	expect(near(state["final_stock_remaining"]["value"], 75, 2) and near(state["final_stock_remaining"]["qty"], 1), f"finished stock remaining {state['final_stock_remaining']}")
	expect(near(state["cogs"], 75, 2), f"COGS {state['cogs']}")
	expect(near(state["revenue_before_tax"], 480, 2) and near(state["revenue_gl"], 480, 2), f"revenue {state['revenue_before_tax']}/{state['revenue_gl']}")
	return json.dumps({"stock": state["final_stock_remaining"], "cogs": state["cogs"], "revenue": state["revenue_before_tax"]})


# --------------------------------------------------------------------------- scenario 3b: landed cost after downstream movement


def landed_cost_after_two_item_production() -> str:
	"""Two finished Items come out of one sheet; both are delivered; then a Landed Cost Voucher is applied.

	Both repacks carry several finished Items, so their rates cannot be derived by ERPNext alone.
	After the voucher and the reposts every layer must carry the extra cost: no leakage to Stock Adjustment.
	"""
	set_capitalization(False)
	acc = accounts()
	currency = v.company_currency()
	before = balances()
	purchase = purchase_cycle([(1, 100)], bill=True)
	spec_a = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=1, temper=0)
	spec_b = make_spec_ops(currency, "GFV Selling", 1, length=1000, width=960, polish=1, temper=0)
	so = two_line_order([spec_a, spec_b])
	job = cutting_job_with_rates(so, purchase["batches"][0])
	v.cut(job)
	processing = v.process(job)
	batches = finished_batches(processing.linked_stock_entry)
	dn_a = deliver_line(so, spec_a.final_item_code, batches[spec_a.final_item_code], pieces=1)
	dn_b = deliver_line(so, spec_b.final_item_code, batches[spec_b.final_item_code], pieces=1)

	def cogs(dn):
		return flt(gl_by_account("Delivery Note", dn.name).get(acc["default_expense_account"], 0), 2)

	stage_before = {"cogs_a": cogs(dn_a), "cogs_b": cogs(dn_b), "repack2": entry_rows(processing.linked_stock_entry)}
	documents = {"purchase": purchase, "lcv": landed_cost(purchase["pr"], 40), "freight_invoice": freight_invoice(40)}
	documents["reposts_run"] = len(run_pending_reposts())
	final_warehouse = v.settings().final_goods_warehouse
	state = {
		"before_landed_cost": stage_before,
		"cogs_a": cogs(dn_a),
		"cogs_b": cogs(dn_b),
		"cut_wip_value_in": [r["value_diff"] for r in sle_rows("Stock Entry", job.linked_stock_entry) if r["value_diff"] > 0],
		"repack2_after": entry_rows(processing.linked_stock_entry),
		"repack2_sle": sle_rows("Stock Entry", processing.linked_stock_entry),
		"final_stock_after": {item: batch_balance(batches[item], final_warehouse) for item in batches},
		"account_deltas": balance_delta(before, balances()),
		"gl_vs_stock_ledger_gap_overall": stock_reconciliation_gap(),
		"documents": {"cutting_entry": job.linked_stock_entry, "repack2_entry": processing.linked_stock_entry, "dn_a": dn_a.name, "dn_b": dn_b.name},
	}
	EVIDENCE["landed_cost_after_two_item_production"] = {"documents": documents, "state": state, "ledgers": {}}
	delta = state["account_deltas"]
	expect(near(state["cogs_a"], 70, 2) and near(state["cogs_b"], 70, 2), f"COGS after landed cost {state['cogs_a']}/{state['cogs_b']}, expected 70/70")
	expect(abs(delta.get(acc["stock_adjustment_account"], 0)) < 0.01, f"landed cost leaked to Stock Adjustment: {delta.get(acc['stock_adjustment_account'])}")
	expect(abs(state["gl_vs_stock_ledger_gap_overall"]) < 0.01, "inventory GL and Stock Ledger disagree")
	return json.dumps({"cogs": [state["cogs_a"], state["cogs_b"]], "stock_adjustment": delta.get(acc["stock_adjustment_account"], 0)})


# --------------------------------------------------------------------------- scenario 2: billing precision

def set_precision_config(mode: str) -> None:
	"""Quantity precision under test.

	system3: ERPNext default Float Precision (3), the current behaviour
	system6: System Settings > Float Precision = 6 for every Float field on the site
	rows6 / rows2: only the selling item Quantity fields get that precision, through the app helper
	"""
	from frappe.core.doctype.system_settings.system_settings import clear_system_settings_cache

	from glass_factory.glass_factory.selling_uom import set_billing_area_precision

	value = "6" if mode == "system6" else ""
	# System Settings saves the value and mirrors it into the defaults that frappe.get_precision reads.
	frappe.db.set_single_value("System Settings", "float_precision", value)
	frappe.db.set_default("float_precision", value)
	clear_system_settings_cache()
	set_billing_area_precision(int(mode[-1]) if mode.startswith("rows") else 3)
	frappe.db.commit()
	frappe.clear_cache()


def _doc_line(doc, item_code=None) -> dict:
	row = doc.items[0]
	return {
		"doc": doc.name,
		"uom": row.uom,
		"qty_m2": flt(row.qty, 9),
		"stock_qty": flt(row.stock_qty, 6),
		"factor": flt(row.conversion_factor, 9),
		"rate": flt(row.rate, 6),
		"amount": flt(row.amount, 2),
		"base_amount": flt(row.base_amount, 2),
		"total": flt(doc.grand_total, 2),
	}


def billing_roundtrip(mode: str, *, length=1234, width=567, pieces=3, rate=500) -> dict:
	"""Specification -> Quotation -> Sales Order -> cut -> process -> typed partial deliveries -> invoices -> returns."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_credit_note
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as make_delivery_return

	set_precision_config(mode)
	try:
		set_capitalization(False)
		exact_area = flt(length * width / 1_000_000, 6)
		exact_total = flt(exact_area * pieces * rate, 2)
		currency = v.company_currency()
		batch_no = v.raw_batch(sheets=1, rate=100)
		spec = make_spec_ops(currency, "GFV Selling", pieces, length=length, width=width, manual_rate=rate)
		quotation, so = v.quote_and_order(spec)
		item = frappe.db.get_value("Item", spec.final_item_code, ["gf_length_mm", "gf_width_mm"], as_dict=True)
		result: dict = {
			"mode": mode,
			"exact_area_per_piece": exact_area,
			"exact_total": exact_total,
			"physical": {"item_mm": [flt(item.gf_length_mm), flt(item.gf_width_mm)], "spec_area_m2": flt(spec.area_m2, 6)},
			"spec": {"name": spec.name, "rate_per_piece": flt(spec.rate_per_piece, 6), "amount": flt(spec.amount, 2)},
			"quotation": _doc_line(quotation),
			"sales_order": _doc_line(so),
		}
		so_row = so.items[0]
		result["physical"]["sales_order_row"] = {"gf_area_m2": flt(so_row.gf_area_m2, 6), "gf_total_area_m2": flt(so_row.gf_total_area_m2, 6)}
		job = v.cut(v.cutting_job_for(so, batch_no))
		piece = job.pieces[0]
		result["physical"]["cutting_piece"] = {"mm": [flt(piece.length_mm), flt(piece.width_mm)], "qty": flt(piece.qty_required)}
		processing = v.process(job)
		final_no = v.final_batch(processing)
		result["physical"]["final_batch_mm"] = frappe.db.get_value("Batch", final_no, ["gf_length_mm", "gf_width_mm"])

		# Typed quantities: the partial deliveries are keyed in by hand at the precision a person would use.
		dn1 = v.delivery_for(so, typed_qty=0.7, batch_no=final_no)
		si1 = v.invoice_for(dn1)
		# Two pieces typed as the exact area rounded to 3 decimals (1.399). Whether the site accepts that
		# or asks for a multiple of the billed area depends on the precision under test.
		typed_attempts = []
		dn2 = None
		for typed in (1.399, 1.4):
			frappe.db.savepoint("typed_partial")
			try:
				dn2 = v.delivery_for(so, typed_qty=typed, batch_no=final_no)
				typed_attempts.append({"typed": typed, "result": "accepted"})
				break
			except Exception as exc:
				frappe.db.rollback(save_point="typed_partial")
				typed_attempts.append({"typed": typed, "result": f"rejected: {str(exc)[:110]}"})
		expect(dn2 is not None, f"no typed partial delivery was accepted {typed_attempts}")
		result["typed_partial_delivery_attempts"] = typed_attempts
		si2 = v.invoice_for(dn2)
		result["deliveries"] = [_doc_line(dn1), _doc_line(dn2)]
		result["invoices"] = [_doc_line(si1), _doc_line(si2)]
		result["stock_ledger_pieces"] = [v.sle_qty(dn1.name), v.sle_qty(dn2.name)]

		so.reload()
		row = so.items[0]
		result["sales_order_after"] = {
			"delivered_qty": flt(row.delivered_qty, 9),
			"billed_amt": flt(row.billed_amt, 2),
			"per_delivered": flt(so.per_delivered, 4),
			"per_billed": flt(so.per_billed, 4),
			"status": so.status,
			"gf_delivered_pieces": flt(row.gf_delivered_qty, 6),
		}

		# Return one piece, typed, then credit it.
		ret = frappe.get_doc(make_delivery_return(dn2.name))
		ret.items[0].qty = -0.7
		ret.items[0].use_serial_batch_fields = 1
		ret.items[0].batch_no = final_no
		ret.insert(ignore_permissions=True)
		ret.submit()
		ret.reload()
		credit = frappe.get_doc(make_credit_note(si2.name))
		credit.items[0].qty = -0.7
		credit.insert(ignore_permissions=True)
		credit.submit()
		credit.reload()
		result["returns"] = {"delivery_return": _doc_line(ret), "credit_note": _doc_line(credit), "stock_ledger_pieces": v.sle_qty(ret.name)}
		so.reload()
		result["sales_order_after_return"] = {
			"gf_delivered_pieces": flt(so.items[0].gf_delivered_qty, 6),
			"delivered_qty": flt(so.items[0].delivered_qty, 9),
			"per_delivered": flt(so.per_delivered, 4),
		}
		frappe.db.commit()
		EVIDENCE[f"billing_{mode}"] = result
		return result
	finally:
		set_precision_config("system3")


def _billing_checks(result: dict, *, exact: bool) -> str:
	"""Documents must always agree with each other; with exact=True they must also match the exact area."""
	pieces = 3
	qn, so = result["quotation"], result["sales_order"]
	dns, sis = result["deliveries"], result["invoices"]
	broken, gaps = [], []
	for name, line in (("quotation", qn), ("sales order", so), ("delivery 1", dns[0]), ("delivery 2", dns[1]), ("invoice 1", sis[0]), ("invoice 2", sis[1])):
		if line["stock_qty"] != round(line["stock_qty"]):
			broken.append(f"{name} stock qty {line['stock_qty']} is not whole")
	if [flt(x) for x in result["stock_ledger_pieces"]] != [-1.0, -2.0]:
		broken.append(f"stock ledger pieces {result['stock_ledger_pieces']}")
	if (qn["qty_m2"], qn["amount"]) != (so["qty_m2"], so["amount"]):
		broken.append(f"quotation {qn['qty_m2']}/{qn['amount']} != sales order {so['qty_m2']}/{so['amount']}")
	if abs(flt(dns[0]["amount"] + dns[1]["amount"], 2) - so["amount"]) > 0.005:
		broken.append("deliveries do not add up to the order amount")
	if abs(flt(sis[0]["amount"] + sis[1]["amount"], 2) - so["amount"]) > 0.005:
		broken.append("invoices do not add up to the order amount")
	if abs(flt(dns[0]["qty_m2"] + dns[1]["qty_m2"], 9) - so["qty_m2"]) > 1e-9:
		broken.append(f"delivered area {dns[0]['qty_m2'] + dns[1]['qty_m2']} != ordered {so['qty_m2']}")
	for before, after in zip(dns, sis, strict=True):
		if (before["qty_m2"], before["amount"]) != (after["qty_m2"], after["amount"]):
			broken.append(f"{before['doc']} and {after['doc']} disagree")
	returns = result["returns"]
	if returns["delivery_return"]["qty_m2"] != returns["credit_note"]["qty_m2"] or returns["delivery_return"]["amount"] != returns["credit_note"]["amount"]:
		broken.append("delivery return and credit note disagree")
	per_piece_amount = flt(so["amount"] / pieces, 2)
	if abs(abs(returns["credit_note"]["amount"]) - per_piece_amount) > 0.005 or abs(abs(returns["delivery_return"]["amount"]) - per_piece_amount) > 0.005:
		broken.append(f"a returned piece is credited {returns['credit_note']['amount']}, but was billed {per_piece_amount}")
	if returns["stock_ledger_pieces"] != 1.0 or result["sales_order_after_return"]["gf_delivered_pieces"] != 2.0:
		broken.append(f"return did not move exactly one piece {returns['stock_ledger_pieces']}/{result['sales_order_after_return']}")
	after = result["sales_order_after"]
	if after["status"] != "Completed" or abs(after["per_delivered"] - 100) > 1e-6 or abs(after["per_billed"] - 100) > 1e-6:
		broken.append(f"sales order did not close {after}")
	if abs(result["spec"]["amount"] - so["amount"]) > 0.005:
		gaps.append(f"specification amount {result['spec']['amount']} != order {so['amount']}")
	exact_area = flt(result["exact_area_per_piece"] * pieces, 6)
	if abs(so["qty_m2"] - exact_area) > 1e-9:
		gaps.append(f"billed area {so['qty_m2']} m2 is not the exact {exact_area} m2")
	info = []
	if abs(result["physical"]["sales_order_row"]["gf_total_area_m2"] - exact_area) > 1e-9:
		info.append(f"informational area column on the order row shows {result['physical']['sales_order_row']['gf_total_area_m2']} (exact {exact_area})")
	if broken:
		frappe.throw("INTEGRITY: " + "; ".join(broken))
	if exact and gaps:
		frappe.throw("NOT EXACT: " + "; ".join(gaps))
	summary = f"consistent across Quotation/Order/Delivery/Invoice/returns: {so['qty_m2']} m2, total {so['amount']} (exact {result['exact_total']})"
	result["findings"] = {"gaps": gaps, "informational": info}
	return summary + (f"; gaps: {'; '.join(gaps)}" if gaps else "; exact") + (f"; note: {'; '.join(info)}" if info else "")


def billing_precision_system3() -> str:
	result = billing_roundtrip("system3")
	return _billing_checks(result, exact=False)


def billing_precision_rows6() -> str:
	result = billing_roundtrip("rows6")
	return _billing_checks(result, exact=True)


def billing_precision_system6() -> str:
	result = billing_roundtrip("system6")
	return _billing_checks(result, exact=True)


def billing_precision_rows2() -> str:
	result = billing_roundtrip("rows2")
	return _billing_checks(result, exact=False)


def billing_rule_change_with_open_order() -> str:
	"""An order taken under one billing rule is still delivered, invoiced, and returned under it after the rule changes."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_credit_note
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as make_delivery_return

	set_precision_config("system3")
	try:
		set_capitalization(False)
		currency = v.company_currency()
		batch_no = v.raw_batch(sheets=1, rate=100)
		spec = make_spec_ops(currency, "GFV Selling", 3, length=1234, width=567, manual_rate=500)
		_quotation, so = v.quote_and_order(spec)
		job = v.cut(v.cutting_job_for(so, batch_no))
		processing = v.process(job)
		final_no = v.final_batch(processing)
		old_factor = flt(so.items[0].conversion_factor, 9)

		set_precision_config("rows6")  # the client changes the rule while this order is still open
		item_factor = flt(ensure_factor(spec.final_item_code), 9)
		expect(abs(item_factor - old_factor) > 1e-6, "the rule change should have moved the item's conversion")
		dn = v.delivery_for(so, typed_qty=0.7, batch_no=final_no)
		invoice = v.invoice_for(dn)
		ret = frappe.get_doc(make_delivery_return(dn.name))
		ret.items[0].qty = -0.7
		ret.items[0].use_serial_batch_fields, ret.items[0].batch_no = 1, final_no
		ret.insert(ignore_permissions=True)
		ret.submit()
		credit = frappe.get_doc(make_credit_note(invoice.name))
		credit.insert(ignore_permissions=True)
		credit.submit()
		expect(near(dn.items[0].conversion_factor, old_factor, 9) and near(dn.items[0].qty, 0.7, 9), f"delivery left the order's rule: {dn.items[0].conversion_factor}/{dn.items[0].qty}")
		expect(near(dn.items[0].amount, 350, 2) and near(invoice.items[0].amount, 350, 2), "open order should keep billing 0.700 m2 per piece")
		expect(near(ret.items[0].qty, -0.7, 9) and near(credit.items[0].amount, -350, 2), f"return/credit {ret.items[0].qty}/{credit.items[0].amount}")

		# A new order after the change uses the new rule.
		spec_new = make_spec_ops(currency, "GFV Selling", 3, length=1234, width=567, manual_rate=500)
		_q, so_new = v.quote_and_order(spec_new)
		expect(near(so_new.items[0].qty, 2.099034, 9) and near(so_new.items[0].amount, 1049.52, 2), f"new order {so_new.items[0].qty}/{so_new.items[0].amount}")
		EVIDENCE["billing_rule_change"] = {
			"open_order": so.name,
			"old_factor": old_factor,
			"new_factor": item_factor,
			"delivery": _doc_line(dn),
			"invoice": _doc_line(invoice),
			"return": _doc_line(ret),
			"credit_note": _doc_line(credit),
			"new_order": _doc_line(so_new),
		}
		frappe.db.commit()
		return f"open order {so.name} stayed at {old_factor} pieces/m2 (0.700 m2 per piece); new order {so_new.name} bills {so_new.items[0].qty} m2"
	finally:
		set_precision_config("system3")


def ensure_factor(item_code: str) -> float:
	from glass_factory.glass_factory.selling_uom import ensure_item_area_conversion

	return ensure_item_area_conversion(item_code)


SWEEP_SIZES = ((1234, 567), (1200, 800), (999, 777), (1501, 1003), (2440, 1830), (610, 305), (333, 333), (1000, 1000))
SWEEP_PIECES = (1, 2, 7, 25, 100)
SWEEP_RATE = 500


def billing_sweep_exact() -> str:
	"""Draft Quotations for many sizes and piece counts: typed totals must always come back as whole pieces."""
	return _billing_sweep("rows6", exact=True)


def billing_sweep_default() -> str:
	return _billing_sweep("system3", exact=False)


def _billing_sweep(mode: str, *, exact: bool) -> str:
	set_precision_config(mode)
	try:
		currency = v.company_currency()
		rows = []
		failures = []
		for length, width in SWEEP_SIZES:
			spec = make_spec_ops(currency, "GFV Selling", 1, length=length, width=width, polish=0, temper=0, manual_rate=SWEEP_RATE)
			frappe.db.commit()  # keep the specification; only the draft Quotations are discarded
			area = flt(length * width / 1_000_000, 6)
			for pieces in SWEEP_PIECES:
				exact_total = flt(area * pieces * SWEEP_RATE, 2)
				for label, typed in (("exact", flt(area * pieces, 6)), ("rounded to 3", flt(area * pieces, 3))):
					quotation = frappe.get_doc("Quotation", add_spec_to_transaction(spec.name, "Quotation")["name"])
					row = quotation.items[0]
					row.qty = typed
					try:
						quotation.save()
					except Exception as exc:
						rows.append({"size": f"{length}x{width}", "pieces": pieces, "typed": label, "outcome": "rejected", "reason": str(exc)[:90]})
						frappe.db.rollback()
						continue
					quotation.reload()
					row = quotation.items[0]
					billed = flt(row.qty, 9)
					rows.append(
						{
							"size": f"{length}x{width}",
							"pieces": pieces,
							"typed": label,
							"outcome": "accepted",
							"billed_m2": billed,
							"stock_qty": flt(row.stock_qty, 6),
							"amount": flt(row.amount, 2),
							"exact_total": exact_total,
							"difference_vs_exact": flt(row.amount - exact_total, 2),
						}
					)
					if flt(row.stock_qty, 6) != pieces:
						failures.append(f"{length}x{width} x{pieces}: stock qty {row.stock_qty}")
					if exact and label == "exact" and (abs(billed - flt(area * pieces, 6)) > 1e-9 or abs(row.amount - exact_total) > 0.005):
						failures.append(f"{length}x{width} x{pieces}: billed {billed} / {row.amount}, exact {flt(area * pieces, 6)} / {exact_total}")
					frappe.db.rollback()  # drafts only: nothing to keep
		accepted = [r for r in rows if r["outcome"] == "accepted"]
		rejected = [r for r in rows if r["outcome"] == "rejected"]
		summary = {
			"mode": mode,
			"typed_entries": len(rows),
			"accepted": len(accepted),
			"rejected_with_message": len(rejected),
			"max_abs_difference_vs_exact": max((abs(r["difference_vs_exact"]) for r in accepted), default=0),
			"rejected_examples": rejected[:4],
			"differences": [r for r in accepted if r["typed"] == "exact" and abs(r["difference_vs_exact"]) > 0.005][:12],
		}
		EVIDENCE[f"billing_sweep_{mode}"] = summary
		if failures:
			frappe.throw("; ".join(failures[:6]))
		return f"{len(accepted)} accepted, {len(rejected)} rejected with a message, max difference vs exact {summary['max_abs_difference_vs_exact']}"
	finally:
		set_precision_config("system3")


# --------------------------------------------------------------------------- scenario 4: standard stock and reversal flows

OTHER_WAREHOUSE = "Stores - OGC"


def stock_rows_for(voucher_type: str, voucher_no: str) -> list[dict]:
	"""Physical rows of a voucher, from the Stock Ledger and the batch bundles behind it."""
	return sle_rows(voucher_type, voucher_no)


def reconcile(*, purpose: str, rows: list[dict], difference_account: str):
	doc = frappe.new_doc("Stock Reconciliation")
	doc.company, doc.purpose, doc.posting_date = COMPANY, purpose, today()
	doc.expense_account = difference_account
	doc.cost_center = frappe.get_cached_value("Company", COMPANY, "cost_center")
	for row in rows:
		doc.append("items", {"use_serial_batch_fields": 1, **row})
	doc.insert(ignore_permissions=True)
	doc.submit()
	doc.reload()
	return doc


def opening_stock_reconciliation_and_transfer() -> str:
	"""Opening stock, a Stock Reconciliation adjustment, and a warehouse transfer of a batch-tracked sheet."""
	acc = accounts()
	raw_wh = v.settings().raw_warehouse
	temporary_opening = frappe.db.get_value("Account", {"company": COMPANY, "account_type": "Temporary", "is_group": 0}, "name")
	start = balances()
	# ERPNext only accepts Opening Stock for an Item with no earlier transaction, so use a sheet size nobody has touched.
	length = 3000
	while frappe.db.exists("Item", f"GLS-CLEAR-6MM-{length}X2000"):
		length += 1
	opening_item = f"GLS-CLEAR-6MM-{length}X2000"
	v._ensure_raw_item(opening_item, v.settings().raw_item_group, COMPANY, raw_wh)
	batch_no = v._batch(opening_item, "OPEN")
	frappe.db.commit()

	opening = reconcile(
		purpose="Opening Stock",
		rows=[{"item_code": opening_item, "warehouse": raw_wh, "qty": 5, "valuation_rate": 80, "batch_no": batch_no}],
		difference_account=temporary_opening,
	)
	gl_opening = gl_by_account("Stock Reconciliation", opening.name)
	expect(near(batch_balance(batch_no, raw_wh)["qty"], 5) and near(batch_balance(batch_no, raw_wh)["value"], 400, 2), f"opening batch {batch_balance(batch_no, raw_wh)}")
	expect(near(gl_opening.get(acc["default_inventory_account"], 0), 400, 2) and near(gl_opening.get(temporary_opening, 0), -400, 2), f"opening GL {gl_opening}")
	expect(len(stock_rows_for("Stock Reconciliation", opening.name)) == 1, "opening stock should post one ledger row")

	# Count correction: one sheet is missing, so the batch is reconciled down to 4 at the same valuation.
	adjustment = reconcile(
		purpose="Stock Reconciliation",
		rows=[{"item_code": opening_item, "warehouse": raw_wh, "qty": 4, "valuation_rate": 80, "batch_no": batch_no}],
		difference_account=acc["stock_adjustment_account"],
	)
	gl_adjustment = gl_by_account("Stock Reconciliation", adjustment.name)
	expect(near(batch_balance(batch_no, raw_wh)["qty"], 4) and near(batch_balance(batch_no, raw_wh)["value"], 320, 2), f"after count {batch_balance(batch_no, raw_wh)}")
	expect(near(gl_adjustment.get(acc["stock_adjustment_account"], 0), 80, 2) and near(gl_adjustment.get(acc["default_inventory_account"], 0), -80, 2), f"adjustment GL {gl_adjustment}")

	# Transfer two sheets to another warehouse: one ledger row out, one in, no change in value.
	transfer = frappe.new_doc("Stock Entry")
	transfer.stock_entry_type = transfer.purpose = "Material Transfer"
	transfer.company, transfer.posting_date = COMPANY, today()
	transfer.append("items", {"item_code": opening_item, "s_warehouse": raw_wh, "t_warehouse": OTHER_WAREHOUSE, "qty": 2, "batch_no": batch_no, "use_serial_batch_fields": 1})
	transfer.insert(ignore_permissions=True)
	transfer.submit()
	rows = stock_rows_for("Stock Entry", transfer.name)
	gl_transfer = gl_by_account("Stock Entry", transfer.name)
	expect(sorted(flt(r["actual_qty"]) for r in rows) == [-2.0, 2.0], f"transfer ledger rows {rows}")
	expect(abs(sle_value("Stock Entry", transfer.name)) < 0.01 and not gl_transfer, f"transfer should not change value or any account: {gl_transfer}")
	expect(near(batch_balance(batch_no, raw_wh)["qty"], 2) and near(batch_balance(batch_no, OTHER_WAREHOUSE)["qty"], 2), "batch should now be split 2 / 2")
	expect(near(batch_balance(batch_no, OTHER_WAREHOUSE)["value"], 160, 2), "transferred sheets must keep their valuation")

	delta = balance_delta(start, balances())
	expect(near(delta.get(acc["default_inventory_account"], 0), 320, 2), f"inventory movement {delta}")
	expect(abs(stock_reconciliation_gap()) < 0.01, f"inventory GL and Stock Ledger disagree by {stock_reconciliation_gap()}")
	EVIDENCE["standard_stock_flows_opening_count_transfer"] = {
		"batch": batch_no,
		"opening_stock_reconciliation": {"name": opening.name, "gl": gl_opening, "sle": stock_rows_for("Stock Reconciliation", opening.name)},
		"count_adjustment": {"name": adjustment.name, "gl": gl_adjustment, "sle": stock_rows_for("Stock Reconciliation", adjustment.name)},
		"transfer": {"name": transfer.name, "gl": gl_transfer, "sle": rows},
		"account_changes": delta,
		"batch_balances": {raw_wh: batch_balance(batch_no, raw_wh), OTHER_WAREHOUSE: batch_balance(batch_no, OTHER_WAREHOUSE)},
	}
	frappe.db.commit()
	return f"opening {opening.name} (+5 @80), count {adjustment.name} (-1), transfer {transfer.name} (2 sheets); inventory change {delta.get(acc['default_inventory_account'])}"



def _first_row(rows: list[dict], *, positive: bool) -> dict:
	return next(row for row in rows if (flt(row["actual_qty"]) > 0) == positive)


def supplier_return_and_debit_note() -> str:
	"""Receive two batched sheets, bill them, send one back, and debit the supplier for it."""
	from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import make_debit_note
	from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_return

	acc = accounts()
	raw_wh = v.settings().raw_warehouse
	start = balances()
	purchase = purchase_cycle([(1, 100), (1, 100)], bill=True)
	pr = frappe.get_doc("Purchase Receipt", purchase["pr"])
	pi = frappe.get_doc("Purchase Invoice", purchase["pi"])
	returned_batch, kept_batch = purchase["batches"][1], purchase["batches"][0]

	ret = frappe.get_doc(make_purchase_return(pr.name))
	ret.items = [row for row in ret.items if row.purchase_receipt_item == pr.items[1].name]
	row = ret.items[0]
	row.qty, row.use_serial_batch_fields, row.batch_no = -1, 1, returned_batch
	row.serial_and_batch_bundle = None
	ret.insert(ignore_permissions=True)
	ret.submit()
	ret.reload()
	ledger = sle_rows("Purchase Receipt", ret.name)
	expect([flt(r["actual_qty"]) for r in ledger] == [-1.0], f"the return must move one sheet exactly once: {ledger}")
	expect(near(batch_balance(returned_batch, raw_wh)["qty"], 0) and near(batch_balance(kept_batch, raw_wh)["qty"], 1), "wrong batch returned")
	gl_return = gl_by_account("Purchase Receipt", ret.name)
	expect(near(gl_return.get(acc["stock_received_but_not_billed"], 0), 100, 2) and near(gl_return.get(acc["default_inventory_account"], 0), -100, 2), f"return GL {gl_return}")

	note = frappe.get_doc(make_debit_note(pi.name))
	note.items = [row for row in note.items if row.pr_detail == pr.items[1].name]
	note.items[0].qty = -1
	note.insert(ignore_permissions=True)
	note.submit()
	note.reload()
	expect(not sle_rows("Purchase Invoice", note.name), "a debit note against a receipt must not move stock a second time")
	gl_note = gl_by_account("Purchase Invoice", note.name)
	expect(near(gl_note.get(acc["default_payable_account"], 0), 100, 2) and near(gl_note.get(acc["stock_received_but_not_billed"], 0), -100, 2), f"debit note GL {gl_note}")

	delta = balance_delta(start, balances())
	expect(abs(delta.get(acc["stock_received_but_not_billed"], 0)) < 0.01, f"SRBNB not cleared: {delta}")
	expect(near(delta.get(acc["default_payable_account"], 0), -100, 2) and near(delta.get(acc["default_inventory_account"], 0), 100, 2), f"net effect {delta}")
	expect(abs(stock_reconciliation_gap()) < 0.01, "inventory GL and Stock Ledger disagree")
	EVIDENCE["standard_stock_flows_supplier_return"] = {
		"purchase_order": purchase["po"],
		"purchase_receipt": pr.name,
		"purchase_invoice": pi.name,
		"purchase_return": {"name": ret.name, "sle": ledger, "gl": gl_return},
		"debit_note": {"name": note.name, "total": flt(note.grand_total, 2), "gl": gl_note},
		"net_account_changes": delta,
		"payable_outstanding": {"purchase_invoice": outstanding("Purchase Invoice", pi.name), "debit_note": outstanding("Purchase Invoice", note.name)},
	}
	frappe.db.commit()
	return f"{pr.name} returned by {ret.name}, debited by {note.name}; payable change {delta.get(acc['default_payable_account'])}, SRBNB change {delta.get(acc['stock_received_but_not_billed'], 0)}"


def customer_partial_return_and_credit_note() -> str:
	"""Deliver and invoice two pieces, take one back, credit it, and settle the rest."""
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return as make_credit_note
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return as make_delivery_return

	set_capitalization(False)
	acc = accounts()
	final_wh = v.settings().final_goods_warehouse
	currency = v.company_currency()
	start = balances()
	batch_no = v._batch(v.RAW, "RET")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 100, batch_no)
	spec = make_spec_ops(currency, "GFV Selling", 2, length=1200, width=800, polish=1, temper=0)
	_quotation, so = v.quote_and_order(spec)
	job = v.cut(v.cutting_job_for(so, batch_no))
	processing = v.process(job)
	final_no = v.final_batch(processing)
	dn = deliver_line(so, spec.final_item_code, final_no, pieces=2)
	invoice = v.invoice_for(dn)
	expect(near(gl_by_account("Delivery Note", dn.name).get(acc["default_expense_account"], 0), 100, 2), "two pieces cost 100")

	ret = frappe.get_doc(make_delivery_return(dn.name))
	ret.items[0].qty = -0.96
	ret.items[0].use_serial_batch_fields, ret.items[0].batch_no = 1, final_no
	ret.insert(ignore_permissions=True)
	ret.submit()
	ret.reload()
	return_ledger = sle_rows("Delivery Note", ret.name)
	expect([flt(r["actual_qty"]) for r in return_ledger] == [1.0], f"the return must move one piece exactly once: {return_ledger}")
	gl_return = gl_by_account("Delivery Note", ret.name)
	expect(near(gl_return.get(acc["default_inventory_account"], 0), 50, 2) and near(gl_return.get(acc["default_expense_account"], 0), -50, 2), f"return GL {gl_return}")

	credit = frappe.get_doc(make_credit_note(invoice.name))
	credit.items[0].qty = -0.96
	# ERPNext opens a credit note with "Update Outstanding for Self" on: it then floats as its own negative
	# balance and the original invoice stays fully open until the two are reconciled. Applying the credit
	# to the original invoice is what makes the customer's net payment settle both.
	default_update_outstanding_for_self = frappe.utils.cint(credit.update_outstanding_for_self)
	credit.update_outstanding_for_self = 0
	credit.insert(ignore_permissions=True)
	credit.submit()
	credit.reload()
	expect(not sle_rows("Sales Invoice", credit.name), "the credit note must not move stock a second time")
	gl_credit = gl_by_account("Sales Invoice", credit.name)
	expect(near(gl_credit.get(acc["default_income_account"], 0), 480, 2) and near(gl_credit.get(acc["default_receivable_account"], 0), -480, 2), f"credit note GL {gl_credit}")
	expect(near(batch_balance(final_no, final_wh)["qty"], 1) and near(batch_balance(final_no, final_wh)["value"], 50, 2), f"stock after return {batch_balance(final_no, final_wh)}")

	invoice.reload()
	outstanding_before_payment = {"invoice": outstanding("Sales Invoice", invoice.name), "credit_note": outstanding("Sales Invoice", credit.name)}
	expect(near(outstanding_before_payment["invoice"], 480, 2), f"the credit should leave 480 open on the invoice: {outstanding_before_payment}")
	payment = customer_payment(invoice.name)
	delta = balance_delta(start, balances())
	expect(near(delta.get(acc["default_receivable_account"], 0), 0, 2), f"receivable not settled: {delta}")
	expect(near(delta.get(acc["default_income_account"], 0), -480, 2) and near(delta.get(acc["default_expense_account"], 0), 50, 2), f"net revenue/COGS {delta}")
	expect(abs(stock_reconciliation_gap()) < 0.01, "inventory GL and Stock Ledger disagree")
	so.reload()
	expect(near(so.items[0].gf_delivered_qty, 1), f"delivered pieces after return {so.items[0].gf_delivered_qty}")
	EVIDENCE["standard_stock_flows_customer_return"] = {
		"sales_order": so.name,
		"delivery_note": dn.name,
		"sales_invoice": invoice.name,
		"delivery_return": {"name": ret.name, "sle": return_ledger, "gl": gl_return},
		"credit_note": {"name": credit.name, "total": flt(credit.grand_total, 2), "gl": gl_credit},
		"erpnext_default_update_outstanding_for_self": default_update_outstanding_for_self,
		"outstanding_before_payment": outstanding_before_payment,
		"payment_entry": payment,
		"net_account_changes": delta,
		"delivered_pieces_on_order_row": flt(so.items[0].gf_delivered_qty, 3),
	}
	frappe.db.commit()
	return f"{dn.name} returned by {ret.name} (1 piece back at 50), credited by {credit.name} (-480); receivable settled by {payment}"


# --------------------------------------------------------------------------- scenario 4b: direct invoices that update stock


def direct_sales_invoice_with_stock_update() -> str:
	"""A Sales Invoice with Update Stock delivers glass too, so it must obey the same limits as a Delivery Note."""
	from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

	set_capitalization(False)
	acc = accounts()
	currency = v.company_currency()
	final_wh = v.settings().final_goods_warehouse
	start = balances()

	def direct_invoice(so, batch_no, *, pieces, update_stock=1):
		invoice = frappe.get_doc(make_sales_invoice(so.name))
		invoice.update_stock = update_stock
		row = invoice.items[0]
		row.qty = flt(pieces / flt(row.conversion_factor), 9)
		row.warehouse = final_wh
		row.batch_no, row.use_serial_batch_fields = batch_no, 1
		invoice.set_warehouse = final_wh
		invoice.insert(ignore_permissions=True)
		return invoice

	# Order A is fully produced. Order B (same finished Item) has not been cut at all.
	batch_a = v._batch(v.RAW, "DIRA")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 100, batch_a)
	spec_a = make_spec_ops(currency, "GFV Selling", 2, length=1200, width=800, polish=1, temper=0)
	_q, so_a = v.quote_and_order(spec_a)
	job_a = v.cut(v.cutting_job_for(so_a, batch_a))
	processing_a = v.process(job_a)
	final_batch_no = v.final_batch(processing_a)
	spec_b = make_spec_ops(currency, "GFV Selling", 2, length=1200, width=800, polish=1, temper=0)
	_q, so_b = v.quote_and_order(spec_b)
	expect(spec_a.final_item_code == spec_b.final_item_code, "both orders must sell the same finished Item")

	# 1. Order B has nothing processed: the other order's stock must not be delivered against it.
	refused = expect_failure(lambda: direct_invoice(so_b, final_batch_no, pieces=1).submit(), contains="process")
	expect(near(batch_balance(final_batch_no, final_wh)["qty"], 2), "the refused invoice moved stock")

	# 2. Order A is processed: one piece goes out on a direct invoice.
	first = direct_invoice(so_a, final_batch_no, pieces=1)
	first.submit()
	first.reload()
	ledger = sle_rows("Sales Invoice", first.name)
	expect([flt(r["actual_qty"]) for r in ledger] == [-1.0], f"direct invoice should move one piece exactly once: {ledger}")
	gl_first = gl_by_account("Sales Invoice", first.name)
	expect(near(gl_first.get(acc["default_expense_account"], 0), 50, 2) and near(gl_first.get(acc["default_inventory_account"], 0), -50, 2), f"COGS/inventory on the invoice {gl_first}")
	expect(near(gl_first.get(acc["default_income_account"], 0), -480, 2), f"revenue {gl_first}")
	row = frappe.db.get_value("Sales Order Item", so_a.items[0].name, ["delivered_qty", "gf_delivered_qty"], as_dict=True)
	expect(near(row.gf_delivered_qty, 1) and near(row.delivered_qty, 0.96), f"the order row must show the delivered piece: {row}")

	# 3. A Delivery Note cannot hand over more than what is left, because the direct invoice already delivered one.
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note as dn_from_order

	def deliver_two():
		dn = frappe.get_doc(dn_from_order(so_a.name))
		dn.items[0].qty = flt(2 / flt(dn.items[0].conversion_factor), 9)  # both pieces, though one already left
		dn.items[0].batch_no, dn.items[0].use_serial_batch_fields = final_batch_no, 1
		dn.insert(ignore_permissions=True)
		dn.submit()

	expect_failure(deliver_two, contains="exceed")

	# 4. Cancelling the invoice gives the piece back to the order row.
	first.cancel()
	row = frappe.db.get_value("Sales Order Item", so_a.items[0].name, ["delivered_qty", "gf_delivered_qty"], as_dict=True)
	expect(near(row.gf_delivered_qty, 0) and near(row.delivered_qty, 0), f"cancel did not release the piece: {row}")
	expect(near(batch_balance(final_batch_no, final_wh)["qty"], 2), "cancel did not return the stock")

	# 5. The same piece, now through a Delivery Note and an invoice that does not touch stock, as the standard flow.
	dn = deliver_line(so_a, spec_a.final_item_code, final_batch_no, pieces=2)
	invoice = v.invoice_for(dn)
	expect(not sle_rows("Sales Invoice", invoice.name), "an invoice made from a Delivery Note must not move stock again")
	delta = balance_delta(start, balances())
	expect(abs(stock_reconciliation_gap()) < 0.01, "inventory GL and Stock Ledger disagree")
	EVIDENCE["direct_sales_invoice_update_stock"] = {
		"order_without_production_refused": refused[:160],
		"direct_invoice": {"name": first.name, "sle": ledger, "gl": gl_first},
		"order_row_after_invoice": {"gf_delivered_pieces": 1.0, "delivered_m2": 0.96},
		"standard_flow": {"delivery_note": dn.name, "sales_invoice": invoice.name},
		"net_account_changes": delta,
	}
	frappe.db.commit()
	return f"direct invoice {first.name} moved 1 piece (COGS 50, revenue 480); invoice for an unprocessed order refused; cancel released the piece"


def direct_purchase_invoice_with_stock_update() -> str:
	"""A Purchase Invoice with Update Stock receives the sheets itself: no Inventory Received But Not Billed."""
	acc = accounts()
	raw_wh = v.settings().raw_warehouse
	ensure_supplier()
	start = balances()
	batch_no = v._batch(v.RAW, "DPI")
	pi = frappe.new_doc("Purchase Invoice")
	pi.company, pi.supplier, pi.posting_date = COMPANY, SUPPLIER, today()
	pi.currency, pi.conversion_rate, pi.buying_price_list = v.company_currency(), 1, "GFV Buying"
	pi.update_stock, pi.set_warehouse = 1, raw_wh
	pi.bill_no, pi.bill_date = f"DIR-{frappe.generate_hash(length=6)}", today()
	pi.append("items", {"item_code": v.RAW, "qty": 2, "rate": 110, "warehouse": raw_wh, "batch_no": batch_no, "use_serial_batch_fields": 1})
	pi.insert(ignore_permissions=True)
	pi.submit()
	pi.reload()
	ledger = sle_rows("Purchase Invoice", pi.name)
	expect([flt(r["actual_qty"]) for r in ledger] == [2.0], f"the invoice must receive 2 sheets exactly once: {ledger}")
	gl = gl_by_account("Purchase Invoice", pi.name)
	expect(near(gl.get(acc["default_inventory_account"], 0), 220, 2) and near(gl.get(acc["default_payable_account"], 0), -220, 2), f"GL {gl}")
	expect(not gl.get(acc["stock_received_but_not_billed"]), "a direct invoice must not touch Inventory Received But Not Billed")
	expect(near(batch_balance(batch_no, raw_wh)["value"], 220, 2), "batch valuation")
	payment = supplier_payment("Purchase Invoice", pi.name)
	expect(near(outstanding("Purchase Invoice", pi.name), 0, 2), "supplier invoice not settled")
	delta = balance_delta(start, balances())
	expect(abs(delta.get(acc["default_payable_account"], 0)) < 0.01 and abs(delta.get(acc["stock_received_but_not_billed"], 0)) < 0.01, f"payable / SRBNB {delta}")
	expect(abs(stock_reconciliation_gap()) < 0.01, "inventory GL and Stock Ledger disagree")
	EVIDENCE["direct_purchase_invoice_update_stock"] = {"purchase_invoice": pi.name, "sle": ledger, "gl": gl, "payment_entry": payment, "net_account_changes": delta}
	frappe.db.commit()
	return f"{pi.name} received 2 sheets at 220 without SRBNB; paid by {payment}"


# --------------------------------------------------------------------------- scenario 5b: USD receivable configurations

USD_CUSTOMER = "GFV Customer USD"


def _account_under(parent_of: str, *, name: str, number: str, account_type: str, currency: str) -> str:
	existing = frappe.db.get_value("Account", {"company": COMPANY, "account_number": number}, "name")
	if existing:
		return existing
	account = frappe.new_doc("Account")
	account.account_name, account.account_number, account.company = name, number, COMPANY
	account.parent_account = frappe.db.get_value("Account", parent_of, "parent_account")
	account.account_type, account.account_currency = account_type, currency
	account.insert(ignore_permissions=True)
	return account.name


def ensure_usd_accounts() -> dict:
	acc = accounts()
	return {
		"receivable": _account_under(acc["default_receivable_account"], name="Accounts Receivable USD", number="11211", account_type="Receivable", currency="USD"),
		"bank": _account_under(acc["default_bank_account"], name="Checking Account USD", number="11132", account_type="Bank", currency="USD"),
	}


def ensure_usd_customer() -> str:
	usd = ensure_usd_accounts()
	if not frappe.db.exists("Customer", USD_CUSTOMER):
		customer = frappe.new_doc("Customer")
		customer.customer_name, customer.customer_group, customer.territory = USD_CUSTOMER, "Commercial", "All Territories"
		customer.default_currency = "USD"
		customer.append("accounts", {"company": COMPANY, "account": usd["receivable"]})
		customer.insert(ignore_permissions=True)
	frappe.db.commit()
	return USD_CUSTOMER


def party_ledger(invoice_name: str, party: str) -> list[dict]:
	"""Every GL line of the customer, in company currency, account currency and the invoice's own currency."""
	return frappe.db.sql(
		"""select voucher_type, voucher_no, account, round(debit,2) debit, round(credit,2) credit,
		round(debit_in_account_currency,2) debit_acc, round(credit_in_account_currency,2) credit_acc,
		transaction_currency, round(debit_in_transaction_currency,2) debit_txn, round(credit_in_transaction_currency,2) credit_txn
		from `tabGL Entry` where party=%s and is_cancelled=0 and (voucher_no=%s or against_voucher=%s)
		order by creation""",
		(party, invoice_name, invoice_name),
		as_dict=True,
	)


def usd_receivable_cycle(mode: str, payment_rate: float) -> str:
	"""The agreed example sold in USD, then settled by Payment Entry at payment_rate TZS per USD.

	mode "company_account": the customer uses the company's TZS receivable account, which ERPNext only
	allows for a USD invoice when Accounts Settings > Allow multi-currency invoices against single party account is on.
	mode "usd_party_account": the customer has its own USD receivable account (a Party Account row) and is paid into a USD bank.
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	acc = accounts()
	usd = ensure_usd_accounts()
	flag = frappe.db.get_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account")
	if mode == "company_account":
		customer = "GFV Customer"
		frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", 1)
	else:
		customer = ensure_usd_customer()
		frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", 0)
	frappe.db.commit()
	try:
		label = f"agreed_example_usd_{mode}_paid_at_{int(payment_rate)}"
		receivable_account = acc["default_receivable_account"] if mode == "company_account" else usd["receivable"]
		balance_before = account_balance(receivable_account, party=customer)
		result = agreed_example(label, currency="USD", price_list="GFV Selling USD", customer=customer, pay_customer=False)
		invoice = frappe.get_doc("Sales Invoice", result["documents"]["sales_invoice"])
		before_payment = {
			"debit_to": invoice.debit_to,
			"debit_to_currency": frappe.db.get_value("Account", invoice.debit_to, "account_currency"),
			"invoice_currency": invoice.currency,
			"conversion_rate": flt(invoice.conversion_rate, 4),
			"grand_total_usd": flt(invoice.grand_total, 2),
			"base_grand_total_tzs": flt(invoice.base_grand_total, 2),
			"outstanding_amount_as_stored": flt(invoice.outstanding_amount, 2),
			"outstanding_amount_currency": invoice.currency if mode == "usd_party_account" else "TZS (the receivable account's currency)",
			"net_total_before_tax_usd": flt(invoice.net_total, 2),
		}
		expect(near(invoice.net_total, 480, 2), f"revenue before tax {invoice.net_total}")
		pe = get_payment_entry("Sales Invoice", invoice.name)
		default_pe = {k: pe.get(k) for k in ("paid_from", "paid_to", "paid_from_account_currency", "paid_to_account_currency", "paid_amount", "received_amount", "source_exchange_rate", "target_exchange_rate")}
		if mode == "usd_party_account":
			pe.paid_to, pe.paid_to_account_currency = usd["bank"], "USD"
			pe.source_exchange_rate = pe.target_exchange_rate = payment_rate
			pe.paid_amount = pe.received_amount = flt(invoice.grand_total, 2)
		else:
			# The receivable is a TZS ledger: ERPNext carries the invoice at TZS 1,200,000 (its USD 480 at the invoice
			# rate), so the customer is asked for TZS. At a different rate the customer pays TZS 480 x rate.
			pe.paid_amount = pe.received_amount = flt(invoice.grand_total * payment_rate, 2)
		pe.reference_no, pe.reference_date = f"USD-{invoice.name}", today()
		pe.insert(ignore_permissions=True)
		pe.submit()
		pe.reload()
		invoice.reload()
		gl_pe = gl_by_account("Payment Entry", pe.name)
		after_payment = {
			"payment_entry": pe.name,
			"payment_entry_as_created": default_pe,
			"paid_amount": flt(pe.paid_amount, 2),
			"received_amount": flt(pe.received_amount, 2),
			"allocated": [{"invoice": r.reference_name, "allocated_usd": flt(r.allocated_amount, 2), "exchange_gain_loss_tzs": flt(r.get("exchange_gain_loss"), 2)} for r in pe.references],
			"deductions": [{"account": d.account, "amount": flt(d.amount, 2), "is_exchange_gain_loss": d.is_exchange_gain_loss} for d in pe.deductions],
			"invoice_outstanding_usd": flt(invoice.outstanding_amount, 2),
			"invoice_status": invoice.status,
			"gl": gl_pe,
			"party_ledger": party_ledger(invoice.name, customer),
		}
		receivable_net = flt(sum(flt(r["debit"]) - flt(r["credit"]) for r in after_payment["party_ledger"]), 2)
		# Net in the receivable account's own currency: TZS for the company account, USD for the party account.
		account_net = flt(sum(flt(r["debit_acc"]) - flt(r["credit_acc"]) for r in after_payment["party_ledger"]), 2)
		# ERPNext books the rate difference on a USD receivable as its own Journal Entry against the invoice.
		fx_entries = [r["voucher_no"] for r in after_payment["party_ledger"] if r["voucher_type"] == "Journal Entry"]
		fx_gain = flt(sum(-gl_by_account("Journal Entry", name).get(acc["exchange_gain_loss_account"], 0) for name in fx_entries), 2)
		unallocated = flt(pe.unallocated_amount, 2)
		customer_balance = flt(account_balance(receivable_account, party=customer) - balance_before, 2)
		result["usd_receivable"] = {
			"mode": mode,
			"payment_rate": payment_rate,
			"setting_allow_multi_currency_single_account": 1 if mode == "company_account" else 0,
			"before_payment": before_payment,
			"after_payment": after_payment,
			"exchange_gain_loss_journal_entries": fx_entries,
			"exchange_gain_tzs": fx_gain,
			"unallocated_customer_credit_tzs": unallocated,
			"customer_balance_change_tzs": customer_balance,
			"customer_ledger_net_tzs": receivable_net,
			"customer_ledger_net_in_account_currency": account_net,
		}
		frappe.db.commit()
		expect(near(invoice.outstanding_amount, 0, 2) and invoice.status == "Paid", f"invoice not settled: {invoice.outstanding_amount} {invoice.status}")
		if mode == "usd_party_account":
			expected_fx = flt(480 * (payment_rate - flt(invoice.conversion_rate)), 2)
			expect(near(fx_gain, expected_fx, 2), f"exchange gain {fx_gain}, expected {expected_fx}")
			expect(near(receivable_net, 0, 2) and near(account_net, 0, 2), f"customer ledger not cleared: TZS {receivable_net}, account currency {account_net}")
		else:
			# A TZS receivable cannot see the rate move: the extra TZS paid is unallocated customer credit.
			expected_extra = flt(480 * (payment_rate - flt(invoice.conversion_rate)), 2)
			expect(near(fx_gain, 0, 2), f"a company-currency receivable should book no exchange difference, got {fx_gain}")
			expect(near(unallocated, expected_extra, 2), f"unallocated credit {unallocated}, expected {expected_extra}")
			expect(near(customer_balance, -expected_extra, 2), f"customer balance {customer_balance}, expected a credit of {expected_extra}")
		return (
			f"{invoice.name}: debit_to {invoice.debit_to} ({before_payment['debit_to_currency']}); {pe.name} at {payment_rate}; "
			f"FX gain {fx_gain} TZS; unallocated credit {unallocated} TZS"
		)
	finally:
		frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", flag or 0)
		frappe.db.commit()


def usd_company_account_same_rate() -> str:
	return usd_receivable_cycle("company_account", 2500)


def usd_company_account_rate_moves() -> str:
	return usd_receivable_cycle("company_account", 2600)


def usd_party_account_same_rate() -> str:
	return usd_receivable_cycle("usd_party_account", 2500)


def usd_party_account_rate_moves() -> str:
	return usd_receivable_cycle("usd_party_account", 2600)


def usd_company_account_without_setting() -> str:
	"""A customer with no ledger history cannot get a USD invoice on the company's TZS receivable account by default.

	ERPNext applies this rule only until the customer has a first ledger entry in any currency, so the same
	invoice goes through for an existing customer. That is why the receivable policy has to be chosen explicitly.
	"""
	flag = frappe.db.get_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account")
	frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", 0)
	frappe.db.commit()
	try:
		fresh = frappe.new_doc("Customer")
		fresh.customer_name = f"GFV New Customer {frappe.generate_hash(length=5)}"
		fresh.customer_group, fresh.territory = "Commercial", "All Territories"
		fresh.insert(ignore_permissions=True)
		dn = _usd_delivery_for_invoice_probe(fresh.name)
		frappe.db.commit()
		message = expect_failure(lambda: v.invoice_for(dn), contains="currency")
		existing_ok = _usd_delivery_for_invoice_probe("GFV Customer")
		invoice = v.invoice_for(existing_ok)
		EVIDENCE["usd_company_account_without_setting"] = {
			"new_customer_refusal": message[:300],
			"existing_customer_invoice_accepted": invoice.name,
			"accounts_setting_allow_multi_currency_single_account": 0,
		}
		frappe.db.commit()
		return f"new customer refused ({message[:80]}); existing customer invoiced as {invoice.name}"
	finally:
		frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", flag or 0)
		frappe.db.commit()


def _usd_delivery_for_invoice_probe(customer: str):
	set_capitalization(False)
	batch_no = v._batch(v.RAW, "USDP")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 100, batch_no)
	spec = make_spec_ops("USD", "GFV Selling USD", 2, length=1200, width=800, polish=1, temper=0, customer=customer)
	_q, so = v.quote_and_order(spec)
	job = v.cut(v.cutting_job_for(so, batch_no))
	processing = v.process(job)
	final_no = v.final_batch(processing)
	return deliver_line(so, spec.final_item_code, final_no, pieces=1)


# --------------------------------------------------------------------------- scenario 3c: landed cost over several batches


def landed_cost_across_batches() -> str:
	"""One receipt, two batches at different prices: the voucher splits by amount and each batch keeps its own cost."""
	acc = accounts()
	raw_wh = v.settings().raw_warehouse
	start = balances()
	purchase = purchase_cycle([(1, 100), (1, 300)], bill=True, pay=True)
	first, second = purchase["batches"]
	before = {first: batch_balance(first, raw_wh), second: batch_balance(second, raw_wh)}
	lcv = landed_cost(purchase["pr"], 40, distribute="Amount")
	freight = freight_invoice(40)
	reposts = run_pending_reposts()
	after = {first: batch_balance(first, raw_wh), second: batch_balance(second, raw_wh)}
	expect(near(before[first]["value"], 100, 2) and near(before[second]["value"], 300, 2), f"receipt valuation {before}")
	# 40 shared by amount (100 : 300) is 10 and 30, so the batches stand at 110 and 330.
	expect(near(after[first]["value"], 110, 2) and near(after[second]["value"], 330, 2), f"after landed cost {after}")
	gl_pr = gl_by_account("Purchase Receipt", purchase["pr"])
	expect(near(gl_pr.get(acc["default_inventory_account"], 0), 440, 2) and near(gl_pr.get(acc["stock_received_but_not_billed"], 0), -400, 2) and near(gl_pr.get(acc["expenses_included_in_valuation"], 0), -40, 2), f"receipt GL {gl_pr}")
	delta = balance_delta(start, balances())
	expect(abs(delta.get(acc["stock_received_but_not_billed"], 0)) < 0.01, f"SRBNB not cleared by the invoice {delta}")
	expect(near(delta.get(acc["default_payable_account"], 0), -40, 2), f"only the unpaid freight should be left payable {delta}")
	expect(abs(stock_reconciliation_gap()) < 0.01, "inventory GL and Stock Ledger disagree")
	EVIDENCE["landed_cost_across_batches"] = {
		"purchase": purchase,
		"landed_cost_voucher": lcv,
		"freight_invoice": freight,
		"reposts_run": len(reposts),
		"batch_value_before": before,
		"batch_value_after": after,
		"receipt_gl": gl_pr,
		"net_account_changes": delta,
		"outstanding": {"goods_invoice": outstanding("Purchase Invoice", purchase["pi"]), "freight_invoice": outstanding("Purchase Invoice", freight)},
	}
	frappe.db.commit()
	return f"{purchase['pr']}: batches 100/300 -> {after[first]['value']}/{after[second]['value']} after {lcv} (40 by amount)"


# --------------------------------------------------------------------------- capitalization account, cost rates, ordinary repack


def _tax_account() -> str | None:
	return frappe.db.get_value(
		"Account",
		{"company": COMPANY, "is_group": 0, "account_name": ["like", "%Deferred Income Tax%"]},
		"name",
	) or frappe.db.get_value("Account", {"company": COMPANY, "is_group": 0, "account_type": "Tax"}, "name")


def capitalization_rejects_unsuitable_account_and_seeded_cost() -> str:
	"""The account seen in testing is refused, a seeded zero blocks posting, and an intentional zero does not."""
	tax = _tax_account()
	expect(bool(tax), "the site has no Deferred Income Tax or other tax account to reject")
	doc = v.settings()
	doc.capitalize_processing_costs = 1
	doc.processing_cost_expense_account = tax
	refused_account = expect_failure(lambda: doc.save(ignore_permissions=True), contains="valuation")
	expect("deferred income tax" in refused_account.lower() or "tax" in refused_account.lower(), refused_account[:240])
	doc.reload()
	expect(not cint_flag(doc.processing_cost_expense_account == tax and doc.capitalize_processing_costs), "the rejected account was saved")

	group = frappe.db.get_value("Account", {"company": COMPANY, "is_group": 1}, "name")
	expect(bool(group), "the site has no group account")
	doc.capitalize_processing_costs = 1
	doc.processing_cost_expense_account = group
	expect_failure(lambda: doc.save(ignore_permissions=True), contains="posting")
	doc.reload()

	set_capitalization(True)
	ensure_cost_rate("Polish", "Per Edge Meter", 0, specified=False)
	currency = v.company_currency()
	batch_no = v._batch(v.RAW, "SEED")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 100, batch_no)
	spec = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=1, temper=0)
	_quotation, so = v.quote_and_order(spec)
	job = v.cut(v.cutting_job_for(so, batch_no))
	entries_before = frappe.db.count("Stock Entry")
	refused_rate = expect_failure(lambda: v.process(job), contains="seeded default")
	expect(frappe.db.count("Stock Entry") == entries_before, "a missing cost rate still created a stock entry")
	expect("Polish" in refused_rate, refused_rate[:240])

	ensure_cost_rate("Polish", "Per Edge Meter", 0, specified=True)
	batch_zero = v._batch(v.RAW, "ZERO")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 80, batch_zero)
	spec_zero = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=1, temper=0)
	_quotation, so_zero = v.quote_and_order(spec_zero)
	job_zero = v.cut(v.cutting_job_for(so_zero, batch_zero))
	processing = v.process(job_zero)
	rows = entry_rows(processing.linked_stock_entry)
	expect(not rows["additional_costs"], f"an intentional zero still capitalized cost: {rows['additional_costs']}")
	expect(not gl_by_account("Stock Entry", processing.linked_stock_entry), "an intentional zero posted a GL entry")

	ensure_cost_rate("Polish", "Per Edge Meter", 5, specified=True)
	set_capitalization(False)
	EVIDENCE["capitalization_account_and_cost_rate"] = {
		"rejected_account": tax,
		"rejected_account_message": refused_account[:300],
		"rejected_group_account": group,
		"seeded_zero_message": refused_rate[:300],
		"intentional_zero_entry": processing.linked_stock_entry,
	}
	frappe.db.commit()
	return f"rejected {tax}; seeded Polish blocked posting; intentional zero posted {processing.linked_stock_entry} with no additional cost"


def cint_flag(value) -> bool:
	return bool(int(value or 0))


def historical_glass_repack_keeps_recorded_processing_cost() -> str:
	"""Repricing a posted glass repack keeps the processing cost it recorded, not today's rates or settings."""
	set_capitalization(True)
	ensure_cost_rate("Polish", "Per Edge Meter", 5, specified=True)
	currency = v.company_currency()
	batch_no = v._batch(v.RAW, "HIST")
	v._receipt(v.RAW, v.settings().raw_warehouse, 1, 100, batch_no)
	spec = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=1, temper=0)
	_quotation, so = v.quote_and_order(spec)
	job = v.cut(v.cutting_job_for(so, batch_no))
	processing = v.process(job)
	posted = frappe.get_doc("Stock Entry", processing.linked_stock_entry)
	finished = next(row for row in posted.items if row.t_warehouse and row.is_finished_item)
	recorded_cost = flt(posted.total_additional_costs, 2)
	recorded_row = flt(finished.gf_processing_cost, 2)
	recorded_rate = flt(finished.valuation_rate, 4)
	recorded_account = posted.additional_costs[0].expense_account
	expect(near(recorded_cost, 20, 2) and near(recorded_row, 20, 2), f"recorded processing cost {recorded_cost}/{recorded_row}, expected 20")

	ensure_cost_rate("Polish", "Per Edge Meter", 99, specified=True)
	set_capitalization(False)
	repriced = frappe.get_doc("Stock Entry", posted.name)
	repriced.flags.via_repost = True
	repriced.calculate_rate_and_amount(raise_error_if_no_rate=False)
	again = next(row for row in repriced.items if row.t_warehouse and row.is_finished_item)
	expect(near(repriced.total_additional_costs, recorded_cost, 2), f"repriced additional cost {repriced.total_additional_costs} != {recorded_cost}")
	expect(near(again.gf_processing_cost, recorded_row, 2), f"repriced row cost {again.gf_processing_cost}")
	expect(near(again.valuation_rate, recorded_rate, 2), f"repriced valuation {again.valuation_rate} != {recorded_rate}")
	expect(repriced.additional_costs[0].expense_account == recorded_account, "repricing changed the posted account")
	stored = frappe.db.get_value(
		"Stock Entry Detail", finished.name, ["gf_processing_cost", "valuation_rate"], as_dict=True
	)
	expect(near(stored.gf_processing_cost, recorded_row, 2) and near(stored.valuation_rate, recorded_rate, 2), f"saved row changed {stored}")

	ensure_cost_rate("Polish", "Per Edge Meter", 5, specified=True)
	set_capitalization(False)
	EVIDENCE["historical_repack_processing_cost"] = {
		"stock_entry": posted.name,
		"recorded_processing_cost": recorded_cost,
		"valuation_rate_before_and_after_reprice": [recorded_rate, flt(again.valuation_rate, 4)],
		"cost_rate_changed_to": 99,
		"capitalization_turned_off_before_reprice": True,
	}
	frappe.db.commit()
	return f"{posted.name} kept processing cost {recorded_cost} and valuation {recorded_rate} after the cost rate became 99 and capitalization was turned off"


def _ordinary_item(code: str) -> str:
	if frappe.db.exists("Item", code):
		return code
	item = frappe.new_doc("Item")
	item.item_code = item.item_name = code
	item.item_group = "All Item Groups"
	item.stock_uom = "Nos"
	item.is_stock_item = 1
	item.valuation_rate = 5
	item.append("item_defaults", {"company": COMPANY, "default_warehouse": v.settings().final_goods_warehouse})
	item.insert(ignore_permissions=True)
	return code


def ordinary_erpnext_repack() -> str:
	"""A normal ERPNext Repack keeps ERPNext's own rates. The glass processing-cost split stays off."""
	from glass_factory.glass_factory.stock_valuation import glass_flow

	warehouse = v.settings().final_goods_warehouse
	suffix = frappe.generate_hash(length=6)
	source = _ordinary_item(f"GFV-ORD-{suffix}")
	single = _ordinary_item(f"GFV-ORD-{suffix}-OUT")
	first = _ordinary_item(f"GFV-ORD-{suffix}-A")
	second = _ordinary_item(f"GFV-ORD-{suffix}-B")
	v._receipt(source, warehouse, 6, 25)
	# One finished item: ERPNext prices it from the consumed value. Glass Factory must not take over.
	auto = frappe.new_doc("Stock Entry")
	auto.stock_entry_type = auto.purpose = "Repack"
	auto.company = COMPANY
	auto.append("items", {"item_code": source, "s_warehouse": warehouse, "qty": 4})
	auto.append("items", {"item_code": single, "t_warehouse": warehouse, "qty": 1, "is_finished_item": 1})
	auto.insert(ignore_permissions=True)
	expect(glass_flow(auto) is None, "an ordinary Repack was treated as a glass movement")
	expect(not auto.items[1].set_basic_rate_manually, "Glass Factory marked an ordinary finished row as manually rated")
	auto.submit()
	auto.reload()
	finished = next(row for row in auto.items if row.is_finished_item)
	expect(near(finished.basic_amount, 100, 2) and near(finished.valuation_rate, 100, 2), f"ordinary single output {finished.basic_amount}/{finished.valuation_rate}")

	# Two finished items have to be rated by hand, which is ERPNext's own rule. A glass weight on the
	# row must not change how the extra cost is shared.
	manual = frappe.new_doc("Stock Entry")
	manual.stock_entry_type = manual.purpose = "Repack"
	manual.company = COMPANY
	manual.append("items", {"item_code": source, "s_warehouse": warehouse, "qty": 2})
	manual.append(
		"items",
		{
			"item_code": first,
			"t_warehouse": warehouse,
			"qty": 1,
			"is_finished_item": 1,
			"set_basic_rate_manually": 1,
			"basic_rate": 10,
			"gf_processing_cost": 999,
		},
	)
	manual.append(
		"items",
		{
			"item_code": second,
			"t_warehouse": warehouse,
			"qty": 1,
			"is_finished_item": 1,
			"set_basic_rate_manually": 1,
			"basic_rate": 40,
			"gf_processing_cost": 1,
		},
	)
	manual.append(
		"additional_costs",
		{
			"expense_account": accounts()["expenses_included_in_valuation"],
			"description": "Ordinary repack charge",
			"amount": 40,
			"base_amount": 40,
			"account_currency": v.company_currency(),
			"exchange_rate": 1,
		},
	)
	manual.insert(ignore_permissions=True)
	manual.submit()
	manual.reload()
	by_item = {row.item_code: row for row in manual.items if row.t_warehouse}
	# Basic amounts 10 and 40. ERPNext shares 40 by that value, which is 8 and 32. The 999 weight would take almost all of it.
	expect(
		near(by_item[first].additional_cost, 8, 2) and near(by_item[second].additional_cost, 32, 2),
		f"additional cost followed gf_processing_cost: {by_item[first].additional_cost}/{by_item[second].additional_cost}",
	)
	expect(glass_flow(manual) is None and not manual.get("gf_glass_stock_flow"), "the manual repack was flagged as glass")
	EVIDENCE["ordinary_erpnext_repack"] = {
		"single_output": {"name": auto.name, "valuation_rate": flt(finished.valuation_rate, 2)},
		"two_output": {
			"name": manual.name,
			"additional_costs": {first: flt(by_item[first].additional_cost, 2), second: flt(by_item[second].additional_cost, 2)},
		},
	}
	frappe.db.commit()
	return f"{auto.name} valued the single output at 100; {manual.name} shared 40 as 8/32 and ignored gf_processing_cost 999/1"


def _sell_attempt(doctype: str, item_code: str, *, update_stock: int = 0) -> str:
	"""Try to sell the item. Mark it sellable inside the savepoint so ERPNext's own flag is not the result."""
	frappe.db.savepoint("sell_attempt")
	try:
		if not frappe.db.get_value("Item", item_code, "is_sales_item"):
			frappe.db.set_value("Item", item_code, "is_sales_item", 1)
		doc = frappe.new_doc(doctype)
		doc.company = COMPANY
		if doctype == "Quotation":
			doc.quotation_to = "Customer"
			doc.party_name = "GFV Customer"
		else:
			doc.customer = "GFV Customer"
		if doctype == "Sales Order":
			doc.delivery_date = add_days(today(), 7)
		if doctype == "Sales Invoice":
			doc.update_stock = update_stock
		doc.append(
			"items",
			{"item_code": item_code, "qty": 1, "rate": 10, "warehouse": v.settings().raw_warehouse},
		)
		doc.insert(ignore_permissions=True)
		frappe.db.rollback(save_point="sell_attempt")
		return "accepted"
	except Exception as exc:
		frappe.db.rollback(save_point="sell_attempt")
		return f"{type(exc).__name__}: {exc}"[:300]


def noncommercial_sales_probe() -> str:
	"""Record which selling documents still accept a raw sheet or a Cut WIP item."""
	currency = v.company_currency()
	spec = make_spec_ops(currency, "GFV Selling", 1, length=1200, width=800, polish=0, temper=0)
	items = {"raw_sheet": v.RAW, "cut_wip": spec.cut_wip_item_code}
	results = {}
	for label, item_code in items.items():
		results[label] = {
			"item": item_code,
			"is_sales_item_on_the_item": int(frappe.db.get_value("Item", item_code, "is_sales_item") or 0),
			"quotation": _sell_attempt("Quotation", item_code),
			"sales_order": _sell_attempt("Sales Order", item_code),
			"delivery_note": _sell_attempt("Delivery Note", item_code),
			"sales_invoice_update_stock": _sell_attempt("Sales Invoice", item_code, update_stock=1),
			"sales_invoice_no_stock": _sell_attempt("Sales Invoice", item_code, update_stock=0),
		}
		for document in ("quotation", "sales_order", "delivery_note", "sales_invoice_update_stock"):
			expect(
				"cannot use" in results[label][document].lower(),
				f"{label} {document} was not blocked: {results[label][document]}",
			)
	EVIDENCE["noncommercial_sales"] = results
	frappe.db.commit()
	return json.dumps({label: {key: str(value)[:120] for key, value in row.items()} for label, row in results.items()})


def leave_billing_precision_at_six() -> str:
	"""Leave the disposable site billing glass at 6 decimals, after checking an open order and an ordinary item.

	Not part of the scenario list: that list restores the default precision when it finishes.
	"""
	v.prepare()
	set_capitalization(False)
	set_precision_config("system3")
	currency = v.company_currency()
	batch_no = v.raw_batch(sheets=1, rate=100)
	spec = make_spec_ops(currency, "GFV Selling", 3, length=1234, width=567, manual_rate=500)
	_quotation, so = v.quote_and_order(spec)
	old_factor = flt(so.items[0].conversion_factor, 9)
	job = v.cut(v.cutting_job_for(so, batch_no))
	processing = v.process(job)
	final_no = v.final_batch(processing)

	from glass_factory.glass_factory.selling_uom import set_billing_area_precision

	applied = set_billing_area_precision(6)
	dn = v.delivery_for(so, typed_qty=0.7, batch_no=final_no)
	expect(near(dn.items[0].conversion_factor, old_factor, 9) and near(dn.items[0].qty, 0.7, 9), f"open order left its rule: {dn.items[0].conversion_factor}/{dn.items[0].qty}")
	expect(near(dn.items[0].amount, 350, 2), f"open order was rebilled: {dn.items[0].amount}")

	spec_new = make_spec_ops(currency, "GFV Selling", 3, length=1234, width=567, manual_rate=500)
	_quotation, so_new = v.quote_and_order(spec_new)
	expect(near(so_new.items[0].qty, 2.099034, 9) and near(so_new.items[0].amount, 1049.52, 2), f"new order {so_new.items[0].qty}/{so_new.items[0].amount}")

	# Nos is a whole-number UOM. Precision 6 must not turn an ordinary item into a glass area row,
	# and it must not make a fractional Nos quantity legal.
	ordinary = frappe.new_doc("Sales Order")
	ordinary.company = COMPANY
	ordinary.customer = "GFV Customer"
	ordinary.delivery_date = add_days(today(), 7)
	ordinary.append(
		"items",
		{
			"item_code": _ordinary_item("GFV-ORDINARY"),
			"qty": 2,
			"rate": 10,
			"warehouse": v.settings().final_goods_warehouse,
		},
	)
	ordinary.insert(ignore_permissions=True)
	ordinary.submit()
	row = ordinary.items[0]
	expect(near(row.qty, 2, 6) and near(row.stock_qty, 2, 6), f"ordinary quantity changed: {row.qty}/{row.stock_qty}")
	expect((row.uom or "") == "Nos" and not row.get("gf_is_glass_item"), f"ordinary row was treated as glass: {row.uom}/{row.gf_is_glass_item}")

	def fractional_ordinary():
		doc = frappe.new_doc("Sales Order")
		doc.company = COMPANY
		doc.customer = "GFV Customer"
		doc.delivery_date = add_days(today(), 7)
		doc.append(
			"items",
			{
				"item_code": "GFV-ORDINARY",
				"qty": 1.234567,
				"rate": 10,
				"warehouse": v.settings().final_goods_warehouse,
			},
		)
		doc.insert(ignore_permissions=True)

	refused = expect_failure(fractional_ordinary, contains="whole number")
	frappe.db.commit()
	result = {
		"billing_precision": applied,
		"open_order": so.name,
		"open_order_delivery": {"name": dn.name, "qty": flt(dn.items[0].qty, 9), "amount": flt(dn.items[0].amount, 2), "factor": flt(dn.items[0].conversion_factor, 9)},
		"new_order": {"name": so_new.name, "qty": flt(so_new.items[0].qty, 9), "amount": flt(so_new.items[0].amount, 2)},
		"ordinary_sales_order": {"name": ordinary.name, "qty": flt(row.qty, 6), "stock_qty": flt(row.stock_qty, 6), "uom": row.uom},
		"ordinary_fraction_refused": refused[:240],
	}
	EVIDENCE["billing_left_at_6"] = result
	return json.dumps(result)


SCENARIOS = (
	("prepare", v.prepare),
	("repack2_valuation_capitalization_off", repack2_valuation_capitalization_off),
	("repack2_capitalization_on", repack2_capitalization_on),
	("repack2_capitalization_line_without_operations", repack2_capitalization_line_without_operations),
	("capitalization_rejects_unsuitable_account_and_seeded_cost", capitalization_rejects_unsuitable_account_and_seeded_cost),
	("historical_glass_repack_keeps_recorded_processing_cost", historical_glass_repack_keeps_recorded_processing_cost),
	("ordinary_erpnext_repack", ordinary_erpnext_repack),
	("noncommercial_sales_probe", noncommercial_sales_probe),
	("accounting_example_tzs", accounting_example_tzs),
	("accounting_example_lcv_after_delivery", accounting_example_lcv_after_delivery),
	("landed_cost_after_two_item_production", landed_cost_after_two_item_production),
	("landed_cost_across_batches", landed_cost_across_batches),
	("billing_precision_system3", billing_precision_system3),
	("billing_precision_rows6", billing_precision_rows6),
	("billing_precision_system6", billing_precision_system6),
	("billing_precision_rows2", billing_precision_rows2),
	("billing_rule_change_with_open_order", billing_rule_change_with_open_order),
	("billing_sweep_exact", billing_sweep_exact),
	("billing_sweep_default", billing_sweep_default),
	("opening_stock_reconciliation_and_transfer", opening_stock_reconciliation_and_transfer),
	("supplier_return_and_debit_note", supplier_return_and_debit_note),
	("customer_partial_return_and_credit_note", customer_partial_return_and_credit_note),
	("direct_sales_invoice_with_stock_update", direct_sales_invoice_with_stock_update),
	("direct_purchase_invoice_with_stock_update", direct_purchase_invoice_with_stock_update),
	("usd_company_account_without_setting", usd_company_account_without_setting),
	("usd_company_account_same_rate", usd_company_account_same_rate),
	("usd_company_account_rate_moves", usd_company_account_rate_moves),
	("usd_party_account_same_rate", usd_party_account_same_rate),
	("usd_party_account_rate_moves", usd_party_account_rate_moves),
)


def run(only: str | None = None) -> dict:
	frappe.set_user("Administrator")
	try:
		for name, fn in SCENARIOS:
			if only and name not in only.split(","):
				continue
			check(name, fn)
	finally:
		# Leave the site as the other suite expects it: no capitalization, default billing precision.
		frappe.db.rollback()
		set_capitalization(False)
		set_precision_config("system3")
	print("EVIDENCE", json.dumps(EVIDENCE, default=str, indent=1))
	failed = [row for row in RESULTS if row["result"] != "passed"]
	print("SUMMARY", len(RESULTS) - len(failed), "passed,", len(failed), "failed")
	if failed:
		frappe.throw(f"{len(failed)} accounting scenario(s) failed")
	return {"passed": len(RESULTS)}
