"""
db/schema.py
------------
Single source of truth for:
  - Raw DB column names  →  app-level alias names
  - Known join relationships (verified)
  - Suspected join relationships (UNKNOWN — must validate before use)
  - Business synonym map (user term → app field → possible raw fields)

CONVENTION
----------
  - All raw column names are preserved exactly as they appear in SQL Server.
  - App aliases use snake_case and match UI/business labels.
  - Fields marked UNKNOWN must be confirmed via DB introspection before use.
    Use db/validate.py queries to discover them.
  - Never infer a column name — mark it UNKNOWN and verify.
"""

# ===========================================================================
# FIELD MAPS  (raw_column -> app_alias)
# ===========================================================================

# ---------------------------------------------------------------------------
# dbo._ORDERS
# ---------------------------------------------------------------------------
ORDERS_FIELD_MAP: dict[str, str] = {
    "ORDER#":                       "order_number",
    "LINE#I":                       "line_number",
    "ACCOUNT#I":                    "account_number",
    # Raw value is YYYYMMDD string — parse to date in the app layer
    "ORDER_ENTRY_DATE_YYYYMMDD":    "order_entry_date_raw",
    # Aliased as eta_date in most contexts; po_eta_date in PO-comparison context
    "PO_ETA_DATE":                  "eta_date",
    "ITEM_MFGR_COLOR_PAT":          "sku",
    "QUANTITY_ORDERED":             "quantity_ordered",
    "UNIT_OF_MEASURE":              "unit_of_measure",
    "CREDIT_TYPE_CODE":             "credit_type_code",
}

# ---------------------------------------------------------------------------
# dbo.ITEM
# ---------------------------------------------------------------------------
ITEM_FIELD_MAP: dict[str, str] = {
    "ItemNumber":   "sku",                  # join target from _ORDERS.ITEM_MFGR_COLOR_PAT
    "ICCTR":        "cost_center",
    "IPRCCD":       "price_class",          # used to join dbo.PRICE (join key on PRICE side: UNKNOWN)
    "INAME":        "sku_description",
    "IDELIV":       "item_lead_time_days",  # business term: LT (primary source)
    "IINVEN":       "inventory_flag",
    "IIXREF":       "iixref",
}

# ---------------------------------------------------------------------------
# dbo.CLASSES  — used for credit-type decode; always filter CLCAT = 'CC'
# ---------------------------------------------------------------------------
CLASSES_FIELD_MAP: dict[str, str] = {
    "CLCAT":    "class_category",   # filter constant: 'CC'
    "CLCODE":   "class_code",       # joins _ORDERS.CREDIT_TYPE_CODE
    # description/label column name: UNKNOWN — verify with:
    #   SELECT TOP 1 * FROM dbo.CLASSES WHERE CLCAT = 'CC'
}

# ---------------------------------------------------------------------------
# dbo.PRICE  — price-class lookup/label
# ---------------------------------------------------------------------------
PRICE_FIELD_MAP: dict[str, str] = {
    # Join key on PRICE side: UNKNOWN
    # Suspected: joins via ITEM.IPRCCD (price_class) but PRICE column name unconfirmed
    # Verify with: SELECT TOP 1 * FROM dbo.PRICE
}

# ---------------------------------------------------------------------------
# dbo.PRODLINE  — product-line metadata; lead-time fallback source
# ---------------------------------------------------------------------------
PRODLINE_FIELD_MAP: dict[str, str] = {
    # Join key: UNKNOWN — likely via ITEM or _ORDERS; column name unconfirmed
    # Lead-time fallback column name: UNKNOWN
    # Verify with: SELECT TOP 1 * FROM dbo.PRODLINE
}

# ---------------------------------------------------------------------------
# dbo.ITEMSTK  — JSTOCK (reorder target) by SKU
# ---------------------------------------------------------------------------
ITEMSTK_FIELD_MAP: dict[str, str] = {
    "JSTOCK":   "jstock",   # business term: Jstk
    # Join key back to ITEM (column name on ITEMSTK side): UNKNOWN
    # Verify with: SELECT TOP 1 * FROM dbo.ITEMSTK
}

# ---------------------------------------------------------------------------
# dbo.ROLLS  — inventory by roll / location / status
# ---------------------------------------------------------------------------
ROLLS_FIELD_MAP: dict[str, str] = {
    "Available":    "available_quantity",   # business term: Inv (→ inventory_sy after UOM conversion)
    "RROLL#":       "roll_number",
    "RLOC1":        "location",
    "RCODE@":       "status_code",
    # Join key to ITEM/SKU (column name on ROLLS side): UNKNOWN
    # Verify with: SELECT TOP 1 * FROM dbo.ROLLS
}

# ---------------------------------------------------------------------------
# dbo.OPENIV  — receipt history (lead-time and receiving context)
# ---------------------------------------------------------------------------
OPENIV_FIELD_MAP: dict[str, str] = {
    "NPO#":     "purchase_order_number",
    "NDATE":    "receipt_date",
    # Join key linking OPENIV to _ORDERS or OPENPO_M: UNKNOWN
    # Verify with: SELECT TOP 1 * FROM dbo.OPENIV
}

# ---------------------------------------------------------------------------
# dbo.OPENPO_M  — message / fee lines tied to order + line references
# ---------------------------------------------------------------------------
OPENPO_M_FIELD_MAP: dict[str, str] = {
    # All raw field names: UNKNOWN — structure not yet provided
    # Suspected join: ORDER# + LINE# back to _ORDERS (column names unconfirmed)
    # Verify with: SELECT TOP 1 * FROM dbo.OPENPO_M
}

# ===========================================================================
# JOIN RELATIONSHIPS
# ===========================================================================

# ---------------------------------------------------------------------------
# VERIFIED joins (explicitly confirmed in domain notes)
# ---------------------------------------------------------------------------
VERIFIED_JOINS: list[dict] = [
    {
        "from_table":    "dbo._ORDERS",
        "from_col":      "ITEM_MFGR_COLOR_PAT",
        "to_table":      "dbo.ITEM",
        "to_col":        "ItemNumber",
        "join_type":     "INNER / LEFT",
        "cardinality":   "N:1  (many orders per SKU)",
        "extra_filter":  None,
        "notes":         "Primary SKU linkage",
    },
    {
        "from_table":    "dbo._ORDERS",
        "from_col":      "CREDIT_TYPE_CODE",
        "to_table":      "dbo.CLASSES",
        "to_col":        "CLCODE",
        "join_type":     "LEFT",
        "cardinality":   "N:1  (lookup/decode)",
        "extra_filter":  "CLASSES.CLCAT = 'CC'",
        "notes":         "Filter on CLCAT is mandatory — omitting it returns wrong rows",
    },
]

# ---------------------------------------------------------------------------
# UNVERIFIED joins (suspected — must validate column names before use)
# ---------------------------------------------------------------------------
UNVERIFIED_JOINS: list[dict] = [
    {
        "tables":           ("dbo.ITEM",      "dbo.PRICE"),
        "suspected_key":    "ITEM.IPRCCD  →  PRICE.???",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.PRICE",
    },
    {
        "tables":           ("dbo.ITEM",      "dbo.PRODLINE"),
        "suspected_key":    "UNKNOWN",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.PRODLINE",
    },
    {
        "tables":           ("dbo.ITEM",      "dbo.ITEMSTK"),
        "suspected_key":    "ITEM.ItemNumber  →  ITEMSTK.???",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.ITEMSTK",
    },
    {
        "tables":           ("dbo.ITEM",      "dbo.ROLLS"),
        "suspected_key":    "ITEM.ItemNumber  →  ROLLS.???",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.ROLLS",
    },
    {
        "tables":           ("dbo.OPENIV",    "dbo._ORDERS"),
        "suspected_key":    "OPENIV.NPO#  →  _ORDERS.???  (or via OPENPO_M)",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.OPENIV",
    },
    {
        "tables":           ("dbo.OPENPO_M",  "dbo._ORDERS"),
        "suspected_key":    "OPENPO_M.???  →  _ORDERS.ORDER# + LINE#I",
        "status":           "UNKNOWN",
        "validate_query":   "SELECT TOP 1 * FROM dbo.OPENPO_M",
    },
]

# ===========================================================================
# BUSINESS SYNONYM MAP
# user_term -> (app_field, possible_raw_fields, confidence, notes)
# ===========================================================================
SYNONYM_MAP: dict[str, dict] = {
    "PO": {
        "app_field":        "derived: effective open/pending PO quantity",
        "raw_fields":       ["_ORDERS.ORDER#", "OPENPO_M refs (structure UNKNOWN)"],
        "confidence":       "medium",
        "notes":            "Not a single raw column. Derived from pending/open PO business logic. Confirm formula.",
    },
    "partial": {
        "app_field":        "partial_received_po",
        "raw_fields":       ["UNKNOWN — derived flag; likely OPENIV + PO comparison"],
        "confidence":       "medium",
        "notes":            "Business logic flag; no single raw field confirmed.",
    },
    "qty": {
        "app_field":        "quantity_sy",
        "raw_fields":       ["_ORDERS.QUANTITY_ORDERED", "_ORDERS.UNIT_OF_MEASURE"],
        "confidence":       "high",
        "notes":            "Normalized to square yards. Raw quantity × UOM conversion factor.",
    },
    "Inv": {
        "app_field":        "inventory_sy",
        "raw_fields":       ["ROLLS.Available"],
        "confidence":       "high",
        "notes":            "Sum of ROLLS.Available after UOM conversion to square yards.",
    },
    "ADS": {
        "app_field":        "avg_daily_sales_sy",
        "raw_fields":       ["UNKNOWN — calculated/derived field"],
        "confidence":       "high",
        "notes":            "Formula and raw source table not yet confirmed.",
    },
    "LT": {
        "app_field":        "lead_time_days",
        "raw_fields":       ["ITEM.IDELIV (primary)", "PRODLINE.??? (fallback, field UNKNOWN)"],
        "confidence":       "high",
        "notes":            "Use ITEM.IDELIV first; fall back to PRODLINE lead-time when IDELIV is null/zero.",
    },
    "DR": {
        "app_field":        "days_until_runout",
        "raw_fields":       ["UNKNOWN — derived: inventory_sy / avg_daily_sales_sy"],
        "confidence":       "high",
        "notes":            "Computed field. Confirm exact formula (e.g. null handling when ADS = 0).",
    },
    "Jstk": {
        "app_field":        "jstock",
        "raw_fields":       ["ITEMSTK.JSTOCK"],
        "confidence":       "high",
        "notes":            "Direct mapping. Join key on ITEMSTK side UNKNOWN.",
    },
    "Reorder": {
        "app_field":        "reorder_qty_sy",
        "raw_fields":       ["UNKNOWN — derived"],
        "confidence":       "high",
        "notes":            "Business logic quantity. Formula not yet confirmed.",
    },
    "over_order_sy": {
        "app_field":        "over_order_sy",
        "raw_fields":       ["UNKNOWN — derived"],
        "confidence":       "high",
        "notes":            "Over-order risk logic. Formula not yet confirmed.",
    },
    "order date": {
        "app_field":        "order_entry_date",
        "raw_fields":       ["_ORDERS.ORDER_ENTRY_DATE_YYYYMMDD"],
        "confidence":       "high",
        "notes":            (
            "Raw field is YYYYMMDD string — parse to date in app layer. "
            "For SKU-level sort/display use MAX(order_entry_date) per SKU, "
            "not the raw row value."
        ),
    },
    "order number": {
        "app_field":        "order_number",
        "raw_fields":       ["_ORDERS.ORDER#"],
        "confidence":       "high",
        "notes":            (
            "For multi-PO SKU, sort/display uses MAX(ORDER#) per SKU. "
            "Do NOT use max ORDER# as a business key — display-sort only."
        ),
    },
}

# ===========================================================================
# AGGREGATION RULES  (SKU-level display)
# ===========================================================================
SKU_AGGREGATION_RULES: list[str] = [
    "Multiple orders/POs per SKU are expected — always aggregate before display.",
    "Sort key  : MAX(_ORDERS.ORDER#) per SKU  — display/sort only, not a business key.",
    "Date key  : MAX(order_entry_date) per SKU — newest order date drives SKU-level display.",
    "Keep display aliases stable even if the underlying query source changes.",
]
