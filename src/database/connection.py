"""
SQLite database connection provider for local retail storage.
"""
import sqlite3
import os
from pathlib import Path

# Local committed database location
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DB_PATH = DATA_DIR / "retail.db"

def get_db_connection() -> sqlite3.Connection:
    """Provides a SQLite connection with row factory enabled."""
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn
