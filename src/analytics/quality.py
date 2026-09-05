"""
Deterministic inventory and sales data quality auditor.
Audits:
1. Negative stock
2. Impossible quantities (e.g. quantity <= 0, discount > revenue, negative pricing)
3. Missing references (foreign key breaches)
4. Unexplained inventory jumps (arithmetic formula breaks or inter-day stock mismatches)
5. Sales exceeding available inventory
6. Duplicate transaction or purchase order IDs
Preserves full source identifiers for auditing and forensic traceability.
"""
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from src.database.connection import get_db_connection
from src.analytics.models import DataQualityReport

def check_negative_stock(conn) -> List[Dict[str, Any]]:
    """Detects any negative opening or closing stock in inventory ledgers."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT date, store_id, product_id, opening_stock, closing_stock
        FROM inventory
        WHERE opening_stock < 0 OR closing_stock < 0;
    """)
    rows = cursor.fetchall()
    issues = []
    for r in rows:
        if r["opening_stock"] < 0:
            issues.append({
                "table": "inventory",
                "date": r["date"],
                "store_id": r["store_id"],
                "product_id": r["product_id"],
                "field": "opening_stock",
                "value": r["opening_stock"],
                "error": "Negative opening stock detected."
            })
        if r["closing_stock"] < 0:
            issues.append({
                "table": "inventory",
                "date": r["date"],
                "store_id": r["store_id"],
                "product_id": r["product_id"],
                "field": "closing_stock",
                "value": r["closing_stock"],
                "error": "Negative closing stock detected."
            })
    return issues

def check_impossible_quantities(conn) -> List[Dict[str, Any]]:
    """Detects invalid numerical values in sales and inventory movements."""
    cursor = conn.cursor()
    issues = []

    # 1. Sales invalid quantities, negative prices, or discounts exceeding gross price
    cursor.execute("""
        SELECT transaction_id, date, store_id, product_id, quantity, unit_price, discount_amount
        FROM sales
        WHERE quantity <= 0 OR unit_price <= 0 OR discount_amount < 0 OR discount_amount > (quantity * unit_price);
    """)
    for r in cursor.fetchall():
        reason = []
        if r["quantity"] <= 0:
            reason.append(f"Non-positive quantity: {r['quantity']}")
        if r["unit_price"] <= 0:
            reason.append(f"Non-positive unit price: {r['unit_price']}")
        if r["discount_amount"] < 0:
            reason.append(f"Negative discount: {r['discount_amount']}")
        if r["discount_amount"] > (r["quantity"] * r["unit_price"]):
            reason.append(f"Discount {r['discount_amount']} exceeds total value {r['quantity'] * r['unit_price']}")

        issues.append({
            "table": "sales",
            "transaction_id": r["transaction_id"],
            "date": r["date"],
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "error": "; ".join(reason)
        })

    # 2. Inventory movements with negative quantities
    cursor.execute("""
        SELECT date, store_id, product_id, received_quantity, sold_quantity, returned_quantity, damaged_quantity
        FROM inventory
        WHERE received_quantity < 0 OR sold_quantity < 0 OR returned_quantity < 0 OR damaged_quantity < 0;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "inventory",
            "date": r["date"],
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "error": "Negative inventory movement quantity detected."
        })

    return issues

def check_missing_references(conn) -> List[Dict[str, Any]]:
    """Detects orphaned records with missing foreign keys."""
    cursor = conn.cursor()
    issues = []

    # Sales missing product or store
    cursor.execute("""
        SELECT s.transaction_id, s.date, s.store_id, s.product_id
        FROM sales s
        LEFT JOIN products p ON s.product_id = p.product_id
        WHERE p.product_id IS NULL;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "sales",
            "transaction_id": r["transaction_id"],
            "date": r["date"],
            "missing_entity": "product",
            "referenced_id": r["product_id"],
            "error": f"Sales transaction references nonexistent product_id: {r['product_id']}"
        })

    cursor.execute("""
        SELECT s.transaction_id, s.date, s.store_id
        FROM sales s
        LEFT JOIN stores st ON s.store_id = st.store_id
        WHERE st.store_id IS NULL;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "sales",
            "transaction_id": r["transaction_id"],
            "date": r["date"],
            "missing_entity": "store",
            "referenced_id": r["store_id"],
            "error": f"Sales transaction references nonexistent store_id: {r['store_id']}"
        })

    # Products missing supplier
    cursor.execute("""
        SELECT p.product_id, p.product_name, p.supplier_id
        FROM products p
        LEFT JOIN suppliers sup ON p.supplier_id = sup.supplier_id
        WHERE sup.supplier_id IS NULL;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "products",
            "product_id": r["product_id"],
            "missing_entity": "supplier",
            "referenced_id": r["supplier_id"],
            "error": f"Product references nonexistent supplier_id: {r['supplier_id']}"
        })

    # Inventory missing store or product
    cursor.execute("""
        SELECT i.date, i.store_id, i.product_id
        FROM inventory i
        LEFT JOIN products p ON i.product_id = p.product_id
        WHERE p.product_id IS NULL;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "inventory",
            "date": r["date"],
            "store_id": r["store_id"],
            "missing_entity": "product",
            "referenced_id": r["product_id"],
            "error": f"Inventory record references nonexistent product_id: {r['product_id']}"
        })

    return issues

def check_unexplained_inventory_jumps(conn) -> List[Dict[str, Any]]:
    """
    Validates:
    1. Daily ledger arithmetic equation:
       closing = opening + received - sold + returned - damaged + adjustment
    2. Inter-day continuity: Day(T) opening == Day(T-1) closing
    """
    cursor = conn.cursor()
    issues = []

    # 1. Ledger Balance Equation
    cursor.execute("""
        SELECT date, store_id, product_id, opening_stock, received_quantity, 
               sold_quantity, returned_quantity, damaged_quantity, adjustment_quantity, 
               closing_stock
        FROM inventory
        WHERE closing_stock != (opening_stock + received_quantity - sold_quantity + returned_quantity - damaged_quantity + adjustment_quantity);
    """)
    for r in cursor.fetchall():
        expected = (r["opening_stock"] + r["received_quantity"] - r["sold_quantity"] + 
                    r["returned_quantity"] - r["damaged_quantity"] + r["adjustment_quantity"])
        issues.append({
            "type": "ARITHMETIC_MISMATCH",
            "table": "inventory",
            "date": r["date"],
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "expected_closing": expected,
            "actual_closing": r["closing_stock"],
            "discrepancy": r["closing_stock"] - expected,
            "error": f"Daily ledger formula violated: expected {expected}, recorded {r['closing_stock']}."
        })

    # 2. Day-to-Day Continuity Check using window function
    cursor.execute("""
        WITH lagged AS (
            SELECT 
                date as current_date,
                store_id,
                product_id,
                opening_stock as current_opening,
                LAG(closing_stock) OVER (PARTITION BY store_id, product_id ORDER BY date) as previous_closing,
                LAG(date) OVER (PARTITION BY store_id, product_id ORDER BY date) as previous_date
            FROM inventory
        )
        SELECT * FROM lagged
        WHERE previous_closing IS NOT NULL AND current_opening != previous_closing;
    """)
    for r in cursor.fetchall():
        issues.append({
            "type": "CONTINUITY_BREAK",
            "table": "inventory",
            "date": r["current_date"],
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "previous_date": r["previous_date"],
            "previous_closing": r["previous_closing"],
            "current_opening": r["current_opening"],
            "discrepancy": r["current_opening"] - r["previous_closing"],
            "error": f"Day-to-day opening stock ({r['current_opening']}) does not equal previous day's closing stock ({r['previous_closing']})."
        })

    return issues

def check_sales_exceeding_inventory(conn) -> List[Dict[str, Any]]:
    """
    Detects if recorded sales on any date exceeded available inventory:
    available = opening_stock + received_quantity + returned_quantity
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            date, store_id, product_id, opening_stock, received_quantity, returned_quantity, sold_quantity, closing_stock
        FROM inventory
        WHERE sold_quantity > (opening_stock + received_quantity + returned_quantity);
    """)
    rows = cursor.fetchall()
    issues = []
    for r in rows:
        available = r["opening_stock"] + r["received_quantity"] + r["returned_quantity"]
        issues.append({
            "table": "inventory",
            "date": r["date"],
            "store_id": r["store_id"],
            "product_id": r["product_id"],
            "available_stock": available,
            "sold_quantity": r["sold_quantity"],
            "excess_sold": r["sold_quantity"] - available,
            "error": f"Sold quantity ({r['sold_quantity']}) exceeds total available inventory ({available})."
        })
    return issues

def check_duplicate_transaction_ids(conn) -> List[Dict[str, Any]]:
    """Detects duplicate primary keys in sales transactions and purchase orders."""
    cursor = conn.cursor()
    issues = []

    # Duplicate sales transactions
    cursor.execute("""
        SELECT transaction_id, COUNT(*) as count, MIN(date) as first_date, MAX(date) as last_date
        FROM sales
        GROUP BY transaction_id
        HAVING COUNT(*) > 1;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "sales",
            "primary_key": "transaction_id",
            "id_value": r["transaction_id"],
            "duplicate_count": r["count"],
            "first_date": r["first_date"],
            "last_date": r["last_date"],
            "error": f"Duplicate transaction_id found with {r['count']} occurrences."
        })

    # Duplicate purchase orders
    cursor.execute("""
        SELECT po_id, COUNT(*) as count
        FROM purchase_orders
        GROUP BY po_id
        HAVING COUNT(*) > 1;
    """)
    for r in cursor.fetchall():
        issues.append({
            "table": "purchase_orders",
            "primary_key": "po_id",
            "id_value": r["po_id"],
            "duplicate_count": r["count"],
            "error": f"Duplicate po_id found with {r['count']} occurrences."
        })

    return issues

def check_inventory_data_quality() -> DataQualityReport:
    """
    Executes complete deterministic suite of data quality checks.
    Preserves all source identifiers for forensic traceability.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(date) FROM inventory")
    latest_date = cursor.fetchone()[0] or datetime.now().strftime("%Y-%m-%d")

    checks = [
        "negative_stock",
        "impossible_quantities",
        "missing_references",
        "unexplained_inventory_jumps",
        "sales_exceeding_available_inventory",
        "duplicate_transaction_ids"
    ]

    issues_map: Dict[str, List[Dict[str, Any]]] = {
        "negative_stock": check_negative_stock(conn),
        "impossible_quantities": check_impossible_quantities(conn),
        "missing_references": check_missing_references(conn),
        "unexplained_inventory_jumps": check_unexplained_inventory_jumps(conn),
        "sales_exceeding_available_inventory": check_sales_exceeding_inventory(conn),
        "duplicate_transaction_ids": check_duplicate_transaction_ids(conn)
    }

    conn.close()

    total_issues = sum(len(items) for items in issues_map.values())
    passed = total_issues == 0

    return DataQualityReport(
        as_of_date=latest_date,
        total_issues_found=total_issues,
        passed=passed,
        checks_performed=checks,
        issues_by_category=issues_map
    )
