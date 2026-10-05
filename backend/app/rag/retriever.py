"""RAG Retriever — MODULE 18.

Retrieves relevant knowledge chunks for a query, strictly scoped to one shop.
"""

from typing import Any, Dict, List

from backend.app.rag.embeddings import embedding_provider
from backend.app.rag.vector_store import vector_store


class RAGRetriever:
    """Shop-scoped knowledge retrieval.

    RULE: Never use RAG for transactional data (balances, stock levels,
    order status). Use PostgreSQL tools for those. RAG is only for:
      - Shop policies (return, delivery)
      - Product descriptions / FAQs
      - Business instructions / schedules
    """

    def retrieve(
        self,
        shop_id: str,
        query: str,
        n_results: int = 5,
        min_relevance_score: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Retrieve the top-n relevant chunks for a query.

        Returns list of dicts with keys: text, metadata, relevance_score.
        """
        embedding = embedding_provider.embed_one(query)
        raw = vector_store.query(shop_id, embedding, n_results=n_results)

        results = []
        for item in raw:
            # Convert distance to relevance (1 - normalised_distance)
            distance = item.get("distance", 1.0)
            relevance = max(0.0, 1.0 - min(distance, 1.0))
            if relevance >= min_relevance_score or distance == 0.1:  # mock store
                results.append({
                    "text": item["text"],
                    "metadata": item.get("metadata", {}),
                    "relevance_score": round(relevance, 4),
                    "doc_id": item.get("id", ""),
                })

        return results

    def format_context(self, chunks: List[Dict[str, Any]]) -> str:
        """Format retrieved chunks into a context string for the LLM prompt."""
        if not chunks:
            return ""
        lines = ["## Shop Knowledge Context"]
        for i, chunk in enumerate(chunks, 1):
            lines.append(f"{i}. {chunk['text']}")
        return "\n".join(lines)


rag_retriever = RAGRetriever()
