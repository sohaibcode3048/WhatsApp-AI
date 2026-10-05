"""RAG Ingestion Pipeline — MODULE 18.

Ingests shop documents (policies, FAQs, product descriptions) into the
shop-scoped ChromaDB collection via chunking + HuggingFace embeddings.
"""

import hashlib
import re
from typing import Any, Dict, List, Optional

from backend.app.core.logging import logger
from backend.app.rag.embeddings import embedding_provider
from backend.app.rag.vector_store import vector_store


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """Split text into overlapping chunks."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks, current, current_len = [], [], 0
    for sent in sentences:
        words = sent.split()
        if current_len + len(words) > chunk_size and current:
            chunks.append(" ".join(current))
            # Keep overlap words
            overlap_words = current[-overlap:] if overlap < len(current) else current
            current = list(overlap_words)
            current_len = len(current)
        current.extend(words)
        current_len += len(words)
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]


def _doc_id(shop_id: str, source: str, chunk_index: int) -> str:
    raw = f"{shop_id}:{source}:{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


class RAGIngestionService:
    """Ingest text documents into the shop-specific knowledge base."""

    def ingest_document(
        self,
        shop_id: str,
        text: str,
        document_type: str = "general",
        source: str = "manual",
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Chunk, embed, and store a text document. Returns chunk count."""
        chunks = _chunk_text(text)
        metadata_base = {
            "document_type": document_type,
            "source": source,
            "shop_id": shop_id,
            **(extra_metadata or {}),
        }
        embeddings = embedding_provider.embed(chunks)

        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            doc_id = _doc_id(shop_id, source, i)
            vector_store.upsert(
                shop_id=shop_id,
                doc_id=doc_id,
                text=chunk,
                embedding=emb,
                metadata={**metadata_base, "chunk_index": i},
            )

        logger.info(
            "Document ingested into RAG.",
            extra={
                "shop_id": shop_id,
                "source": source,
                "chunks": len(chunks),
                "document_type": document_type,
            },
        )
        return len(chunks)

    def delete_document(self, shop_id: str, source: str, chunk_count: int) -> None:
        """Remove all chunks for a document from the vector store."""
        for i in range(chunk_count):
            doc_id = _doc_id(shop_id, source, i)
            vector_store.delete(shop_id, doc_id)

    def ingest_shop_policies(self, shop_id: str, policies: Dict[str, str]) -> Dict[str, int]:
        """Convenience: ingest multiple named policy texts. Returns {name: chunks}."""
        results = {}
        for name, text in policies.items():
            results[name] = self.ingest_document(
                shop_id=shop_id,
                text=text,
                document_type="policy",
                source=f"policy:{name}",
            )
        return results


rag_ingestion = RAGIngestionService()
