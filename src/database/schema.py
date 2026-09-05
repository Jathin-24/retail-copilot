"""
SQLite schema definitions and CSV data loading for retail entities:
stores, suppliers, products, purchase_orders, inventory, and sales.
Matches TRACK_ID=PS03 data model specifications.
"""
import csv
from pathlib import Path
from src.database.connection import get_db_connection, DATA_DIR

def init_db():
    """Initializes local SQLite tables and loads CSV data if not already present."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Stores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY,
        store_name TEXT NOT NULL,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        store_type TEXT NOT NULL
    );
    """)

    # Suppliers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS suppliers (
        supplier_id TEXT PRIMARY KEY,
        supplier_name TEXT NOT NULL,
        lead_time_days INTEGER NOT NULL,
        minimum_order_quantity INTEGER NOT NULL
    );
    """)

    # Products
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        product_id TEXT PRIMARY KEY,
        sku TEXT NOT NULL,
        product_name TEXT NOT NULL,
        category TEXT NOT NULL,
        subcategory TEXT NOT NULL,
        brand TEXT NOT NULL,
        supplier_id TEXT NOT NULL,
        cost_price REAL NOT NULL,
        selling_price REAL NOT NULL,
        reorder_point INTEGER NOT NULL,
        shelf_life_days INTEGER NOT NULL,
        FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id)
    );
    """)

    # Purchase Orders
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS purchase_orders (
        po_id TEXT PRIMARY KEY,
        order_date TEXT NOT NULL,
        expected_date TEXT NOT NULL,
        received_date TEXT,
        supplier_id TEXT NOT NULL,
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        ordered_quantity INTEGER NOT NULL,
        received_quantity INTEGER NOT NULL,
        status TEXT NOT NULL,
        unit_cost REAL NOT NULL,
        FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id),
        FOREIGN KEY (store_id) REFERENCES stores (store_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    );
    """)

    # Inventory Ledger (daily historical records)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        date TEXT NOT NULL,
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        opening_stock INTEGER NOT NULL,
        received_quantity INTEGER NOT NULL,
        sold_quantity INTEGER NOT NULL,
        returned_quantity INTEGER NOT NULL,
        damaged_quantity INTEGER NOT NULL,
        adjustment_quantity INTEGER NOT NULL,
        closing_stock INTEGER NOT NULL,
        PRIMARY KEY (date, store_id, product_id),
        FOREIGN KEY (store_id) REFERENCES stores (store_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    );
    """)

    # Sales Transactions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        transaction_id TEXT PRIMARY KEY,
        date TEXT NOT NULL,
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        discount_amount REAL NOT NULL,
        FOREIGN KEY (store_id) REFERENCES stores (store_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    );
    """)

    # Analytical indexes: date/store/product filtered aggregates drive the
    # deterministic analytics engine; indexes keep scans fast on 63k+ rows.
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_sales_date
    ON sales (date);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_sales_store_prod_date
    ON sales (store_id, product_id, date);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_inventory_date
    ON inventory (date);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_inventory_store_prod_date
    ON inventory (store_id, product_id, date);
    """)
    cursor.execute("""
    CREATE INDEX IF NOT EXISTS idx_purchase_orders_store_prod
    ON purchase_orders (store_id, product_id);
    """)

    conn.commit()

    # Seed from CSVs if empty
    cursor.execute("SELECT COUNT(*) FROM stores")
    if cursor.fetchone()[0] == 0:
        seed_sqlite_from_csv(conn)

    conn.close()

def seed_sqlite_from_csv(conn):
    """Populates SQLite tables from local CSV files."""
    cursor = conn.cursor()

    # Helper function to bulk insert CSV
    def load_table(csv_file, table_name, columns):
        file_path = DATA_DIR / csv_file
        if not file_path.exists():
            return
        with open(file_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            placeholders = ",".join(["?"] * len(columns))
            col_names = ",".join(columns)
            sql = f"INSERT OR REPLACE INTO {table_name} ({col_names}) VALUES ({placeholders})"
            rows = [[row[col] for col in columns] for row in reader]
            cursor.executemany(sql, rows)

    load_table("stores.csv", "stores", ["store_id", "store_name", "city", "state", "store_type"])
    load_table("suppliers.csv", "suppliers", ["supplier_id", "supplier_name", "lead_time_days", "minimum_order_quantity"])
    load_table("products.csv", "products", [
        "product_id", "sku", "product_name", "category", "subcategory", 
        "brand", "supplier_id", "cost_price", "selling_price", "reorder_point", "shelf_life_days"
    ])
    load_table("purchase_orders.csv", "purchase_orders", [
        "po_id", "order_date", "expected_date", "received_date", "supplier_id", 
        "store_id", "product_id", "ordered_quantity", "received_quantity", "status", "unit_cost"
    ])
    load_table("inventory.csv", "inventory", [
        "date", "store_id", "product_id", "opening_stock", "received_quantity", 
        "sold_quantity", "returned_quantity", "damaged_quantity", "adjustment_quantity", "closing_stock"
    ])
    load_table("sales.csv", "sales", [
        "transaction_id", "date", "store_id", "product_id", "quantity", "unit_price", "discount_amount"
    ])

    conn.commit()
