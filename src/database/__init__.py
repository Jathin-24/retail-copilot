"""
Database package for local SQLite persistence.
"""
from src.database.connection import get_db_connection, DB_PATH
from src.database.schema import init_db

__all__ = ["get_db_connection", "init_db", "DB_PATH"]
