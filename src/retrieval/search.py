"""
Local search and retrieval mechanism.
Strictly local: SQLite and local vector search.
No external vector databases, Pinecone, or external RAG.
"""
from typing import List, Dict, Any
from src.database.connection import get_db_connection

def local_catalog_search(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Performs local search across product catalog and category metadata.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    search_term = f"%{query}%"
    cursor.execute("""
        SELECT product_id, product_name, category, selling_price, cost_price
        FROM products
        WHERE product_name LIKE ? OR category LIKE ?
        LIMIT ?;
    """, (search_term, search_term, limit))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
