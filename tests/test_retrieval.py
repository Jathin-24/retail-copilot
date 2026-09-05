"""
Deterministic and offline unit tests for the local document retrieval system
(src/retrieval.py). No network calls are made; Gemini embedding internals are
monkeypatched or the API key is removed.
"""
import sqlite3

import numpy as np
import pytest

import src.retrieval as retrieval

_REAL = retrieval.get_db_connection


class _FakeDB:
    def __init__(self, path):
        self.path = str(path)

    def __call__(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


@pytest.fixture
def fake_db(tmp_path, monkeypatch):
    db = _FakeDB(tmp_path / "test_retrieval.db")
    monkeypatch.setattr(retrieval, "get_db_connection", db)
    conn = db()
    try:
        conn.execute(
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
        conn.execute(
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
    return db


def _seed_index(db, chunks):
    conn = db()
    try:
        for chunk in chunks:
            blob = np.asarray(chunk["vec"], dtype=np.float32).tobytes()
            conn.execute(
                "INSERT INTO document_chunks "
                "(document_name, chunk_id, section, text, embedding) "
                "VALUES (?, ?, ?, ?, ?)",
                (chunk["document_name"], chunk["chunk_id"], chunk["section"], chunk["text"], blob),
            )
        docs = len({c["document_name"] for c in chunks})
        conn.execute(
            "INSERT OR REPLACE INTO document_index_meta "
            "(id, model, indexed_at, documents_count, chunks_count) "
            "VALUES (1, 'gemini-embedding-001', '2026-09-05T00:00:00+00:00', ?, ?)",
            (docs, len(chunks)),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) Chunking
# ---------------------------------------------------------------------------


def test_chunk_document_creates_multiple_sections(tmp_path):
    long_para = " ".join(f"term{i}" for i in range(450))
    doc = tmp_path / "policy.md"
    doc.write_text(
        "# Store Policy\n\n"
        "This opening paragraph explains the policy in a single short sentence.\n\n"
        "## Approval Workflow\n\n"
        f"{long_para}\n\n"
        "### Emergency Approvals\n\n"
        "Emergency approvals require immediate sign-off by the regional manager.\n\n"
        "## Audit Rules\n\n"
        f"{long_para}\n",
        encoding="utf-8",
    )

    chunks = retrieval._chunk_document(doc)

    assert len(chunks) == 2
    for i, chunk in enumerate(chunks):
        assert set(chunk.keys()) == {"chunk_id", "section", "text"}
        assert chunk["chunk_id"] == f"policy.md#{i}"
        assert len(chunk["text"].split()) >= 15
        assert len(chunk["text"].split()) <= 500
    sections = [c["section"] for c in chunks]
    assert any("Store Policy > Approval Workflow" == s for s in sections)
    assert any("Store Policy > Audit Rules" == s for s in sections)
    assert long_para in [c["text"] for c in chunks]


def test_chunk_document_splits_oversized_paragraph_at_sentence_boundaries(tmp_path):
    sentence = (
        "Observation {i} indicates the inventory level reached the critical "
        "threshold and now requires immediate managerial attention before any further action."
    )
    doc = tmp_path / "long.md"
    sentences = [sentence.format(i=i) for i in range(27)]
    doc.write_text("# Report\n\n" + " ".join(sentences) + "\n", encoding="utf-8")

    chunks = retrieval._chunk_document(doc)

    assert len(chunks) == 2
    for chunk in chunks:
        assert len(chunk["text"].split()) <= 500
        assert chunk["text"].startswith("Observation")
        assert chunk["text"].rstrip().endswith("action.")
    assert sum(len(c["text"].split()) for c in chunks) == 27 * 20


def test_chunk_document_missing_file_returns_empty():
    assert retrieval._chunk_document(r"C:\nonexistent\missing.md") == []


# ---------------------------------------------------------------------------
# (b) Cosine similarity and top_k ordering via a fake embedding store
# ---------------------------------------------------------------------------


def _sample_chunks():
    return [
        {
            "document_name": "alpha.md",
            "chunk_id": "alpha.md#0",
            "section": "Alpha Policy",
            "text": "apple fruit red",
            "vec": [1.0, 0.0, 0.0],
        },
        {
            "document_name": "beta.md",
            "chunk_id": "beta.md#0",
            "section": "Beta Policy",
            "text": "banana fruit yellow",
            "vec": [0.0, 1.0, 0.0],
        },
        {
            "document_name": "gamma.md",
            "chunk_id": "gamma.md#0",
            "section": "Gamma Policy",
            "text": "carrot vegetable orange",
            "vec": [0.0, 0.0, 1.0],
        },
    ]


def _fake_embed(texts):
    def vec_for(text):
        lowered = str(text).lower()
        if "apple" in lowered:
            return np.array([[0.9, 0.1, 0.0]], dtype=np.float32)
        if "banana" in lowered:
            return np.array([[0.0, 0.9, 0.1]], dtype=np.float32)
        if "carrot" in lowered:
            return np.array([[0.0, 0.1, 0.9]], dtype=np.float32)
        return np.array([[1.0, 0.0, 0.0]], dtype=np.float32)

    return np.concatenate([vec_for(t) for t in texts], axis=0)


def test_retrieve_cosine_ranking_and_ordering(fake_db, monkeypatch):
    _seed_index(fake_db, _sample_chunks())
    retriever = retrieval.DocumentRetriever(api_key="test-key")
    monkeypatch.setattr(retriever, "_embed", _fake_embed)

    assert retriever.is_available() is True

    results = retriever.retrieve("apple fruit", top_k=2)
    assert len(results) == 2
    assert results[0].document_name == "alpha.md"
    assert results[0].chunk_id == "alpha.md#0"
    assert results[0].section == "Alpha Policy"
    assert results[0].text == "apple fruit red"
    assert results[0].score >= results[1].score
    assert results[0].score == round(0.9 / np.sqrt(0.82), 4)
    assert results[1].document_name == "beta.md"
    assert results[1].score < results[0].score

    all_results = retriever.retrieve("apple", top_k=100)
    assert [c.document_name for c in all_results] == ["alpha.md", "beta.md", "gamma.md"]
    assert all(isinstance(c.score, float) and 0.0 <= c.score <= 1.0 for c in all_results)

    assert retriever.retrieve("apple", top_k=0) == []
    assert retriever.retrieve("") == []
    assert retriever.retrieve(None) == []


def test_retrieve_returns_empty_when_embed_call_fails(fake_db, monkeypatch):
    _seed_index(fake_db, _sample_chunks())
    retriever = retrieval.DocumentRetriever(api_key="test-key")

    def boom(texts):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(retriever, "_embed", boom)

    assert retriever.retrieve("apple fruit") == []
    assert retriever.is_available() is True


# ---------------------------------------------------------------------------
# (c) Graceful unavailable paths
# ---------------------------------------------------------------------------


def test_unavailable_without_api_key(fake_db, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    retriever = retrieval.DocumentRetriever(api_key=None)

    assert retriever.api_key is None
    assert retriever.is_available() is False
    assert retriever.retrieve("anything") == []

    status = retriever.status()
    assert status["available"] is False
    assert status["reason"]
    assert status["chunks_count"] == 0
    assert status["documents_indexed"] == 0


def test_build_index_fails_gracefully_without_api_key(fake_db, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    retriever = retrieval.DocumentRetriever(api_key=None)

    result = retriever.build_index()
    assert result["success"] is False
    assert result["reason"]
    assert result["documents_indexed"] == 0
    assert result["chunks_count"] == 0
    assert retriever.is_available() is False


def test_build_index_fails_gracefully_without_documents(fake_db, monkeypatch, tmp_path):
    empty_docs = tmp_path / "empty_docs"
    empty_docs.mkdir()
    monkeypatch.setattr(retrieval, "DOCUMENTS_DIR", empty_docs)
    retriever = retrieval.DocumentRetriever(api_key="test-key")

    result = retriever.build_index(force=True)
    assert result["success"] is False
    assert "No markdown documents" in result["reason"]


def test_module_level_functions_smoke(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    def boom(texts):
        raise RuntimeError("offline")

    monkeypatch.setattr(retrieval.DEFAULT_RETRIEVER, "_embed", boom)

    status = retrieval.retrieval_status()
    assert {"available", "reason", "model", "documents_indexed", "chunks_count", "indexed_at"} <= set(status.keys())
    assert isinstance(status["reason"], str)

    assert retrieval.retrieve_documents("anything") == []


# ---------------------------------------------------------------------------
# (d) End-to-end offline build + schema idempotency
# ---------------------------------------------------------------------------


def test_build_index_force_builds_and_retrieves(fake_db, monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "ops.md").write_text(
        "# Ops Policy\n\n"
        "This is the first paragraph containing enough content words to exceed "
        "the minimum threshold comfortably for indexing.\n\n"
        "## Transfers\n\n"
        "This is the second chunk with similarly sufficient content to be indexed "
        "as a separate section.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(retrieval, "DOCUMENTS_DIR", docs)

    def fake_embed(texts):
        return np.ones((len(texts), 4), dtype=np.float32)

    retriever = retrieval.DocumentRetriever(api_key="test-key")
    monkeypatch.setattr(retriever, "_embed", fake_embed)

    result = retriever.build_index(force=True)
    assert result["success"] is True
    assert result["documents_indexed"] == 1
    assert result["chunks_count"] == 2
    assert result["reason"] is None
    assert retriever.is_available() is True

    status = retriever.status()
    assert status["available"] is True
    assert status["reason"] == "ok"
    assert status["chunks_count"] == 2
    assert status["documents_indexed"] == 1
    assert status["indexed_at"]

    matches = retriever.retrieve("query terms here", top_k=3)
    assert len(matches) == 2
    assert all(round(m.score, 4) == 1.0 for m in matches)


def test_build_index_skips_when_already_indexed(fake_db, monkeypatch):
    _seed_index(fake_db, _sample_chunks())
    calls = {"n": 0}

    def fake_embed(texts):
        calls["n"] += 1
        return np.ones((len(texts), 3), dtype=np.float32)

    retriever = retrieval.DocumentRetriever(api_key="test-key")
    monkeypatch.setattr(retriever, "_embed", fake_embed)

    result = retriever.build_index(force=False)
    assert result["success"] is True
    assert result["chunks_count"] == 3
    assert calls["n"] == 0


def test_schema_creation_is_idempotent():
    retrieval._ensure_schema()
    retrieval._ensure_schema()

    conn = _REAL()
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()

    assert "document_chunks" in tables
    assert "document_index_meta" in tables


def test_public_api_signature():
    from dataclasses import fields

    names = [f.name for f in fields(retrieval.RetrievedChunk)]
    assert names == ["document_name", "chunk_id", "section", "text", "score"]

    assert callable(retrieval.DEFAULT_RETRIEVER.retrieve)
    assert callable(retrieval.DEFAULT_RETRIEVER.build_index)
    assert callable(retrieval.DEFAULT_RETRIEVER.is_available)
    assert callable(retrieval.DEFAULT_RETRIEVER.status)
    assert callable(retrieval.retrieve_documents)
    assert callable(retrieval.retrieval_status)
    assert callable(retrieval.build_document_index)