"""
Local document retrieval system for retail policies and procedures.

Indexes markdown documents from ``data/documents/`` using Gemini embeddings,
stores per-chunk text plus float32 embedding blobs in the local SQLite store
(``data/retail.db``), and answers retrieval queries with cosine similarity.

The module degrades gracefully: whenever GEMINI_API_KEY is missing, invalid,
or the network is down, every public entry point falls back to an
"unavailable" result instead of raising.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = lambda *args, **kwargs: False  # type: ignore[assignment]

from src.database.connection import DATA_DIR, get_db_connection

logger = logging.getLogger("retrieval")

DOCUMENTS_DIR = DATA_DIR / "documents"

_EMBED_BATCH = 32
_MAX_WORDS_PER_CHUNK = 500
_TARGET_MAX_WORDS = 400
_MIN_CHUNK_WORDS = 15
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_HORIZONTAL_RULES = {"---", "***", "___"}


def _word_count(text: str) -> int:
    return len(text.split())


def _ensure_schema() -> None:
    """Creates the retrieval tables if missing. Never raises."""
    try:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS document_chunks (
                    document_name TEXT,
                    chunk_id TEXT,
                    section TEXT,
                    text TEXT,
                    embedding BLOB,
                    PRIMARY KEY (document_name, chunk_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS document_index_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    model TEXT,
                    indexed_at TEXT,
                    documents_count INTEGER,
                    chunks_count INTEGER
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logger.warning("Could not ensure document retrieval schema: %s", e)


_ensure_schema()


def _extract_sections(content: str) -> List[tuple]:
    """Splits a markdown document into (section_path, body) pairs by headings."""
    sections: List[tuple] = []
    heading_stack: List[tuple] = []
    current_section = ""
    content_lines: List[str] = []

    def flush() -> None:
        nonlocal content_lines
        body = "\n".join(content_lines).strip()
        if body:
            sections.append((current_section, body))
        content_lines = []

    for raw_line in content.splitlines():
        match = _HEADING_RE.match(raw_line.strip())
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, title))
            current_section = " > ".join(item[1] for item in heading_stack)
        else:
            content_lines.append(raw_line)
    flush()
    return sections


def _split_paragraphs(body: str) -> List[str]:
    """Splits section body text into paragraph units joined at sentence spaces."""
    paragraphs: List[str] = []
    for block in re.split(r"\n\s*\n", body):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        lines = [line for line in lines if line not in _HORIZONTAL_RULES]
        if not lines:
            continue
        paragraphs.append(" ".join(lines))
    return paragraphs


def _split_long_paragraph(paragraph: str, max_words: int = _TARGET_MAX_WORDS) -> List[str]:
    """Splits an oversized paragraph into groups at sentence boundaries only."""
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(paragraph) if s.strip()]
    groups: List[str] = []
    current: List[str] = []
    count = 0
    for sentence in sentences:
        size = _word_count(sentence)
        if current and count + size > max_words:
            groups.append(" ".join(current))
            current = []
            count = 0
        current.append(sentence)
        count += size
    if current:
        groups.append(" ".join(current))
    return groups or [paragraph]


def _buffer_paragraphs(paragraphs: List[str]):
    """Groups paragraphs into chunks targeting ~300-400 words (max ~500)."""
    buffer: List[str] = []
    buffer_words = 0
    for paragraph in paragraphs:
        size = _word_count(paragraph)
        if size > _MAX_WORDS_PER_CHUNK:
            if buffer:
                yield buffer
                buffer = []
                buffer_words = 0
            for group in _split_long_paragraph(paragraph):
                yield [group]
            continue
        if buffer and buffer_words + size > _TARGET_MAX_WORDS:
            if buffer_words >= _MIN_CHUNK_WORDS:
                yield buffer
                buffer = []
                buffer_words = 0
        buffer.append(paragraph)
        buffer_words += size
    if buffer:
        yield buffer


def _chunk_document(path: Path) -> List[Dict[str, Any]]:
    """Deterministically chunks one markdown document. Never raises."""
    filename = Path(path).name
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("Could not read document %s: %s", path, e)
        return []

    chunks: List[Dict[str, Any]] = []
    index = 0
    for section_name, body in _extract_sections(content):
        for group in _buffer_paragraphs(_split_paragraphs(body)):
            chunk_text = "\n\n".join(group).strip()
            if _word_count(chunk_text) < _MIN_CHUNK_WORDS:
                continue
            chunks.append(
                {
                    "chunk_id": f"{filename}#{index}",
                    "section": section_name,
                    "text": chunk_text,
                }
            )
            index += 1
    return chunks


@dataclass
class RetrievedChunk:
    document_name: str
    chunk_id: str
    section: str
    text: str
    score: float


class DocumentRetriever:
    """Embedding-backed local document retriever over the SQLite index store."""

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-embedding-001"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model
        self._client: Any = None
        self._cached_norm: Optional[np.ndarray] = None
        self._cached_meta: Optional[List[Dict[str, Any]]] = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            return None
        try:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as e:
            logger.warning("Could not initialize Gemini client: %s", e)
            self._client = None
            return None

    def _embed(self, texts: List[str]) -> np.ndarray:
        """Embeds a list of texts into a (N, D) float32 array. Raises on failure."""
        client = self._get_client()
        if client is None:
            raise RuntimeError("Gemini API key is not configured")
        vectors: List[np.ndarray] = []
        for start in range(0, len(texts), _EMBED_BATCH):
            batch = texts[start : start + _EMBED_BATCH]
            try:
                response = client.models.embed_content(model=self.model, contents=batch)
            except Exception as e:
                raise RuntimeError(f"Gemini embedding call failed: {e}") from e
            items = getattr(response, "embeddings", None)
            if not items:
                raise RuntimeError("Gemini returned no embeddings")
            for item in items:
                values = getattr(item, "values", None)
                if values is None or len(values) == 0:
                    raise RuntimeError("Gemini returned an empty embedding vector")
                array = np.asarray(values, dtype=np.float32)
                if array.ndim != 1 or array.size == 0 or not np.any(array):
                    raise RuntimeError("Gemini returned an empty or zero embedding vector")
                vectors.append(array)
        if not vectors:
            raise RuntimeError("No embeddings were produced")
        return np.stack(vectors)

    def _invalidate_cache(self) -> None:
        self._cached_norm = None
        self._cached_meta = None

    def _read_meta(self) -> Optional[Dict[str, Any]]:
        try:
            conn = get_db_connection()
            try:
                row = conn.execute(
                    "SELECT model, indexed_at, documents_count, chunks_count "
                    "FROM document_index_meta WHERE id = 1"
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Could not read document index metadata: %s", e)
            return None
        return dict(row) if row is not None else None

    def _load_vectors(self) -> bool:
        """Loads all chunk vectors once into a normalized (N, D) array (cached)."""
        if self._cached_norm is not None and self._cached_meta is not None:
            return True
        self._invalidate_cache()
        try:
            conn = get_db_connection()
            try:
                rows = conn.execute(
                    "SELECT document_name, chunk_id, section, text, embedding "
                    "FROM document_chunks"
                ).fetchall()
            finally:
                conn.close()
            vectors: List[np.ndarray] = []
            meta: List[Dict[str, Any]] = []
            for row in rows:
                blob = row["embedding"]
                if not blob:
                    continue
                try:
                    vector = np.frombuffer(blob, dtype=np.float32)
                except Exception:
                    continue
                if vector.size == 0:
                    continue
                vectors.append(vector.astype(np.float32, copy=False))
                meta.append(
                    {
                        "document_name": row["document_name"],
                        "chunk_id": row["chunk_id"],
                        "section": row["section"],
                        "text": row["text"],
                    }
                )
            if not vectors:
                return False
            matrix = np.stack(vectors)
            if matrix.ndim != 2 or matrix.shape[0] == 0:
                return False
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self._cached_norm = matrix / np.maximum(norms, 1e-9)
            self._cached_meta = meta
            return True
        except Exception as e:
            logger.warning("Could not load document vectors: %s", e)
            self._invalidate_cache()
            return False

    def is_available(self) -> bool:
        """True only when an index with chunks exists in the local store."""
        try:
            conn = get_db_connection()
            try:
                meta = conn.execute(
                    "SELECT chunks_count FROM document_index_meta WHERE id = 1"
                ).fetchone()
                if meta is None or not meta["chunks_count"]:
                    return False
                count = conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
            finally:
                conn.close()
            return count > 0
        except Exception as e:
            logger.warning("Could not determine retrieval availability: %s", e)
            return False

    def status(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {
            "available": False,
            "reason": None,
            "model": self.model,
            "documents_indexed": 0,
            "chunks_count": 0,
            "indexed_at": None,
        }
        meta = self._read_meta()
        if meta is None:
            report["reason"] = (
                "No document index exists yet - run python -m src.retrieval to build it"
            )
            return report
        report["model"] = meta.get("model") or self.model
        report["documents_indexed"] = meta.get("documents_count", 0)
        report["chunks_count"] = meta.get("chunks_count", 0)
        report["indexed_at"] = meta.get("indexed_at")
        try:
            conn = get_db_connection()
            try:
                count = conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Could not read chunk count: %s", e)
            count = 0
        report["chunks_count"] = count
        if report["chunks_count"] <= 0:
            report["reason"] = "Index store is empty (no chunks stored)"
            return report
        report["reason"] = "ok"
        report["available"] = True
        return report

    def build_index(self, force: bool = False) -> Dict[str, Any]:
        """Builds the local embedding index. Never raises; returns a summary dict."""
        try:
            if not force:
                existing = self._read_meta()
                if existing and existing.get("chunks_count", 0) > 0:
                    try:
                        conn = get_db_connection()
                        try:
                            count = conn.execute(
                                "SELECT COUNT(*) FROM document_chunks"
                            ).fetchone()[0]
                        finally:
                            conn.close()
                    except Exception:
                        count = 0
                    if count > 0:
                        self._invalidate_cache()
                        return {
                            "success": True,
                            "documents_indexed": existing.get("documents_count", 0),
                            "chunks_count": count,
                            "reason": None,
                        }

            if self._get_client() is None:
                raise RuntimeError(
                    "GEMINI_API_KEY is not configured - document retrieval unavailable"
                )
            if not DOCUMENTS_DIR.is_dir():
                raise RuntimeError(f"Documents directory not found: {DOCUMENTS_DIR}")

            document_files = sorted(
                p for p in DOCUMENTS_DIR.iterdir()
                if p.is_file() and p.suffix.lower() == ".md"
            )
            if not document_files:
                raise RuntimeError("No markdown documents found to index")

            entries: List[tuple] = []
            for path in document_files:
                for chunk in _chunk_document(path):
                    entries.append((path.name, chunk))
            if not entries:
                raise RuntimeError("No indexable chunks produced from documents")

            texts = [chunk["text"] for _, chunk in entries]
            vectors = self._embed(texts)
            if vectors is None or vectors.ndim != 2 or vectors.shape[0] != len(entries):
                raise RuntimeError("Embedding response size mismatch")

            rows_by_doc: Dict[str, List[tuple]] = {}
            for (doc_name, chunk), vector in zip(entries, vectors):
                rows_by_doc.setdefault(doc_name, []).append(
                    (
                        doc_name,
                        chunk["chunk_id"],
                        chunk["section"],
                        chunk["text"],
                        np.asarray(vector, dtype=np.float32).tobytes(),
                    )
                )

            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                for doc_name in rows_by_doc:
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_name = ?",
                        (doc_name,),
                    )
                    cursor.executemany(
                        "INSERT INTO document_chunks "
                        "(document_name, chunk_id, section, text, embedding) "
                        "VALUES (?, ?, ?, ?, ?)",
                        rows_by_doc[doc_name],
                    )
                indexed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                cursor.execute(
                    "INSERT OR REPLACE INTO document_index_meta "
                    "(id, model, indexed_at, documents_count, chunks_count) "
                    "VALUES (1, ?, ?, ?, ?)",
                    (self.model, indexed_at, len(rows_by_doc), len(entries)),
                )
                conn.commit()
            finally:
                conn.close()

            self._invalidate_cache()
            return {
                "success": True,
                "documents_indexed": len(rows_by_doc),
                "chunks_count": len(entries),
                "reason": None,
            }
        except Exception as e:
            logger.warning("Document index build failed: %s", e)
            return {
                "success": False,
                "documents_indexed": 0,
                "chunks_count": 0,
                "reason": str(e) or "Document index build failed",
            }

    def retrieve(self, query: str, top_k: int = 5) -> List[RetrievedChunk]:
        """Returns the top-k chunks most similar to the query, or [] if unavailable."""
        if not query or not str(query).strip():
            return []
        if not isinstance(top_k, int) or top_k <= 0:
            return []
        try:
            if not self._load_vectors():
                return []
            if self._cached_norm is None or self._cached_meta is None:
                return []
            query_vecs = self._embed([str(query)])
            if query_vecs is None or query_vecs.shape != (1, self._cached_norm.shape[1]):
                return []
            query_norm = np.linalg.norm(query_vecs, axis=1)
            if not np.any(query_norm):
                return []
            query_unit = (query_vecs / np.maximum(query_norm[:, None], 1e-9))[0]
            scores = self._cached_norm @ query_unit
            top = min(top_k, len(self._cached_meta))
            if top <= 0:
                return []
            order = np.argsort(-scores)[:top]
            results: List[RetrievedChunk] = []
            for i in order:
                meta = self._cached_meta[int(i)]
                score = max(0.0, min(1.0, float(scores[int(i)])))
                results.append(
                    RetrievedChunk(
                        document_name=meta["document_name"],
                        chunk_id=meta["chunk_id"],
                        section=meta["section"],
                        text=meta["text"],
                        score=round(score, 4),
                    )
                )
            return results
        except Exception as e:
            logger.warning("Document retrieval failed: %s", e)
            return []


DEFAULT_RETRIEVER = DocumentRetriever()


def retrieve_documents(query: str, top_k: int = 5) -> List[RetrievedChunk]:
    return DEFAULT_RETRIEVER.retrieve(query, top_k)


def retrieval_status() -> Dict[str, Any]:
    return DEFAULT_RETRIEVER.status()


def build_document_index(force: bool = False) -> Dict[str, Any]:
    return DEFAULT_RETRIEVER.build_index(force)


__all__ = [
    "RetrievedChunk",
    "DocumentRetriever",
    "DEFAULT_RETRIEVER",
    "retrieve_documents",
    "retrieval_status",
    "build_document_index",
]


if __name__ == "__main__":
    try:
        load_dotenv(".env.local")
    except Exception:
        pass
    summary = DocumentRetriever().build_index(force=True)
    print(json.dumps(summary, indent=2, ensure_ascii=False))