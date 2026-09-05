"""
SQLite schema definitions for retail entities:
Stores, Products, Inventory, Sales, and Suppliers.
"""
from src.database.connection import get_db_connection

def init_db():
    """Initializes local SQLite tables if they do not exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Stores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stores (
        store_id TEXT PRIMARY KEY,
        store_name TEXT NOT NULL,
        location TEXT NOT NULL,
        region TEXT NOT NULL
    );
    """)

    # Suppliers
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS suppliers (
        supplier_id TEXT PRIMARY KEY,
        supplier_name TEXT NOT NULL,
        lead_time_days INTEGER NOT NULL,
        contact_email TEXT
    );
    """)

    # Products
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        product_id TEXT PRIMARY KEY,
        product_name TEXT NOT NULL,
        category TEXT NOT NULL,
        cost_price REAL NOT NULL,
        selling_price REAL NOT NULL,
        supplier_id TEXT,
        FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id)
    );
    """)

    # Inventory
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        stock_on_hand INTEGER NOT NULL,
        reorder_threshold INTEGER NOT NULL,
        safety_stock INTEGER NOT NULL,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (store_id, product_id),
        FOREIGN KEY (store_id) REFERENCES stores (store_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    );
    """)

    # Sales
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        sale_id TEXT PRIMARY KEY,
        store_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        quantity_sold INTEGER NOT NULL,
        total_revenue REAL NOT NULL,
        timestamp TIMESTAMP NOT NULL,
        FOREIGN KEY (store_id) REFERENCES stores (store_id),
        FOREIGN KEY (product_id) REFERENCES products (product_id)
    );
    """)

    conn.commit()
    conn.close()
