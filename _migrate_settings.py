"""One-time migration: update cost_center_filter settings to use COST_CENTER from _ORDERS."""
import sys
sys.path.insert(0, r"C:\Users\lukass\Desktop\rebate tracking")
from db.local_db import init_db, set_setting
init_db()
set_setting("cost_center_filter", "orders_field")
set_setting("cost_center_orders_field", "COST_CENTER")
print("Settings updated: cost_center_filter=orders_field, cost_center_orders_field=COST_CENTER")
