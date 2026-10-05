"""ChromaDB vector store with shop-scoped collections — MODULE 18.

Every shop has its own ChromaDB collection: ``munshi_shop_{shop_id}``.
This ensures strict tenant isolation at the vector-store level.

Falls back to an in-process mock (ephemeral dict) when ChromaDB is
unavailable so tests never require a running ChromaDB instance.
"""

import uuid
from typing import Any, Dict, List, Optional

from backend.app.core.config import settings
from backend.app.core.logging import logger


class MockVectorStore:
    """In-process vector store for tests and dev without ChromaDB."""

    def __init__(self):
        self._docs: Dict[str, List[Dict]] = {}

    def _col(self, name: str) -> List[Dict]:
        return self._docs.setdefault(name, [])

    def upsert(self, collection: str, doc_id: str, text: str,
               embedding: List[float], metadata: Dict) -> None:
        col = self._col(collection)
        for i, d in enumerate(col):
            if d["id"] == doc_id:
                col[i] = {"id": doc_id, "text": text,
                          "embedding": embedding, "metadata": metadata}
                return
        col.append({"id": doc_id, "text": text,
                    "embedding": embedding, "metadata": metadata})

    def query(self, collection: str, embedding: List[float],
              n_results: int = 5, where: Optional[Dict] = None) -> List[Dict]:
        col = self._col(collection)
        # Simple cosine-like: return first n_results (no real similarity)
        results = col if not where else [
            d for d in col
            if all(d["metadata"].get(k) == v for k, v in where.items())
        ]
        return [
            {"id": d["id"], "text": d["text"],
             "metadata": d["metadata"], "distance": 0.1}
            for d in results[:n_results]
        ]

    def delete(self, collection: str, doc_id: str) -> None:
        self._docs[collection] = [
            d for d in self._col(collection) if d["id"] != doc_id
        ]

    def count(self, collection: str) -> int:
        return len(self._col(collection))


class ChromaVectorStore:
    """Production ChromaDB-backed vector store."""

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            import chromadb
            self._client = chromadb.HttpClient(
                host=settings.CHROMA_HOST, port=settings.CHROMA_PORT
            )
            logger.info(
                "ChromaDB client connected.",
                extra={"host": settings.CHROMA_HOST, "port": settings.CHROMA_PORT},
            )
        except Exception as exc:
            logger.warning(
                "ChromaDB unavailable — using in-process mock store.",
                extra={"error": str(exc)},
            )
        return self._client

    def _collection_name(self, shop_id: str) -> str:
        return f"{settings.CHROMA_COLLECTION_PREFIX}{shop_id.replace('-', '_')}"

    def _get_or_create(self, shop_id: str):
        client = self._get_client()
        if client is None:
            return None
        return client.get_or_create_collection(
            name=self._collection_name(shop_id),
            metadata={"shop_id": shop_id},
        )

    def upsert(self, shop_id: str, doc_id: str, text: str,
               embedding: List[float], metadata: Dict) -> None:
        col = self._get_or_create(shop_id)
        if col is None:
            return
        col.upsert(
            ids=[doc_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[{**metadata, "shop_id": shop_id}],
        )

    def query(self, shop_id: str, embedding: List[float],
              n_results: int = 5) -> List[Dict[str, Any]]:
        col = self._get_or_create(shop_id)
        if col is None:
            return []
        results = col.query(
            query_embeddings=[embedding],
            n_results=min(n_results, col.count() or 1),
            include=["documents", "metadatas", "distances"],
        )
        output = []
        for i, doc_id in enumerate(results["ids"][0]):
            output.append({
                "id": doc_id,
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i],
            })
        return output

    def delete(self, shop_id: str, doc_id: str) -> None:
        col = self._get_or_create(shop_id)
        if col:
            col.delete(ids=[doc_id])

    def count(self, shop_id: str) -> int:
        col = self._get_or_create(shop_id)
        return col.count() if col else 0


# Use in-process mock as the default (ChromaDB optional)
_mock_store = MockVectorStore()


class VectorStore:
    """Unified interface: delegates to ChromaDB when available, mock otherwise."""

    def __init__(self) -> None:
        self._chroma = ChromaVectorStore()

    def _backend(self, shop_id: str):
        col = self._chroma._get_or_create(shop_id)
        return self._chroma if col is not None else None

    def upsert(self, shop_id: str, doc_id: str, text: str,
               embedding: List[float], metadata: Dict) -> None:
        b = self._backend(shop_id)
        if b:
            b.upsert(shop_id, doc_id, text, embedding, metadata)
        else:
            _mock_store.upsert(
                f"shop_{shop_id}", doc_id, text, embedding, metadata
            )

    def query(self, shop_id: str, embedding: List[float],
              n_results: int = 5) -> List[Dict[str, Any]]:
        b = self._backend(shop_id)
        if b:
            return b.query(shop_id, embedding, n_results)
        return _mock_store.query(f"shop_{shop_id}", embedding, n_results)

    def delete(self, shop_id: str, doc_id: str) -> None:
        b = self._backend(shop_id)
        if b:
            b.delete(shop_id, doc_id)
        else:
            _mock_store.delete(f"shop_{shop_id}", doc_id)

    def count(self, shop_id: str) -> int:
        b = self._backend(shop_id)
        if b:
            return b.count(shop_id)
        return _mock_store.count(f"shop_{shop_id}")


vector_store = VectorStore()
