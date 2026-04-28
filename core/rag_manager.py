import hashlib
import json
from pathlib import Path

import chromadb
import httpx

DB_BASE = Path(__file__).parent.parent / "databases"
CONFIG_BASE = Path(__file__).parent.parent / "config"

# Cache PersistentClient per db_name to avoid file-lock conflicts within one process
_clients: dict[str, chromadb.PersistentClient] = {}


def _load_llm_config() -> dict:
    path = CONFIG_BASE / "llm.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_rag_config(db_name: str) -> dict:
    path = DB_BASE / db_name / "config.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("rag", {})


def _vector_db_path(db_name: str) -> Path:
    return DB_BASE / db_name / "vector_db"


def _get_collection(db_name: str) -> chromadb.Collection:
    if db_name not in _clients:
        path = _vector_db_path(db_name)
        path.mkdir(parents=True, exist_ok=True)
        _clients[db_name] = chromadb.PersistentClient(path=str(path))
    client = _clients[db_name]
    return client.get_or_create_collection(
        name=db_name,
        metadata={"hnsw:space": "cosine"},
    )


def _embed(texts: list[str], model: str, base_url: str) -> list[list[float]]:
    """Call Ollama /api/embed to get embeddings for a list of texts."""
    url = base_url.rstrip("/") + "/api/embed"
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, json={"model": model, "input": texts})
        resp.raise_for_status()
    return resp.json()["embeddings"]


def _chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 50) -> list[str]:
    """Split text into overlapping character-count chunks with natural boundary detection."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunks.append(text[start:])
            break
        # Find a natural split point close to chunk_size
        for sep in ("\n\n", "\n", "。", ".", " "):
            pos = text.rfind(sep, start + chunk_size // 2, end)
            if pos != -1:
                end = pos + len(sep)
                break
        chunks.append(text[start:end])
        start = end - chunk_overlap

    return [c.strip() for c in chunks if c.strip()]


def ingest_text(
    db_name: str,
    text: str,
    source: str = "",
    metadata: dict | None = None,
) -> int:
    """Chunk text, embed, and upsert into vector DB. Returns number of chunks stored."""
    rag_cfg = _load_rag_config(db_name)
    llm_cfg = _load_llm_config()

    embedding_model = rag_cfg.get("embedding_model", "bge-m3")
    chunk_size = rag_cfg.get("chunk_size", 500)
    chunk_overlap = rag_cfg.get("chunk_overlap", 50)
    base_url = llm_cfg.get("base_url", "http://localhost:11434")

    chunks = _chunk_text(text, chunk_size, chunk_overlap)
    if not chunks:
        return 0

    embeddings = _embed(chunks, embedding_model, base_url)

    collection = _get_collection(db_name)

    ids = []
    metadatas = []
    for i, chunk in enumerate(chunks):
        # Deterministic ID: same file + same chunk position = same ID → upsert is idempotent
        chunk_id = hashlib.sha256(f"{source}:{i}:{chunk[:64]}".encode()).hexdigest()[:16]
        ids.append(chunk_id)
        meta: dict = {"source": source, "chunk_index": i}
        if metadata:
            meta.update(metadata)
        metadatas.append(meta)

    collection.upsert(
        documents=chunks,
        embeddings=embeddings,
        ids=ids,
        metadatas=metadatas,
    )
    return len(chunks)


def search(
    db_name: str,
    query: str,
    k: int = 4,
    score_threshold: float = 0.3,
) -> list[dict]:
    """Semantic similarity search. Returns list of {content, source, score} dicts."""
    rag_cfg = _load_rag_config(db_name)
    llm_cfg = _load_llm_config()

    embedding_model = rag_cfg.get("embedding_model", "bge-m3")
    base_url = llm_cfg.get("base_url", "http://localhost:11434")

    collection = _get_collection(db_name)
    count = collection.count()
    if count == 0:
        return []

    query_embedding = _embed([query], embedding_model, base_url)[0]

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(k, count),
        include=["documents", "distances", "metadatas"],
    )

    docs = []
    for doc, dist, meta in zip(
        results["documents"][0],
        results["distances"][0],
        results["metadatas"][0],
    ):
        # ChromaDB cosine distance: 0=identical → similarity = 1 - distance
        similarity = max(0.0, 1.0 - dist)
        if similarity < score_threshold:
            continue
        docs.append({
            "content": doc,
            "source": meta.get("source", ""),
            "score": round(similarity, 4),
        })

    return docs


def clear_collection(db_name: str) -> int:
    """Delete all documents from the collection. Returns count of deleted docs."""
    collection = _get_collection(db_name)
    count = collection.count()
    if count > 0:
        all_ids = collection.get(include=[])["ids"]
        collection.delete(ids=all_ids)
    return count


def collection_stats(db_name: str) -> dict:
    """Return document count and RAG enabled status for a DB."""
    try:
        collection = _get_collection(db_name)
        rag_cfg = _load_rag_config(db_name)
        return {
            "document_count": collection.count(),
            "enabled": rag_cfg.get("enabled", False),
            "embedding_model": rag_cfg.get("embedding_model", "bge-m3"),
        }
    except Exception:
        return {"document_count": 0, "enabled": False, "embedding_model": ""}
