# Glass Factory

Phase 0 implements the manual simple-glass MVP for ERPNext/Frappe v16.

## Phase 0 flow

Commercial flow:

`Quotation -> Sales Order -> Delivery Note -> Sales Invoice`

Production flow:

`Sales Order -> Cutting Job -> Stock Entry Repack #1 -> Glass Processing Job -> Stock Entry Repack #2 -> Delivery Note -> Sales Invoice`

The exact final processed glass Item is created or reused before Sales Order submission. The same final Item must remain on Quotation Item, Sales Order Item, Delivery Note Item, and Sales Invoice Item.

Examples:

- Raw Sheet Item: `GLS-CLEAR-8MM-3210X2250`
- Cut WIP Item: `GLS-CLEAR-8MM-1200X800-CUT`
- Final Item: `GLS-CLEAR-8MM-1200X800-POL-HOL-TMP`

## Manual MVP

- Cutting Job groups one or more submitted Sales Orders.
- Repack #1 consumes Raw Sheet or Remnant Items and produces Cut WIP, Remnant, and Scrap Items.
- Glass Processing Job consumes Cut WIP Items and produces the exact final Sales Order Items.
- Delivery Note must be created from Sales Order and deliver the same final Item.
- Partial delivery uses standard ERPNext Sales Order and Delivery Note behavior.

## Exclusions

Simple glass processing does not use BOM, Work Order, Production Plan, or Job Card.

## COP

COP support is dormant/optional in Phase 0. Manual sheet and piece entry must work without COP uploads or utilization approval.
