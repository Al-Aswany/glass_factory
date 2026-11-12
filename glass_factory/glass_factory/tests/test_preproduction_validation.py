"""Committed integration checks for the pre-production Glass Factory corrections.

Run on a disposable site only:
bench --site glass-factory-validation.local execute glass_factory.glass_factory.tests.test_preproduction_validation.run

Each scenario commits real documents. Scenarios can also be run one at a time by name, for
example ...test_preproduction_validation.m2_acceptance_tzs.
"""

from __future__ import annotations

import threading

import frappe
from frappe.utils import add_days, flt, today

from glass_factory.glass_factory.doctype.cutting_job.cutting_job import make_cutting_job
from glass_factory.glass_factory.selling_uom import delivered_pieces
from glass_factory.glass_factory.spec_transaction import add_spec_to_transaction, map_spec_to_transaction_row
from glass_factory.glass_factory.tests.test_spec_e2e_submitted_flow import (
	_ensure_customer,
	_ensure_item_price,
	_ensure_price_list,
	_ensure_raw_item,
)

COMPANY = "Other Glass Co"  # operating company, company currency TZS
OTHER_COMPANY = "Glass Validation"  # second company, USD, never used for glass
RAW = "GLS-CLEAR-8MM-3000X2000"
RESULTS: list[dict] = []
EXAMPLE: dict[str, dict] = {}


def check(name: str, fn) -> None:
	try:
		detail = fn() or ""
		RESULTS.append({"scenario": name, "result": "passed", "detail": str(detail)})
		print(f"PASS {name} {detail}")
	except Exception as exc:
		frappe.db.rollback()
		RESULTS.append({"scenario": name, "result": "failed", "detail": f"{type(exc).__name__}: {exc}"})
		print(f"FAIL {name}: {type(exc).__name__}: {exc}")
		frappe.log_error(title=f"GF validation {name}", message=frappe.get_traceback())


def expect(condition, message: str) -> None:
	if not condition:
		frappe.throw(message)


def expect_failure(fn, contains: str | None = None) -> str:
	"""Run fn expecting it to fail, undoing only its own writes so earlier scenario data survives."""
	frappe.db.savepoint("gf_expect_failure")
	try:
		fn()
	except Exception as exc:
		frappe.db.rollback(save_point="gf_expect_failure")
		message = str(exc)
		if contains:
			expect(contains.lower() in message.lower(), f"unexpected message: {message[:240]}")
		return message
	frappe.db.rollback(save_point="gf_expect_failure")
	frappe.throw("an operation that should have been refused was accepted")


def near(actual, expected, places=6) -> bool:
	return abs(flt(actual) - flt(expected)) < 10 ** (-places)


def snapshot() -> dict:
	bins = frappe.db.sql(
		"select item_code, warehouse, actual_qty, valuation_rate, stock_value from tabBin order by item_code, warehouse",
		as_dict=True,
	)
	sle = frappe.db.sql(
		"select count(*), ifnull(sum(stock_value_difference),0) from `tabStock Ledger Entry` where is_cancelled=0"
	)[0]
	gl = frappe.db.sql(
		"select count(*), ifnull(sum(debit),0), ifnull(sum(credit),0) from `tabGL Entry` where is_cancelled=0"
	)[0]
	return {"bins": bins, "sle": sle, "gl": gl}


def settings():
	return frappe.get_single("Glass Factory Settings")


def company_currency() -> str:
	return frappe.db.get_value("Company", COMPANY, "default_currency")


# --------------------------------------------------------------------------- fixtures


def _ensure_operation_rate(operation, currency, basis, rate, cost_rate, *, specified=None) -> None:
	"""Write a cost rate. A positive rate is specified; pass specified=True to record an intentional zero."""
	if specified is None:
		specified = 1 if flt(cost_rate) > 0 else 0
	doc = settings()
	for row in doc.operation_rates:
		if row.operation == operation and row.currency == currency and row.pricing_basis == basis:
			row.rate, row.cost_rate, row.enabled = rate, cost_rate, 1
			row.cost_rate_set = 1 if specified else 0
			doc.save(ignore_permissions=True)
			return
	doc.append(
		"operation_rates",
		{
			"operation": operation,
			"currency": currency,
			"pricing_basis": basis,
			"rate": rate,
			"cost_rate": cost_rate,
			"cost_rate_set": 1 if specified else 0,
			"enabled": 1,
		},
	)
	doc.save(ignore_permissions=True)


def prepare() -> str:
	frappe.db.set_single_value("Stock Settings", "enable_serial_and_batch_no_for_item", 1)
	# ERPNext rule for invoicing in a currency other than the customer's receivable account currency.
	frappe.db.set_single_value("Accounts Settings", "allow_multi_currency_invoices_against_single_party_account", 1)
	frappe.db.set_single_value("Glass Factory Settings", "enable_cop", 0)
	# Scenarios that capitalize processing cost switch it back off, but a run that stopped halfway must not leak it.
	frappe.db.set_single_value("Glass Factory Settings", "capitalize_processing_costs", 0)
	frappe.defaults.set_global_default("company", COMPANY)
	currency = company_currency()
	doc = settings()
	buying = _ensure_price_list("GFV Buying", buying=True, selling=False, currency=currency)
	selling = _ensure_price_list("GFV Selling", buying=False, selling=True, currency=currency)
	usd_selling = _ensure_price_list("GFV Selling USD", buying=False, selling=True, currency="USD")
	doc.default_buying_price_list = buying
	doc.default_selling_price_list = selling
	# Set on the document that is saved: a Settings document read from cache would write a stale value back.
	doc.capitalize_processing_costs = 0
	doc.enable_cop = 0
	doc.save(ignore_permissions=True)
	frappe.clear_cache()
	_ensure_customer("GFV Customer")
	_ensure_raw_item(RAW, doc.raw_item_group, COMPANY, doc.raw_warehouse)
	_ensure_item_price(RAW, buying, 600, currency, buying=True, selling=False, uom="Nos")
	_ensure_item_price(RAW, selling, 80, currency, buying=False, selling=True, uom="Square Meter")
	_ensure_item_price(RAW, usd_selling, 25, "USD", buying=False, selling=True, uom="Square Meter")
	for operation, basis in (("Polish", "Per Edge Meter"), ("Temper", "Per Square Meter")):
		_ensure_operation_rate(operation, currency, basis, 40, 4)
		_ensure_operation_rate(operation, "USD", basis, 20, 2)
	if not frappe.db.exists("Currency Exchange", {"from_currency": "USD", "to_currency": currency}):
		exchange = frappe.new_doc("Currency Exchange")
		exchange.from_currency, exchange.to_currency = "USD", currency
		exchange.exchange_rate, exchange.date = 2500, today()
		exchange.insert(ignore_permissions=True)
	if not frappe.db.exists("Item", "GFV-ORDINARY"):
		item = frappe.new_doc("Item")
		item.item_code = item.item_name = "GFV-ORDINARY"
		item.item_group = "All Item Groups"
		item.stock_uom = "Nos"
		item.is_stock_item = 1
		item.valuation_rate = 5
		item.append("item_defaults", {"company": COMPANY, "default_warehouse": doc.final_goods_warehouse})
		item.insert(ignore_permissions=True)
	frappe.db.commit()
	return f"company={COMPANY} currency={currency}"


def _batch(item_code: str, label: str) -> str:
	batch = frappe.new_doc("Batch")
	batch.item = item_code
	batch.batch_id = f"{label}-{frappe.generate_hash(length=6)}"
	batch.insert(ignore_permissions=True)
	return batch.name


def _receipt(item_code, warehouse, qty, rate, batch_no=None) -> str:
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = se.purpose = "Material Receipt"
	se.company = COMPANY
	se.posting_date = today()
	row = {"item_code": item_code, "t_warehouse": warehouse, "qty": qty, "basic_rate": rate}
	if batch_no:
		row.update({"batch_no": batch_no, "use_serial_batch_fields": 1})
	se.append("items", row)
	se.insert(ignore_permissions=True)
	se.submit()
	return se.name


def raw_batch(sheets=3, rate=100) -> str:
	batch_no = _batch(RAW, "RAW")
	_receipt(RAW, settings().raw_warehouse, sheets, rate, batch_no)
	return batch_no


def make_spec(currency, price_list, pieces, *, length=1200, width=800, manual_rate=500, buying=600):
	spec = frappe.new_doc("Glass Product Specification")
	spec.company, spec.customer, spec.currency, spec.price_list = COMPANY, "GFV Customer", currency, price_list
	spec.raw_sheet_item, spec.length_mm, spec.width_mm, spec.qty = RAW, length, width, pieces
	spec.polish = spec.temper = 1
	spec.manual_selling_rate_per_m2 = manual_rate
	spec.raw_sheet_rate_per_piece = buying
	spec.insert(ignore_permissions=True)
	spec.refresh_pricing()
	spec.reload()
	spec.generate_items()
	spec.reload()
	return spec


def quote_and_order(spec, *, row_rate=None):
	"""Specification -> Quotation (submitted) -> Sales Order (submitted)."""
	from erpnext.selling.doctype.quotation.quotation import make_sales_order

	quotation = frappe.get_doc("Quotation", add_spec_to_transaction(spec.name, "Quotation")["name"])
	if row_rate is not None:
		quotation.items[0].rate = row_rate
		quotation.save()
		quotation.reload()
	quotation.submit()
	so = make_sales_order(quotation.name)
	so.delivery_date = add_days(today(), 7)
	for row in so.items:
		row.delivery_date = so.delivery_date
	so.insert(ignore_permissions=True)
	so.submit()
	so.reload()
	return quotation, so


def cutting_job_for(so, batch_no, *, pieces=None):
	job = make_cutting_job(so.name)
	job.reload()
	if pieces is not None:
		job.pieces[0].qty_required = job.pieces[0].qty_assigned = job.pieces[0].qty_cut = pieces
	job.source_sheets[0].batch_no = batch_no
	job.source_sheets[0].qty_consumed = 1
	job.save(ignore_permissions=True)
	job.submit()
	job.reload()
	return job


def cut(job):
	job.submit_repack_stock_entry()
	job.reload()
	return job


def process(job, *, post=True):
	job.start_processing()
	job.reload()
	processing = frappe.get_doc("Glass Processing Job", job.linked_processing_job)
	for operation in processing.operations:
		operation.status = "Completed"
	processing.save(ignore_permissions=True)
	processing.submit()
	if post:
		processing.submit_repack_stock_entry()
	processing.reload()
	return processing


def delivery_for(so, *, pieces=None, typed_qty=None, batch_no=None, submit=True):
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = frappe.get_doc(make_delivery_note(so.name))
	row = dn.items[0]
	if typed_qty is not None:
		row.qty = typed_qty
	elif pieces is not None:
		row.qty = flt(pieces / flt(row.conversion_factor), 9)
	row.batch_no = batch_no
	row.use_serial_batch_fields = 1
	dn.insert(ignore_permissions=True)
	if submit:
		dn.submit()
	dn.reload()
	return dn


def invoice_for(dn):
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

	si = make_sales_invoice(dn.name)
	si.insert(ignore_permissions=True)
	si.submit()
	si.reload()
	return si


def final_batch(processing) -> str:
	entry = frappe.get_doc("Stock Entry", processing.linked_stock_entry)
	return next(row.batch_no for row in entry.items if row.t_warehouse and row.is_finished_item)


def so_row(so_name):
	return frappe.db.get_value(
		"Sales Order Item",
		{"parent": so_name},
		["name", "qty", "uom", "stock_uom", "conversion_factor", "stock_qty", "rate", "amount", "base_rate", "base_amount", "delivered_qty", "gf_cut_qty", "gf_processed_qty", "gf_delivered_qty", "gf_transaction_rate_overridden"],
		as_dict=True,
		order_by="idx asc",
	)


def sle_qty(voucher_no: str) -> float:
	return flt(
		frappe.db.sql(
			"select ifnull(sum(actual_qty),0) from `tabStock Ledger Entry` where voucher_no=%s and is_cancelled=0",
			voucher_no,
		)[0][0]
	)


# --------------------------------------------------------------------------- scenarios


def pricing_costs() -> str:
	currency = company_currency()
	spec = make_spec(currency, "GFV Selling", 2, manual_rate=500)
	rows = {row.operation: row for row in spec.operation_pricing}
	polish, temper = rows["Polish"], rows["Temper"]
	expect(polish.pricing_basis == "Per Edge Meter" and near(polish.quantity, 4.0), f"polish basis/qty {polish.pricing_basis} {polish.quantity}")
	expect(near(polish.cost_rate, 4) and near(polish.cost_amount, 16.0), f"polish cost {polish.cost_rate}/{polish.cost_amount}")
	expect(near(temper.quantity, 0.96) and near(temper.cost_amount, 3.84), f"temper cost {temper.quantity}/{temper.cost_amount}")
	expect(near(polish.rate, 40) and near(polish.amount, 160), f"polish selling {polish.rate}/{polish.amount}")
	internal = flt(sum(flt(row.cost_amount) for row in spec.operation_pricing), 2)
	expect(near(spec.processing_cost_per_piece, internal), f"processing cost {spec.processing_cost_per_piece} != rows {internal}")
	expect(near(spec.estimated_cost_per_piece, flt(spec.raw_cost_per_finished_piece + internal, 2), 2), "margin cost is not raw + row costs")
	baseline_cost, baseline_rows = flt(spec.estimated_cost_per_piece), [flt(row.cost_amount) for row in spec.operation_pricing]

	# A selling-rate override on an operation, and a different final price, must not move cost.
	polish.is_overridden, polish.rate = 1, 999
	spec.manual_selling_rate_per_m2 = 650
	spec.save()
	spec.reload()
	expect(near(spec.estimated_cost_per_piece, baseline_cost), "override changed estimated cost")
	expect([flt(row.cost_amount) for row in spec.operation_pricing] == baseline_rows, "override changed row costs")
	expect(near(spec.processing_amount_per_piece, flt(999 * 4 + 40 * 0.96, 2), 2), f"selling amount not updated {spec.processing_amount_per_piece}")
	spec.manual_selling_rate_per_m2 = 500
	polish_row = next(row for row in spec.operation_pricing if row.operation == "Polish")
	polish_row.is_overridden, polish_row.rate = 0, 40
	spec.save()

	usd = make_spec("USD", "GFV Selling USD", 2, manual_rate=500, buying=200)
	usd_rows = {row.operation: row for row in usd.operation_pricing}
	expect(near(usd_rows["Polish"].cost_rate, 2) and near(usd_rows["Polish"].cost_amount, 8.0), "USD cost rate not used")
	expect(near(usd.estimated_cost_per_piece, flt(usd.raw_cost_per_finished_piece + usd.processing_cost_per_piece, 2), 2), "USD margin")
	frappe.db.commit()
	return (
		f"TZS rows polish={polish.cost_rate}x{polish.quantity}={polish.cost_amount} temper={temper.cost_amount} "
		f"total={internal} estimated_cost={baseline_cost}; USD polish cost={usd_rows['Polish'].cost_amount} "
		f"estimated_cost={usd.estimated_cost_per_piece}; override moved selling amount only"
	)


def _user(email: str, role: str) -> str:
	if frappe.db.exists("User", email):
		user = frappe.get_doc("User", email)
	else:
		user = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0})
		user.insert(ignore_permissions=True)
	if role not in {row.role for row in user.roles}:
		user.append("roles", {"role": role})
		user.save(ignore_permissions=True)
	return user.name


def cost_visibility() -> str:
	from glass_factory.glass_factory.spec_pricing import pricing_result

	spec_name = frappe.db.get_value("Glass Product Specification", {"company": COMPANY}, "name")
	operator = _user("gf-operator@validation.local", "Glass Cutting Operator")
	planner = _user("gf-planner@validation.local", "Glass Production Planner")
	seen = {}
	for label, user in (("operator", operator), ("planner", planner)):
		frappe.set_user(user)
		doc = frappe.get_doc("Glass Product Specification", spec_name)
		doc.apply_fieldlevel_read_permissions()
		seen[label] = {
			"estimated_cost": doc.get("estimated_cost_per_piece"),
			"row_cost": [row.get("cost_amount") for row in doc.operation_pricing],
			"row_selling": [row.get("amount") for row in doc.operation_pricing],
			"result_keys": "estimated_cost_per_piece" in pricing_result(doc),
		}
	frappe.set_user("Administrator")
	expect(seen["operator"]["estimated_cost"] is None and all(v is None for v in seen["operator"]["row_cost"]), f"operator saw costs {seen['operator']}")
	expect(all(v is not None for v in seen["operator"]["row_selling"]), "operator lost selling amounts")
	expect(not seen["operator"]["result_keys"], "pricing_result leaked costs to the operator")
	expect(seen["planner"]["estimated_cost"] is not None and all(v is not None for v in seen["planner"]["row_cost"]), "planner cannot see costs")
	expect(seen["planner"]["result_keys"], "pricing_result hid costs from the planner")

	from glass_factory.glass_factory.report.glass_processing_cost_report import glass_processing_cost_report as report

	frappe.set_user(operator)
	try:
		report.execute({})
	except frappe.PermissionError:
		blocked = True
	else:
		blocked = False
	frappe.set_user(planner)
	columns, rows = report.execute({})[:2]
	frappe.set_user("Administrator")
	expect(blocked, "operator could run the processing cost report")
	expect(rows and rows[0].get("cost_amount") is not None, "planner report has no cost rows")
	# Customer documents carry no cost fields.
	for doctype in ("Quotation Item", "Sales Order Item", "Delivery Note Item", "Sales Invoice Item"):
		leaked = [f.fieldname for f in frappe.get_meta(doctype).fields if f.fieldname.startswith("gf_") and ("cost" in f.fieldname or "profit" in f.fieldname)]
		expect(not leaked, f"{doctype} has cost fields {leaked}")
	return "operator: costs hidden, selling visible, report refused; planner: costs and report visible; no cost fields on customer rows"


def full_flow(label, currency, price_list, *, buying, row_rate=None, pieces=2, length=1200, width=800, partial_pieces=1, partial_typed=None, manual_rate=500):
	"""Spec -> Quotation -> Sales Order -> cut -> process -> partial DN + SI -> remainder DN + SI."""
	batch_no = raw_batch()
	spec = make_spec(currency, price_list, pieces, length=length, width=width, manual_rate=manual_rate, buying=buying)
	# Each piece is billed at its area rounded to the Quantity precision (3 decimals), so whole
	# pieces convert exactly. 1200 x 800 mm is 0.96 m2 either way.
	billed_area = flt(length * width / 1_000_000, 3)
	total_m2 = flt(billed_area * pieces, 9)
	rate = flt(row_rate if row_rate is not None else manual_rate)
	conversion = flt(1 / billed_area, 9)
	quotation, so = quote_and_order(spec, row_rate=row_rate)

	q_row = quotation.items[0]
	expect(q_row.uom == "Square Meter" and q_row.stock_uom == "Nos", f"quotation uoms {q_row.uom}/{q_row.stock_uom}")
	expect(near(q_row.qty, total_m2) and near(q_row.stock_qty, pieces), f"quotation qty {q_row.qty}/{q_row.stock_qty}")
	expect(near(q_row.conversion_factor, conversion, 9), f"quotation factor {q_row.conversion_factor} vs {conversion}")
	expect(near(q_row.rate, rate), f"quotation rate {q_row.rate}")
	expect(near(q_row.amount, flt(total_m2 * rate, 2), 2), f"quotation amount {q_row.amount}")
	if row_rate is not None:
		expect(cint_(q_row.gf_transaction_rate_overridden) == 1, "row override flag not set")

	row = so_row(so.name)
	expect(row.uom == "Square Meter" and row.stock_uom == "Nos", "sales order uoms")
	expect(near(row.qty, total_m2) and near(row.stock_qty, pieces), f"sales order qty {row.qty}/{row.stock_qty}")
	expect(near(row.rate, rate) and near(row.amount, flt(total_m2 * rate, 2), 2), f"sales order rate/amount {row.rate}/{row.amount}")
	exchange = flt(so.conversion_rate)
	expect(near(row.base_amount, flt(row.amount * exchange, 2), 2), f"base amount {row.base_amount} vs {row.amount} x {exchange}")

	job = cut(cutting_job_for(so, batch_no))
	piece_row = job.pieces[0]
	expect(near(piece_row.qty_required, pieces), f"cutting job pieces {piece_row.qty_required}")
	expect(near(so_row(so.name).gf_cut_qty, pieces), "cut qty not in pieces")
	processing = process(job)
	expect(near(processing.outputs[0].qty, pieces), "processing output not in pieces")
	expect(near(so_row(so.name).gf_processed_qty, pieces), f"processed qty {so_row(so.name).gf_processed_qty}")
	final_no = final_batch(processing)

	first = delivery_for(so, pieces=partial_pieces, typed_qty=partial_typed, batch_no=final_no)
	d1 = first.items[0]
	expect(near(d1.stock_qty, partial_pieces), f"first DN pieces {d1.stock_qty}")
	expect(near(sle_qty(first.name), -partial_pieces), f"first DN stock ledger {sle_qty(first.name)}")
	expect(near(d1.amount, flt(d1.qty * rate, 2), 2), f"first DN amount {d1.amount}")
	after_first = so_row(so.name)
	expect(near(after_first.gf_delivered_qty, partial_pieces), f"delivered pieces {after_first.gf_delivered_qty}")
	expect(near(after_first.delivered_qty, d1.qty), f"ERPNext delivered_qty {after_first.delivered_qty} vs DN qty {d1.qty}")
	first_invoice = invoice_for(first)
	expect(near(first_invoice.items[0].qty, d1.qty) and first_invoice.items[0].uom == "Square Meter", "first invoice qty/uom")

	second = delivery_for(so, batch_no=final_no)
	d2 = second.items[0]
	remaining = pieces - partial_pieces
	expect(near(d2.stock_qty, remaining) and near(sle_qty(second.name), -remaining), f"second DN pieces {d2.stock_qty}")
	final_row = so_row(so.name)
	expect(near(final_row.gf_delivered_qty, pieces), f"all pieces delivered {final_row.gf_delivered_qty}")
	expect(near(final_row.delivered_qty, total_m2), f"all m2 delivered {final_row.delivered_qty}")
	second_invoice = invoice_for(second)
	invoiced = flt(first_invoice.grand_total + second_invoice.grand_total, 2)
	expect(near(first_invoice.items[0].amount + second_invoice.items[0].amount, flt(total_m2 * rate, 2), 2), "invoices do not add up to the line amount")

	# Nothing more can be delivered once every piece has gone.
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	def deliver_beyond_order():
		extra = frappe.get_doc(make_delivery_note(so.name))
		extra.items[0].batch_no = final_no
		extra.insert(ignore_permissions=True)
		extra.submit()

	expect_failure(deliver_beyond_order)

	frappe.db.commit()
	EXAMPLE[label] = {
		"spec": spec.name,
		"quotation": quotation.name,
		"sales_order": so.name,
		"currency": so.currency,
		"conversion_rate": exchange,
		"qty_m2": flt(row.qty, 6),
		"uom": row.uom,
		"stock_qty_pieces": flt(row.stock_qty, 6),
		"conversion_factor": flt(row.conversion_factor, 9),
		"rate": flt(row.rate, 6),
		"amount": flt(row.amount, 2),
		"base_rate": flt(row.base_rate, 6),
		"base_amount": flt(row.base_amount, 2),
		"cutting_job": job.name,
		"processing_job": processing.name,
		"delivery_1": f"{first.name}: {flt(d1.qty, 6)} m2 = {flt(d1.stock_qty, 6)} pcs, amount {flt(d1.amount, 2)}",
		"delivery_2": f"{second.name}: {flt(d2.qty, 6)} m2 = {flt(d2.stock_qty, 6)} pcs, amount {flt(d2.amount, 2)}",
		"invoice_1": f"{first_invoice.name}: {flt(first_invoice.items[0].qty, 6)} m2, amount {flt(first_invoice.items[0].amount, 2)}",
		"invoice_2": f"{second_invoice.name}: {flt(second_invoice.items[0].qty, 6)} m2, amount {flt(second_invoice.items[0].amount, 2)}",
		"gf_delivered_pieces": flt(final_row.gf_delivered_qty, 6),
	}
	return frappe.as_json(EXAMPLE[label])


def cint_(value) -> int:
	return int(flt(value))


def m2_acceptance_tzs() -> str:
	return full_flow("TZS", company_currency(), "GFV Selling", buying=600)


def m2_acceptance_usd() -> str:
	return full_flow("USD", "USD", "GFV Selling USD", buying=200)


def row_rate_override() -> str:
	return full_flow("TZS row override 450/m2", company_currency(), "GFV Selling", buying=600, row_rate=450)


def odd_dimensions_partial() -> str:
	"""1234 x 567 mm: area per piece has 6 decimals, so m2 typed in the desk rounds to 3."""
	return full_flow(
		"odd 1234x567",
		company_currency(),
		"GFV Selling",
		buying=600,
		pieces=3,
		length=1234,
		width=567,
		partial_pieces=1,
		partial_typed=0.700,
	)


def legacy_and_mixed_order() -> str:
	"""One order: m2 glass row, an old Nos glass row, and an ordinary item."""
	batch_no = raw_batch(sheets=3)
	currency = company_currency()
	area_spec = make_spec(currency, "GFV Selling", 2, manual_rate=500)
	old_spec = make_spec(currency, "GFV Selling", 3, length=1000, width=700, manual_rate=500)
	_receipt("GFV-ORDINARY", settings().final_goods_warehouse, 10, 5)
	so = frappe.get_doc("Sales Order", add_spec_to_transaction(area_spec.name, "Sales Order")["name"])
	legacy = map_spec_to_transaction_row(old_spec)
	pieces = flt(old_spec.qty)
	legacy.update(
		{
			"uom": "Nos",
			"stock_uom": "Nos",
			"conversion_factor": 1,
			"qty": pieces,
			"stock_qty": pieces,
			"rate": old_spec.rate_per_piece,
			"gf_rate_per_piece": old_spec.rate_per_piece,
			"gf_total_area_m2": flt(old_spec.area_m2 * pieces, 6),
		}
	)
	legacy_row = so.append("items", legacy)
	so.append("items", {"item_code": "GFV-ORDINARY", "qty": 4, "rate": 12, "warehouse": settings().final_goods_warehouse, "delivery_date": so.delivery_date})
	so.save()
	so.submit()
	so.reload()
	rows = {row.item_code: row for row in so.items}
	m2_row = rows[area_spec.final_item_code]
	old_row = rows[old_spec.final_item_code]
	expect(m2_row.uom == "Square Meter" and near(m2_row.stock_qty, 2), "m2 row in mixed order")
	expect(old_row.uom == "Nos" and near(old_row.qty, 3) and near(old_row.stock_qty, 3), "legacy Nos row changed")
	job = make_cutting_job(so.name)
	job.reload()
	by_item = {piece.final_item: piece for piece in job.pieces}
	expect(set(by_item) == {area_spec.final_item_code, old_spec.final_item_code}, f"ordinary item entered the cutting job {set(by_item)}")
	expect(near(by_item[area_spec.final_item_code].qty_required, 2) and near(by_item[old_spec.final_item_code].qty_required, 3), "cutting pieces in mixed order")
	job.source_sheets[0].batch_no = batch_no
	job.source_sheets[0].qty_consumed = 1
	job.save(ignore_permissions=True)
	job.submit()
	cut(job)
	processing = process(job)
	entry = frappe.get_doc("Stock Entry", processing.linked_stock_entry)
	batches = {row.item_code: row.batch_no for row in entry.items if row.t_warehouse and row.is_finished_item}
	# Two different finished Items in one Repack #2: the consumed Cut WIP value is split by area.
	finished = [row for row in entry.items if row.t_warehouse and row.is_finished_item]
	expect(len(finished) == 2 and all(flt(row.basic_rate) > 0 for row in finished), "finished rows were not valued")
	net = frappe.db.sql(
		"select ifnull(sum(stock_value_difference),0) from `tabStock Ledger Entry` where voucher_no=%s and is_cancelled=0",
		entry.name,
	)[0][0]
	expect(abs(flt(net)) < 0.05, f"Repack #2 value in != value out ({net})")
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	dn = frappe.get_doc(make_delivery_note(so.name))
	for row in dn.items:
		if row.item_code in batches:
			row.batch_no = batches[row.item_code]
			row.use_serial_batch_fields = 1
		if row.item_code == "GFV-ORDINARY":
			row.qty = 3  # partial delivery of the ordinary row
	dn.insert(ignore_permissions=True)
	dn.submit()
	dn.reload()
	delivered = {row.item_code: row for row in dn.items}
	expect(near(delivered[area_spec.final_item_code].stock_qty, 2), "m2 row pieces in mixed DN")
	expect(near(delivered[old_spec.final_item_code].stock_qty, 3), "legacy row pieces in mixed DN")
	expect(near(delivered["GFV-ORDINARY"].qty, 3) and near(delivered["GFV-ORDINARY"].stock_qty, 3), "ordinary row changed")
	mirror = {r.item_code: r for r in frappe.get_all("Sales Order Item", filters={"parent": so.name}, fields=["item_code", "gf_delivered_qty", "delivered_qty"])}
	expect(near(mirror[area_spec.final_item_code].gf_delivered_qty, 2), "m2 delivered pieces mirror")
	expect(near(mirror[old_spec.final_item_code].gf_delivered_qty, 3), "legacy delivered pieces mirror")
	expect(near(mirror["GFV-ORDINARY"].delivered_qty, 3), "ordinary delivered qty")
	frappe.db.commit()
	return f"{so.name}: m2 row 1.92 m2 = 2 pcs, legacy Nos row 3 pcs, ordinary 4 Nos (3 delivered); DN {dn.name}"


def delivery_limits_and_multiple_jobs() -> str:
	"""4 pieces on one row: two jobs, and every limit compares pieces with pieces."""
	batch_no = raw_batch(sheets=4)
	spec = make_spec(company_currency(), "GFV Selling", 4, manual_rate=500)
	so = frappe.get_doc("Sales Order", add_spec_to_transaction(spec.name, "Sales Order")["name"])
	so.submit()
	so.reload()
	expect(near(so.items[0].qty, 3.84) and near(so.items[0].stock_qty, 4), "4 pieces = 3.84 m2")
	first = cutting_job_for(so, batch_no, pieces=2)
	expect(near(first.pieces[0].qty_assigned, 2), "first job pieces")
	second = make_cutting_job(so.name)
	second.reload()
	expect(len(second.pieces) == 1 and near(second.pieces[0].qty_required, 2), f"second job should take the 2 remaining pieces, got {[p.qty_required for p in second.pieces]}")
	second.source_sheets[0].batch_no = batch_no
	second.source_sheets[0].qty_consumed = 1
	second.save(ignore_permissions=True)
	second.submit()
	extra = frappe.get_doc({"doctype": "Cutting Job", "company": COMPANY, "status": "Planned"})
	extra.append("sales_orders", {"sales_order": so.name, "customer": so.customer})
	piece = second.pieces[0].as_dict()
	for key in ("name", "parent", "parentfield", "parenttype", "idx", "creation", "modified", "owner", "modified_by", "docstatus"):
		piece.pop(key, None)
	piece.update({"qty_required": 1, "qty_assigned": 1, "qty_cut": 1})
	extra.append("pieces", piece)
	extra.append("source_sheets", {"item_code": RAW, "source_role": "Raw Sheet", "warehouse": settings().raw_warehouse, "batch_no": batch_no, "qty_consumed": 1})
	frappe.db.commit()
	expect_failure(lambda: extra.insert(ignore_permissions=True), contains="exceeds ordered quantity")

	cut(first)
	processing = process(first)
	final_no = final_batch(processing)
	from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

	# Ordered 4 pieces, only 2 processed: three pieces (2.88 m2) must be refused in pieces.
	dn = frappe.get_doc(make_delivery_note(so.name))
	dn.items[0].qty = flt(3 / flt(dn.items[0].conversion_factor), 9)
	dn.items[0].batch_no, dn.items[0].use_serial_batch_fields = final_no, 1
	frappe.db.commit()
	expect_failure(lambda: dn.insert(ignore_permissions=True), contains="processed quantity of 2")
	dn_two = delivery_for(so, pieces=2, batch_no=final_no)
	expect(near(dn_two.items[0].stock_qty, 2) and near(dn_two.items[0].qty, 1.92), "two processed pieces delivered")
	mirror = so_row(so.name)
	expect(near(mirror.gf_delivered_qty, 2) and near(mirror.delivered_qty, 1.92), f"mirror {mirror.gf_delivered_qty}/{mirror.delivered_qty}")
	# Cancel the delivery: pieces come back on the mirror and ERPNext delivered qty.
	dn_two.cancel()
	mirror = so_row(so.name)
	expect(near(mirror.gf_delivered_qty, 0) and near(mirror.delivered_qty, 0), f"cancel did not release pieces {mirror.gf_delivered_qty}/{mirror.delivered_qty}")
	dn_again = delivery_for(so, pieces=2, batch_no=final_no)
	expect(near(delivered_pieces(so.items[0].name), 2), "delivered pieces after re-delivery")
	cut(second)
	process(second)
	frappe.db.commit()
	return f"{so.name}: 2 jobs x 2 pieces; over-assignment refused; 3-piece delivery refused; DN {dn_again.name}"


def valuation_by_batch_and_capitalization() -> str:
	currency = company_currency()
	cheap, dear = _batch(RAW, "CHEAP"), _batch(RAW, "DEAR")
	_receipt(RAW, settings().raw_warehouse, 2, 100, cheap)
	_receipt(RAW, settings().raw_warehouse, 2, 900, dear)
	rates = {}
	for label, batch_no in (("cheap", cheap), ("dear", dear)):
		spec = make_spec(currency, "GFV Selling", 1, manual_rate=500)
		_quotation, so = quote_and_order(spec)
		job = cut(cutting_job_for(so, batch_no))
		cut_entry = frappe.get_doc("Stock Entry", job.linked_stock_entry)
		cut_row = next(r for r in cut_entry.items if r.t_warehouse and r.is_finished_item)
		settings_doc = settings()
		settings_doc.capitalize_processing_costs = 0
		settings_doc.save(ignore_permissions=True)
		processing = process(job)
		entry = frappe.get_doc("Stock Entry", processing.linked_stock_entry)
		outgoing = next(r for r in entry.items if r.s_warehouse)
		finished = next(r for r in entry.items if r.t_warehouse and r.is_finished_item)
		expect(flt(outgoing.basic_rate) > 0, f"{label}: processing consumed Cut WIP at zero")
		expect(near(outgoing.basic_rate, cut_row.valuation_rate, 3), f"{label}: Cut WIP out {outgoing.basic_rate} != in {cut_row.valuation_rate}")
		expect(near(finished.valuation_rate, outgoing.basic_rate, 3), f"{label}: finished {finished.valuation_rate} != material {outgoing.basic_rate}")
		ledger = frappe.db.sql(
			"select ifnull(sum(stock_value_difference),0) from `tabStock Ledger Entry` where voucher_no=%s and is_cancelled=0",
			entry.name,
		)[0][0]
		expect(abs(flt(ledger)) < 0.01, f"{label}: repack ledger does not net to zero ({ledger})")
		rates[label] = flt(finished.valuation_rate, 4)
	expect(rates["dear"] > rates["cheap"] * 5, f"batches were not valued separately {rates}")

	# Capitalization on: finished goods absorb material plus the internal processing cost.
	batch_no = _batch(RAW, "CAP")
	_receipt(RAW, settings().raw_warehouse, 2, 900, batch_no)
	spec = make_spec(currency, "GFV Selling", 1, manual_rate=500)
	_quotation, so = quote_and_order(spec)
	job = cut(cutting_job_for(so, batch_no))
	account = frappe.db.get_value("Account", {"company": COMPANY, "root_type": "Expense", "is_group": 0}, "name")
	settings_doc = settings()
	settings_doc.capitalize_processing_costs = 1
	settings_doc.processing_cost_expense_account = account
	settings_doc.save(ignore_permissions=True)
	processing = process(job)
	entry = frappe.get_doc("Stock Entry", processing.linked_stock_entry)
	outgoing = next(r for r in entry.items if r.s_warehouse)
	finished = next(r for r in entry.items if r.t_warehouse and r.is_finished_item)
	extra = flt(entry.additional_costs[0].amount) if entry.additional_costs else 0
	expect(extra > 0, "capitalization added no additional cost")
	expect(near(finished.valuation_rate, flt(outgoing.basic_rate) + extra, 2), f"capitalized {finished.valuation_rate} != {outgoing.basic_rate} + {extra}")
	settings_doc = settings()
	settings_doc.capitalize_processing_costs = 0
	settings_doc.save(ignore_permissions=True)
	frappe.db.commit()
	return f"cheap batch finished at {rates['cheap']}, dear batch at {rates['dear']}; capitalized {outgoing.basic_rate} + {extra} = {finished.valuation_rate}"


def draft_balances() -> str:
	before = snapshot()
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = se.purpose = "Material Receipt"
	se.company = COMPANY
	se.append("items", {"item_code": RAW, "t_warehouse": settings().raw_warehouse, "qty": 1, "basic_rate": 50})
	se.insert(ignore_permissions=True)
	se.save(ignore_permissions=True)
	se.delete()
	frappe.db.commit()
	expect(snapshot() == before, "draft receipt changed balances")
	return "draft receipt save + delete: bin, stock ledger, GL unchanged"


def draft_cut_wip_to_finished_goods() -> str:
	"""Save and delete a real draft Cut WIP -> Finished Goods Stock Entry."""
	batch_no = raw_batch()
	spec = make_spec(company_currency(), "GFV Selling", 2, manual_rate=500)
	_quotation, so = quote_and_order(spec)
	job = cut(cutting_job_for(so, batch_no))
	processing = process(job, post=False)
	result = processing.create_repack_stock_entry()
	entry_name = result["stock_entry"]
	entry = frappe.get_doc("Stock Entry", entry_name)
	expect(entry.docstatus == 0 and entry.gf_glass_stock_flow == "Cut WIP to Final", "not a draft Cut WIP to Final entry")
	frappe.db.commit()
	before = snapshot()
	entry.reload()
	entry.save(ignore_permissions=True)
	entry.reload()
	entry.save(ignore_permissions=True)
	expect(snapshot() == before, "saving the draft changed Bin, Stock Ledger, or GL")
	outgoing = next(r for r in entry.items if r.s_warehouse)
	expect(flt(outgoing.basic_rate) > 0, "draft consumes Cut WIP at zero rate")
	frappe.db.set_value("Glass Processing Job", processing.name, "linked_stock_entry", None, update_modified=False)
	frappe.delete_doc("Stock Entry", entry_name, force=True, ignore_permissions=True)
	frappe.db.commit()
	expect(not frappe.db.exists("Stock Entry", entry_name), "draft was not deleted")
	expect(snapshot() == before, "deleting the draft changed Bin, Stock Ledger, or GL")
	return f"draft {entry_name} saved twice and deleted; {len(before['bins'])} bins, SLE {before['sle']}, GL {before['gl']} unchanged"


def missing_valuation() -> str:
	before = snapshot()
	cut_item = "GLS-CLEAR-8MM-1111X1111"
	final_item = "GLS-CLEAR-8MM-1112X1112"
	for code, role, group in (
		(cut_item, "Cut WIP", settings().cut_wip_item_group),
		(final_item, "Final", settings().final_item_group),
	):
		if not frappe.db.exists("Item", code):
			item = frappe.new_doc("Item")
			item.item_code = item.item_name = code
			item.item_group, item.stock_uom, item.is_stock_item = group, "Nos", 1
			item.gf_glass_item_role = role
			item.insert(ignore_permissions=True)
	try:
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type, se.purpose, se.company = "Glass Processing Repack", "Repack", COMPANY
		se.gf_created_by_glass_factory, se.gf_glass_stock_flow = 1, "Cut WIP to Final"
		se.append("items", {"item_code": cut_item, "s_warehouse": settings().cut_wip_warehouse, "qty": 1, "gf_source_item_role": "Cut WIP"})
		se.append("items", {"item_code": final_item, "t_warehouse": settings().final_goods_warehouse, "qty": 1, "is_finished_item": 1, "gf_source_item_role": "Final"})
		se.insert(ignore_permissions=True)
		se.submit()
	except Exception as exc:
		frappe.db.rollback()
		message = str(exc)
		expect("valuation" in message.lower(), f"failure was not about valuation: {message[:200]}")
		expect(snapshot() == before, "failed valuation changed Bin, Stock Ledger, or GL")
		return message.split("<")[0][:160]
	frappe.throw("A processing movement with no posted cost was accepted")


def cancellation_and_recovery() -> str:
	batch_no = raw_batch()
	spec = make_spec(company_currency(), "GFV Selling", 2, manual_rate=500)
	_quotation, so = quote_and_order(spec)
	soi = so.items[0].name
	job = cut(cutting_job_for(so, batch_no))
	processing = process(job)
	final_no = final_batch(processing)
	delivery = delivery_for(so, pieces=1, batch_no=final_no)
	# Cutting cannot be cancelled while processing stands; final cannot while delivered.
	frappe.db.commit()
	for label, entry_name in (("cutting", job.linked_stock_entry), ("final", processing.linked_stock_entry)):
		message = expect_failure(lambda name=entry_name: frappe.get_doc("Stock Entry", name).cancel())
		expect(any(word in message.lower() for word in ("cancel", "deliver")), f"{label}: unclear message {message[:160]}")
	delivery.cancel()
	expect(near(so_row(so.name).gf_delivered_qty, 0), "delivery cancel did not release pieces")
	final_entry = processing.linked_stock_entry
	frappe.get_doc("Stock Entry", final_entry).cancel()
	processing.reload()
	expect(processing.status != "Final Stock Posted", "processing stayed posted")
	expect(near(so_row(so.name).gf_processed_qty, 0), "processed qty not released")
	recovered = processing.recover_cancelled_stock_movement()
	processing.reload()
	expect(not processing.linked_stock_entry, "recovery left the link")
	processing.create_repack_stock_entry()
	processing.reload()
	replacement = processing.linked_stock_entry
	expect(replacement and replacement != final_entry, "no replacement movement")
	processing.submit_repack_stock_entry()
	expect(near(so_row(so.name).gf_processed_qty, 2), "processed qty after replacement")
	frappe.db.commit()
	return f"cancelled {final_entry}, recovered, replaced by {replacement}; processed qty back to 2 pieces; delivery cancel released 1 piece"


def _race(make_call, workers=2, snapshot_isolation=True):
	"""Run make_call on `workers` independent connections at once.

	MariaDB 11.6+ defaults to innodb_snapshot_isolation=ON, which aborts a writer whose snapshot is
	stale. Older MariaDB and MySQL behave like OFF, so the lock must not depend on it.
	"""
	site, sites_path = frappe.local.site, frappe.utils.get_bench_path() + "/sites"
	barrier = threading.Barrier(workers)
	results: list = []

	def worker():
		frappe.init(site=site, sites_path=sites_path)
		frappe.connect()
		frappe.set_user("Administrator")
		if not snapshot_isolation:
			frappe.db.sql("SET SESSION innodb_snapshot_isolation=OFF")
		try:
			barrier.wait(timeout=30)
			out = make_call()
			frappe.db.commit()
			results.append(("ok", out, cint_(frappe.flags.get("gf_lock_retries"))))
		except Exception as exc:
			frappe.db.rollback()
			results.append(("err", f"{type(exc).__name__}: {str(exc)[:120]}", cint_(frappe.flags.get("gf_lock_retries"))))
		finally:
			frappe.destroy()

	threads = [threading.Thread(target=worker) for _ in range(workers)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join(60)
	frappe.db.rollback()  # leave this connection's snapshot behind and read committed state
	return results


def _movements(filters):
	return frappe.get_all("Stock Entry", filters=filters, fields=["name", "docstatus"], order_by="creation asc")


def _concurrency_checks(snapshot_isolation: bool) -> str:
	batch_no = raw_batch()
	spec = make_spec(company_currency(), "GFV Selling", 2, manual_rate=500)
	_quotation, so = quote_and_order(spec)
	soi = so.items[0].name
	job = cutting_job_for(so, batch_no)
	cut_filter = {"gf_cutting_job": job.name, "gf_glass_stock_flow": "Raw to Cut WIP"}
	frappe.db.commit()

	# 1. Two connections create the cutting movement at the same time.
	r1 = _race(lambda: frappe.get_doc("Cutting Job", job.name).create_repack_stock_entry().get("stock_entry"), snapshot_isolation=snapshot_isolation)
	movements = _movements(cut_filter)
	expect(len(movements) == 1, f"cutting create left {movements}; results {r1}")
	expect(frappe.db.get_value("Cutting Job", job.name, "linked_stock_entry") == movements[0].name, "job does not link the surviving movement")
	expect(len({result[1] for result in r1 if result[0] == "ok"}) <= 1, f"both calls got different movements {r1}")

	# 2. Two connections submit it at the same time.
	r2 = _race(lambda: frappe.get_doc("Cutting Job", job.name).submit_repack_stock_entry().get("stock_entry"), snapshot_isolation=snapshot_isolation)
	movements = _movements(cut_filter)
	expect(len(movements) == 1 and movements[0].docstatus == 1, f"cutting submit left {movements}; results {r2}")
	sle_rows = frappe.db.count("Stock Ledger Entry", {"voucher_no": movements[0].name, "is_cancelled": 0})
	single = frappe.db.sql("select count(*) from `tabStock Entry Detail` where parent=%s", movements[0].name)[0][0]
	expect(sle_rows == single, f"stock ledger rows {sle_rows} != entry rows {single}: posted twice")
	expect(near(so_row(so.name).gf_cut_qty, 2), f"cut qty {so_row(so.name).gf_cut_qty}")
	expect(frappe.db.get_value("Cutting Job", job.name, "status") == "Cut Stock Posted", "cutting status")

	# 3 and 4. The same for the processing stage.
	processing = process(frappe.get_doc("Cutting Job", job.name), post=False)
	final_filter = {"gf_processing_job": processing.name, "gf_glass_stock_flow": "Cut WIP to Final"}
	frappe.db.commit()
	r3 = _race(lambda: frappe.get_doc("Glass Processing Job", processing.name).create_repack_stock_entry().get("stock_entry"), snapshot_isolation=snapshot_isolation)
	movements = _movements(final_filter)
	expect(len(movements) == 1, f"processing create left {movements}; results {r3}")
	r4 = _race(lambda: frappe.get_doc("Glass Processing Job", processing.name).submit_repack_stock_entry().get("stock_entry"), snapshot_isolation=snapshot_isolation)
	movements = _movements(final_filter)
	expect(len(movements) == 1 and movements[0].docstatus == 1, f"processing submit left {movements}; results {r4}")
	sle_rows = frappe.db.count("Stock Ledger Entry", {"voucher_no": movements[0].name, "is_cancelled": 0})
	single = frappe.db.sql("select count(*) from `tabStock Entry Detail` where parent=%s", movements[0].name)[0][0]
	expect(sle_rows == single, f"final stock ledger rows {sle_rows} != entry rows {single}")
	expect(near(so_row(so.name).gf_processed_qty, 2), f"processed qty {so_row(so.name).gf_processed_qty}")
	drafts = frappe.get_all("Stock Entry", filters={"docstatus": 0, "gf_created_by_glass_factory": 1, "gf_cutting_job": job.name})
	expect(not drafts, f"orphan drafts remain {drafts}")
	return f"cutting create {r1}; cutting submit {r2}; final create {r3}; final submit {r4}; one movement per stage, qty {so_row(so.name).gf_cut_qty}/{so_row(so.name).gf_processed_qty}"


def concurrency() -> str:
	return "snapshot isolation ON: " + _concurrency_checks(True)


def concurrency_snapshot_isolation_off() -> str:
	return "snapshot isolation OFF: " + _concurrency_checks(False)


def concurrency_without_lock_control() -> str:
	"""Negative control: the same race with the row lock removed.

	Removing the lock does not necessarily leave a duplicate: the deterministic output Batch name
	and ERPNext's timestamp check stop the second writer. What it does change is how the second
	request ends. Without the lock it fails with a database error; with the lock it reuses the
	first request's movement. Recorded here, on a server with innodb_snapshot_isolation=OFF.
	"""
	from glass_factory.glass_factory.doctype.cutting_job import cutting_job as cutting_module

	original = cutting_module.lock_job
	cutting_module.lock_job = lambda doctype, name: frappe.get_doc(doctype, name)  # plain read, no FOR UPDATE
	attempts = []
	try:
		for _ in range(3):
			batch_no = raw_batch()
			_quotation, so = quote_and_order(make_spec(company_currency(), "GFV Selling", 1, manual_rate=500))
			job = cutting_job_for(so, batch_no)
			frappe.db.commit()
			results = _race(
				lambda name=job.name: frappe.get_doc("Cutting Job", name).create_repack_stock_entry().get("stock_entry"),
				snapshot_isolation=False,
			)
			found = _movements({"gf_cutting_job": job.name, "gf_glass_stock_flow": "Raw to Cut WIP"})
			attempts.append({"movements": len(found), "results": [(r[0], r[1][:90]) for r in results]})
			for extra in found[1:]:
				frappe.db.set_value("Cutting Job", job.name, "linked_stock_entry", found[0].name, update_modified=False)
				frappe.delete_doc("Stock Entry", extra.name, force=True, ignore_permissions=True)
			frappe.db.commit()
	finally:
		cutting_module.lock_job = original
	duplicated = any(a["movements"] > 1 for a in attempts)
	failed_cleanly_not = any(r[0] == "err" for a in attempts for r in a["results"])
	expect(
		duplicated or failed_cleanly_not,
		f"without the lock both requests behaved identically to the locked run: {attempts}",
	)
	return f"without the lock: duplicate_committed={duplicated}, second request errored={failed_cleanly_not}; {attempts}"


def conversion_backfill() -> str:
	"""Existing Final items missing the Square Meter row, or carrying a wrong one, are repaired."""
	from glass_factory.install import ensure_glass_factory_setup

	item_code = "GLS-CLEAR-8MM-1200X800-POL-TMP"
	frappe.db.delete("UOM Conversion Detail", {"parent": item_code, "uom": "Square Meter"})
	frappe.db.commit()
	expect(not frappe.db.exists("UOM Conversion Detail", {"parent": item_code, "uom": "Square Meter"}), "row not removed")
	ensure_glass_factory_setup()
	restored = frappe.db.get_value("UOM Conversion Detail", {"parent": item_code, "uom": "Square Meter"}, "conversion_factor")
	expect(near(restored, 1.041666667, 9), f"missing row restored with {restored}")
	frappe.db.set_value("UOM Conversion Detail", {"parent": item_code, "uom": "Square Meter"}, "conversion_factor", 2.5)
	frappe.db.commit()
	ensure_glass_factory_setup()
	corrected = frappe.db.get_value("UOM Conversion Detail", {"parent": item_code, "uom": "Square Meter"}, "conversion_factor")
	expect(near(corrected, 1.041666667, 9), f"wrong factor corrected to {corrected}")
	raw = frappe.db.get_value("UOM Conversion Detail", {"parent": RAW, "uom": "Square Meter"}, "conversion_factor")
	expect(near(raw, 1 / 6.0, 8), f"raw sheet factor {raw} (pieces per m2 for a 6 m2 sheet)")
	return f"{item_code}: row restored {restored}, wrong 2.5 corrected to {corrected}; raw sheet {RAW} factor {raw}"


def customer_print_formats() -> str:
	"""What a customer sees: area, rate per m2, amount; no cost anywhere in the standard prints."""
	quotation = frappe.db.sql(
		"""select qi.parent from `tabQuotation Item` qi join tabQuotation q on q.name = qi.parent
		where qi.uom = 'Square Meter' and q.docstatus = 1 and qi.rate = 500 and qi.qty = 1.92
		order by q.creation desc limit 1"""
	)[0][0]
	invoice = frappe.db.sql(
		"""select sii.parent from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
		where sii.uom = 'Square Meter' and si.docstatus = 1 and sii.rate = 500 order by si.creation desc limit 1"""
	)[0][0]
	delivery = frappe.db.sql(
		"""select dni.parent from `tabDelivery Note Item` dni join `tabDelivery Note` dn on dn.name = dni.parent
		where dni.uom = 'Square Meter' and dn.docstatus = 1 order by dn.creation desc limit 1"""
	)[0][0]
	forbidden = ("Internal Cost", "Estimated Cost", "cost_rate", "cost_amount", "Profit /", "Profit %", "Total Margin", "Gross Profit", "Buying Cost", "gross_profit")
	seen = {}
	for doctype, name in (("Quotation", quotation), ("Sales Invoice", invoice), ("Delivery Note", delivery)):
		html = frappe.get_print(doctype, name, no_letterhead=1)
		expect("Square Meter" in html or "m\u00b2" in html, f"{doctype} {name} does not show the area unit")
		leaks = [word for word in forbidden if word.lower() in html.lower()]
		expect(not leaks, f"{doctype} {name} print shows {leaks}")
		seen[doctype] = {"name": name, "shows_area_unit": True}
	html = frappe.get_print("Quotation", quotation, no_letterhead=1)
	for needle in ("1.92", "500", "960"):
		expect(needle in html, f"quotation print is missing {needle}")
	spec_name = frappe.db.get_value("Glass Product Specification", {"company": COMPANY}, "name")
	spec_html = frappe.get_print("Glass Product Specification", spec_name, no_letterhead=1)
	spec_leaks = [word for word in ("Internal Cost", "Estimated Cost", "Profit /", "Profit %", "Total Margin", "Buying Cost") if word.lower() in spec_html.lower()]
	expect(not spec_leaks, f"specification print shows {spec_leaks}")
	return f"quotation {quotation} shows 1.92 Square Meter, 500, 960; invoice {invoice}, delivery {delivery}, specification print: no cost or margin text"


def permissions() -> str:
	if not frappe.db.exists("Role", "GF Validation Reader"):
		frappe.get_doc({"doctype": "Role", "role_name": "GF Validation Reader", "desk_access": 1}).insert(ignore_permissions=True)
	if not frappe.db.exists("Custom DocPerm", {"parent": "Cutting Job", "role": "GF Validation Reader", "permlevel": 0}):
		frappe.permissions.add_permission("Cutting Job", "GF Validation Reader", 0)
	for perm, value in (("read", 1), ("write", 0), ("create", 0), ("submit", 0)):
		frappe.permissions.update_permission_property("Cutting Job", "GF Validation Reader", 0, perm, value)
	reader = _user("gf-reader@validation.local", "GF Validation Reader")
	sales = _user("gf-sales@validation.local", "Glass Sales User")
	other = _user("gf-other@validation.local", "Glass Cutting Operator")
	if not frappe.db.exists("User Permission", {"user": other, "allow": "Company", "for_value": OTHER_COMPANY}):
		frappe.get_doc({"doctype": "User Permission", "user": other, "allow": "Company", "for_value": OTHER_COMPANY}).insert(ignore_permissions=True)
	job_name = frappe.db.get_value("Cutting Job", {"company": COMPANY, "docstatus": 1, "linked_stock_entry": ["is", "not set"]}, "name")
	if not job_name:
		batch_no = raw_batch()
		_q, so = quote_and_order(make_spec(company_currency(), "GFV Selling", 1, manual_rate=500))
		job_name = cutting_job_for(so, batch_no).name
	so_name = frappe.db.get_value("Sales Order", {"company": COMPANY, "docstatus": 1}, "name")
	frappe.db.commit()
	denied = {}
	for label, user, call in (
		("reader create movement", reader, lambda: frappe.get_doc("Cutting Job", job_name).create_repack_stock_entry()),
		("reader submit movement", reader, lambda: frappe.get_doc("Cutting Job", job_name).submit_repack_stock_entry()),
		("other-company operator submit", other, lambda: frappe.get_doc("Cutting Job", job_name).submit_repack_stock_entry()),
		("sales user make_cutting_job", sales, lambda: make_cutting_job(so_name)),
	):
		frappe.set_user(user)
		try:
			call()
		except frappe.PermissionError:
			denied[label] = "PermissionError"
		else:
			frappe.set_user("Administrator")
			frappe.throw(f"{label} was allowed")
		finally:
			frappe.set_user("Administrator")
			frappe.db.rollback()
	return frappe.as_json(denied)


def optimizer() -> str:
	from glass_factory.glass_factory import glass_optimizer as optimizer_module
	from glass_factory.glass_factory.glass_optimizer import export_optimization_job, import_optimization_result

	job_name = frappe.db.get_value("Cutting Job", {"company": COMPANY, "docstatus": 1}, "name")
	frappe.db.set_single_value("Glass Factory Settings", "enable_cop", 0)
	for call in (
		lambda: export_optimization_job(job_name),
		lambda: import_optimization_result(job_name, json_text="{}"),
	):
		try:
			call()
		except Exception as exc:
			expect("COP is disabled" in str(exc), f"unexpected {exc}")
		else:
			frappe.throw("Optimizer ran while COP was disabled")
	script = frappe.get_app_path("glass_factory", "glass_factory", "doctype", "cutting_job", "cutting_job.js")
	text = open(script).read()
	expect('get_single_value("Glass Factory Settings", "enable_cop")' in text and "Export Optimization Job" in text, "form is not gated on enable_cop")
	expect(text.index("Export Optimization Job") > text.index("enable_cop"), "optimizer buttons are outside the COP gate")
	frappe.db.set_single_value("Glass Factory Settings", "enable_cop", 1)
	exported = export_optimization_job(job_name)
	expect(frappe.db.get_value("File", {"file_url": exported["file_url"]}, "is_private"), "export file is public")
	marker = frappe.get_doc({"doctype": "Cutting Job Optimization Used Sheet", "parent": job_name, "parentfield": "optimization_used_sheets", "parenttype": "Cutting Job", "sheet_id": "MARKER", "used_qty": 1})
	marker.insert(ignore_permissions=True)
	frappe.db.commit()
	original = optimizer_module._persist_import_to_db

	def fail_after_write(*args, **kwargs):
		original(*args, **kwargs)
		frappe.throw("forced import failure")

	optimizer_module._persist_import_to_db = fail_after_write
	try:
		import_optimization_result(
			job_name,
			json_text=frappe.as_json(
				{"cutting_job": job_name, "status": "completed", "used_sheets": [{"sheet_id": "S1", "used_qty": 1}],
				 "placed_pieces": [{"piece_id": "P1", "item_code": RAW, "length_mm": 1000, "width_mm": 1000, "qty": 1, "source_sheet_id": "S1"}]}
			),
		)
	except Exception:
		frappe.db.rollback()
	finally:
		optimizer_module._persist_import_to_db = original
	kept = frappe.db.get_value("Cutting Job Optimization Used Sheet", {"parent": job_name, "sheet_id": "MARKER"}, "name")
	imported = frappe.db.get_value("Cutting Job Optimization Used Sheet", {"parent": job_name, "sheet_id": "S1"}, "name")
	expect(kept and not imported, f"failed import was committed (marker={kept}, imported={imported})")
	frappe.set_user("gf-reader@validation.local")
	try:
		export_optimization_job(job_name)
	except frappe.PermissionError:
		pass
	else:
		frappe.throw("read-only user exported an optimization job")
	finally:
		frappe.set_user("Administrator")
	frappe.db.set_single_value("Glass Factory Settings", "enable_cop", 0)
	frappe.db.commit()
	return "disabled: export+import refused; enabled: private file, failed import rolled back, read-only user refused"


SCENARIOS = (
	("prepare", prepare),
	("pricing_costs", pricing_costs),
	("m2_acceptance_tzs", m2_acceptance_tzs),
	("m2_acceptance_usd", m2_acceptance_usd),
	("row_rate_override", row_rate_override),
	("odd_dimensions_partial", odd_dimensions_partial),
	("legacy_and_mixed_order", legacy_and_mixed_order),
	("delivery_limits_and_multiple_jobs", delivery_limits_and_multiple_jobs),
	("valuation_by_batch_and_capitalization", valuation_by_batch_and_capitalization),
	("draft_balances", draft_balances),
	("draft_cut_wip_to_finished_goods", draft_cut_wip_to_finished_goods),
	("missing_valuation", missing_valuation),
	("cancellation_and_recovery", cancellation_and_recovery),
	("concurrency", concurrency),
	("concurrency_snapshot_isolation_off", concurrency_snapshot_isolation_off),
	("concurrency_without_lock_control", concurrency_without_lock_control),
	("cost_visibility", cost_visibility),
	("conversion_backfill", conversion_backfill),
	("customer_print_formats", customer_print_formats),
	("permissions", permissions),
	("optimizer", optimizer),
)


def run(only: str | None = None) -> dict:
	frappe.set_user("Administrator")
	for name, fn in SCENARIOS:
		if only and name not in only.split(","):
			continue
		check(name, fn)
	failed = [row for row in RESULTS if row["result"] != "passed"]
	print("SUMMARY", len(RESULTS) - len(failed), "passed,", len(failed), "failed")
	if failed:
		frappe.throw(f"{len(failed)} validation scenario(s) failed")
	return {"passed": len(RESULTS)}
