"""Posted stock movements for cutting and processing jobs.

A movement is valid only while its Stock Entry is submitted. Cancelled entries
stay linked until an explicit recovery clears that link. Recovery never inserts
a replacement Stock Entry.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

CUTTING_FLOW = "Raw to Cut WIP"
PROCESSING_FLOW = "Cut WIP to Final"
CUTTING_OUTPUT_ROLE = "Cut WIP"
PROCESSING_OUTPUT_ROLE = "Final"


def lock_job(doctype: str, name: str):
	"""Re-read the job under a row lock inside the current transaction."""
	if not name:
		frappe.throw(_("{0} must be saved before a stock movement can be created.").format(doctype))
	if not frappe.db.transaction_writes:
		# Only reads so far. End that snapshot so the locking read below starts a fresh one:
		# every later read in this request then sees whatever a competing request committed
		# while this one waited for the lock. Never done once this request has written.
		frappe.db.rollback()
	try:
		return frappe.get_doc(doctype, name, for_update=True)
	except frappe.QueryDeadlockError:
		# MariaDB refuses a locking read of a row another transaction committed after this
		# transaction's snapshot (error 1020). Start over on a fresh snapshot so the re-read
		# sees that commit and the caller reuses the movement it created.
		frappe.flags.gf_lock_retries = cint(frappe.flags.get("gf_lock_retries")) + 1
		frappe.db.rollback()
		return frappe.get_doc(doctype, name, for_update=True)


def movement_docstatus(stock_entry: str | None) -> int | None:
	if not stock_entry:
		return None
	value = frappe.db.get_value("Stock Entry", stock_entry, "docstatus")
	if value is None:
		return None
	return cint(value)


def assert_movement_belongs(stock_entry, *, flow: str, cutting_job: str | None = None, processing_job: str | None = None) -> None:
	if not stock_entry:
		frappe.throw(_("Stock movement was not found."))
	if not cint(stock_entry.get("gf_created_by_glass_factory")):
		frappe.throw(
			_("Stock Entry {0} was not created by Glass Factory and cannot be used for this job.").format(
				stock_entry.name
			)
		)
	actual_flow = stock_entry.get("gf_glass_stock_flow") or ""
	if actual_flow != flow:
		frappe.throw(
			_("Stock Entry {0} is a {1} movement. This action requires {2}.").format(
				stock_entry.name, actual_flow or _("different"), flow
			)
		)
	if cutting_job and stock_entry.get("gf_cutting_job") != cutting_job:
		frappe.throw(
			_("Stock Entry {0} belongs to Cutting Job {1}, not {2}.").format(
				stock_entry.name, stock_entry.get("gf_cutting_job") or _("another job"), cutting_job
			)
		)
	if processing_job and stock_entry.get("gf_processing_job") != processing_job:
		frappe.throw(
			_("Stock Entry {0} belongs to Glass Processing Job {1}, not {2}.").format(
				stock_entry.name, stock_entry.get("gf_processing_job") or _("another job"), processing_job
			)
		)


def submitted_movements(*, flow: str, cutting_job: str | None = None, processing_job: str | None = None) -> list[str]:
	filters = {
		"docstatus": 1,
		"gf_created_by_glass_factory": 1,
		"gf_glass_stock_flow": flow,
	}
	if cutting_job:
		filters["gf_cutting_job"] = cutting_job
	if processing_job:
		filters["gf_processing_job"] = processing_job
	return frappe.get_all("Stock Entry", filters=filters, pluck="name", order_by="posting_date asc, name asc")


def reject_cancelled_movement(stock_entry_name: str) -> None:
	frappe.throw(
		_(
			"Stock Entry {0} is cancelled. Glass Factory will not recreate it automatically. "
			"Use Recover Cancelled Movement after the downstream documents are cancelled, "
			"then create a new movement."
		).format(stock_entry_name),
		title=_("Cancelled Stock Movement"),
	)


def guard_linked_movement(job, *, flow: str) -> str | None:
	"""Return the linked Stock Entry name, or raise when it cannot be reused."""
	linked = job.linked_stock_entry
	if not linked:
		others = submitted_movements(
			flow=flow,
			cutting_job=job.name if flow == CUTTING_FLOW else job.cutting_job,
			processing_job=job.name if flow == PROCESSING_FLOW else None,
		)
		if others:
			frappe.throw(
				_("Submitted stock movement {0} already exists for this job. Link it before creating another.").format(
					", ".join(others)
				)
			)
		return None

	stock_entry = frappe.get_doc("Stock Entry", linked)
	assert_movement_belongs(
		stock_entry,
		flow=flow,
		cutting_job=job.cutting_job if flow == PROCESSING_FLOW else job.name,
		processing_job=job.name if flow == PROCESSING_FLOW else None,
	)
	if cint(stock_entry.docstatus) == 2:
		reject_cancelled_movement(stock_entry.name)
	if cint(stock_entry.docstatus) == 1:
		return stock_entry.name
	if cint(stock_entry.docstatus) != 0:
		frappe.throw(_("Stock Entry {0} is not a draft or submitted movement.").format(stock_entry.name))
	return stock_entry.name


def sales_order_items_for_job(job) -> list[str]:
	items = []
	for row in job.get("pieces") or job.get("outputs") or []:
		if row.sales_order_item and row.sales_order_item not in items:
			items.append(row.sales_order_item)
	return items


def output_qty_by_sales_order_item(
	*,
	flow: str,
	cutting_job: str | None = None,
	processing_job: str | None = None,
	exclude_stock_entry: str | None = None,
	job=None,
) -> dict[str, float]:
	"""Sum submitted output quantities. Cancelled and excluded vouchers contribute nothing."""
	names = submitted_movements(flow=flow, cutting_job=cutting_job, processing_job=processing_job)
	if exclude_stock_entry:
		names = [name for name in names if name != exclude_stock_entry]
	role = CUTTING_OUTPUT_ROLE if flow == CUTTING_FLOW else PROCESSING_OUTPUT_ROLE
	totals: dict[str, float] = {}
	if not names:
		return totals
	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={
			"parent": ["in", names],
			"parenttype": "Stock Entry",
			"gf_source_item_role": role,
			"t_warehouse": ["is", "set"],
		},
		fields=["gf_sales_order_item", "item_code", "qty"],
	)
	if any(row.gf_sales_order_item for row in rows):
		for row in rows:
			if not row.gf_sales_order_item:
				continue
			totals[row.gf_sales_order_item] = flt(totals.get(row.gf_sales_order_item)) + flt(row.qty)
		return totals
	if not rows:
		return totals

	item_field = "cut_wip_item" if flow == CUTTING_FLOW else "final_item"
	job_rows = list((job.get("pieces") or job.get("outputs") or []) if job else [])
	for row in rows:
		matches = {
			piece.sales_order_item
			for piece in job_rows
			if piece.get(item_field) == row.item_code and piece.get("sales_order_item")
		}
		if len(matches) != 1:
			frappe.throw(
				_(
					"Stock movement output {0} is not linked to exactly one Sales Order row, "
					"so production quantities were not changed."
				).format(row.item_code)
			)
		sales_order_item = next(iter(matches))
		totals[sales_order_item] = flt(totals.get(sales_order_item)) + flt(row.qty)
	return totals


def output_qty_for_sales_order_items(
	flow: str, sales_order_items: list[str], exclude_stock_entry: str | None = None
) -> dict[str, float]:
	"""Sum every remaining submitted movement for these Sales Order rows, across jobs."""
	totals = {item: 0.0 for item in sales_order_items}
	if not sales_order_items:
		return totals
	role = CUTTING_OUTPUT_ROLE if flow == CUTTING_FLOW else PROCESSING_OUTPUT_ROLE
	names = frappe.get_all(
		"Stock Entry",
		filters={"docstatus": 1, "gf_created_by_glass_factory": 1, "gf_glass_stock_flow": flow},
		pluck="name",
	)
	if exclude_stock_entry:
		names = [name for name in names if name != exclude_stock_entry]
	if not names:
		return totals
	rows = frappe.get_all(
		"Stock Entry Detail",
		filters={
			"parent": ["in", names],
			"parenttype": "Stock Entry",
			"gf_source_item_role": role,
			"t_warehouse": ["is", "set"],
			"gf_sales_order_item": ["in", sales_order_items],
		},
		fields=["gf_sales_order_item", "qty"],
	)
	for row in rows:
		totals[row.gf_sales_order_item] = flt(totals.get(row.gf_sales_order_item)) + flt(row.qty)
	return totals


def sync_sales_order_quantities(job, *, flow: str, exclude_stock_entry: str | None = None) -> None:
	fieldname = "gf_cut_qty" if flow == CUTTING_FLOW else "gf_processed_qty"
	totals = output_qty_for_sales_order_items(
		flow,
		sales_order_items_for_job(job),
		exclude_stock_entry=exclude_stock_entry,
	)
	for sales_order_item in sales_order_items_for_job(job):
		frappe.db.set_value(
			"Sales Order Item",
			sales_order_item,
			fieldname,
			flt(totals.get(sales_order_item)),
			update_modified=False,
		)


def mark_posted_after_submit(job, stock_entry_name: str) -> None:
	stock_entry = frappe.get_doc("Stock Entry", stock_entry_name)
	if cint(stock_entry.docstatus) != 1:
		frappe.throw(
			_("Stock Entry {0} is not submitted, so {1} {2} was not marked as posted.").format(
				stock_entry_name, job.doctype, job.name
			)
		)
	assert_movement_belongs(
		stock_entry,
		flow=stock_entry.gf_glass_stock_flow,
		cutting_job=job.cutting_job if job.doctype == "Glass Processing Job" else job.name,
		processing_job=job.name if job.doctype == "Glass Processing Job" else None,
	)
	flow = stock_entry.gf_glass_stock_flow
	sync_sales_order_quantities(job, flow=flow)
	if flow == CUTTING_FLOW:
		job.db_set("status", "Cut Stock Posted")
	elif flow == PROCESSING_FLOW:
		job.db_set("status", "Final Stock Posted")


def non_cancelled_processing_jobs(cutting_job: str) -> list[str]:
	return frappe.get_all(
		"Glass Processing Job",
		filters={"cutting_job": cutting_job, "docstatus": ["!=", 2]},
		pluck="name",
	)


def assert_cutting_movement_can_cancel(cutting_job: str) -> None:
	processing_jobs = non_cancelled_processing_jobs(cutting_job)
	if processing_jobs:
		frappe.throw(
			_("Cancel or delete Glass Processing Job {0} before cancelling the cutting stock movement.").format(
				", ".join(processing_jobs)
			)
		)
	processing_stock = submitted_movements(flow=PROCESSING_FLOW, cutting_job=cutting_job)
	if processing_stock:
		frappe.throw(
			_("Cancel submitted processing movement {0} before cancelling the cutting stock movement.").format(
				", ".join(processing_stock)
			)
		)


def assert_processing_movement_can_cancel(processing_job) -> None:
	for sales_order_item in sales_order_items_for_job(processing_job):
		delivered = flt(frappe.db.get_value("Sales Order Item", sales_order_item, "delivered_qty"))
		if delivered > 0:
			frappe.throw(
				_(
					"Sales Order Item {0} has delivered quantity {1}. "
					"Cancel that delivery before cancelling the final stock movement."
				).format(sales_order_item, delivered)
			)


def before_cancel_stock_entry(stock_entry) -> None:
	if not cint(stock_entry.get("gf_created_by_glass_factory")):
		return
	flow = stock_entry.get("gf_glass_stock_flow")
	if flow == CUTTING_FLOW and stock_entry.get("gf_cutting_job"):
		assert_cutting_movement_can_cancel(stock_entry.gf_cutting_job)
	elif flow == PROCESSING_FLOW and stock_entry.get("gf_processing_job"):
		job = frappe.get_doc("Glass Processing Job", stock_entry.gf_processing_job)
		assert_processing_movement_can_cancel(job)
	# The job keeps its link until an explicit recovery. That link must not block cancellation
	# after the downstream checks above have passed.
	stock_entry.flags.ignore_links = True


def on_cancel_stock_entry(stock_entry) -> None:
	if not cint(stock_entry.get("gf_created_by_glass_factory")):
		return
	flow = stock_entry.get("gf_glass_stock_flow")
	if flow == CUTTING_FLOW and stock_entry.get("gf_cutting_job"):
		job = frappe.get_doc("Cutting Job", stock_entry.gf_cutting_job)
		sync_sales_order_quantities(job, flow=CUTTING_FLOW, exclude_stock_entry=stock_entry.name)
		if job.status == "Cut Stock Posted" and not submitted_movements(flow=CUTTING_FLOW, cutting_job=job.name):
			job.db_set("status", "Ready for Cutting")
	elif flow == PROCESSING_FLOW and stock_entry.get("gf_processing_job"):
		job = frappe.get_doc("Glass Processing Job", stock_entry.gf_processing_job)
		sync_sales_order_quantities(job, flow=PROCESSING_FLOW, exclude_stock_entry=stock_entry.name)
		if job.status == "Final Stock Posted" and not submitted_movements(
			flow=PROCESSING_FLOW, processing_job=job.name
		):
			job.db_set("status", "Processing In Progress")


def assert_job_cancel_allowed(job) -> None:
	if job.doctype == "Cutting Job":
		movements = submitted_movements(flow=CUTTING_FLOW, cutting_job=job.name)
		if movements:
			frappe.throw(
				_("Cancel submitted cutting movement {0} before cancelling Cutting Job {1}.").format(
					", ".join(movements), job.name
				)
			)
		processing_jobs = non_cancelled_processing_jobs(job.name)
		if processing_jobs:
			frappe.throw(
				_("Cancel or delete Glass Processing Job {0} before cancelling Cutting Job {1}.").format(
					", ".join(processing_jobs), job.name
				)
			)
	elif job.doctype == "Glass Processing Job":
		movements = submitted_movements(flow=PROCESSING_FLOW, processing_job=job.name)
		if movements:
			frappe.throw(
				_("Cancel submitted processing movement {0} before cancelling Glass Processing Job {1}.").format(
					", ".join(movements), job.name
				)
			)


def clear_cancelled_link(job, *, flow: str) -> str:
	"""Clear a cancelled movement link. Does not create a replacement."""
	from glass_factory.glass_factory.authorization import require_doc_permission

	require_doc_permission(job, "write")
	locked = lock_job(job.doctype, job.name)
	if not locked.linked_stock_entry:
		frappe.throw(_("There is no linked stock movement to recover."))
	stock_entry = frappe.get_doc("Stock Entry", locked.linked_stock_entry)
	assert_movement_belongs(
		stock_entry,
		flow=flow,
		cutting_job=locked.cutting_job if flow == PROCESSING_FLOW else locked.name,
		processing_job=locked.name if flow == PROCESSING_FLOW else None,
	)
	if cint(stock_entry.docstatus) != 2:
		frappe.throw(
			_("Stock Entry {0} is not cancelled. Only a cancelled movement link can be cleared.").format(
				stock_entry.name
			)
		)
	if flow == CUTTING_FLOW:
		assert_cutting_movement_can_cancel(locked.name)
		remaining = submitted_movements(flow=CUTTING_FLOW, cutting_job=locked.name)
	else:
		assert_processing_movement_can_cancel(locked)
		remaining = submitted_movements(flow=PROCESSING_FLOW, processing_job=locked.name)
	if remaining:
		frappe.throw(
			_("Submitted stock movement {0} is still valid. The cancelled link was not cleared.").format(
				", ".join(remaining)
			)
		)
	cancelled_name = stock_entry.name
	locked.db_set("linked_stock_entry", None)
	sync_sales_order_quantities(locked, flow=flow)
	if flow == CUTTING_FLOW and locked.status in ("Cut Stock Posted", "Ready for Cutting"):
		locked.db_set("status", "Ready for Cutting")
	elif flow == PROCESSING_FLOW and locked.status == "Final Stock Posted":
		locked.db_set("status", "Processing In Progress")
	return cancelled_name
