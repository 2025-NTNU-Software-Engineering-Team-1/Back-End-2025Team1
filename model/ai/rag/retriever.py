"""
Document retriever using ChromaDB for vector search.
"""

import os
from typing import List, Optional

from ..logging import get_logger

logger = get_logger('rag.retriever')

__all__ = [
    'retrieve_context',
    'add_to_knowledge_base',
    'get_chroma_client',
]

# ChromaDB instance (lazy initialized)
_chroma_client = None
_collection = None

# Default paths
CHROMA_PERSIST_DIR = os.environ.get('CHROMA_PERSIST_DIR', './chroma_data')
COLLECTION_NAME = "noj_knowledge_base"


def get_chroma_client():
    """
    Get or create ChromaDB client (singleton pattern).
    
    Returns:
        ChromaDB client instance.
    """
    global _chroma_client, _collection

    if _chroma_client is None:
        try:
            import chromadb
            from chromadb.config import Settings

            _chroma_client = chromadb.Client(
                Settings(chroma_db_impl="duckdb+parquet",
                         persist_directory=CHROMA_PERSIST_DIR,
                         anonymized_telemetry=False))

            _collection = _chroma_client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"description": "Normal-OJ Knowledge Base"})

            logger.info(f"ChromaDB initialized at {CHROMA_PERSIST_DIR}")

        except ImportError:
            logger.warning("ChromaDB not installed. RAG features disabled.")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize ChromaDB: {e}")
            return None

    return _chroma_client


def retrieve_context(query: str,
                     top_k: int = 3,
                     course_name: str = None) -> List[dict]:
    """
    Retrieve relevant documents from knowledge base.
    
    Args:
        query: Search query (student's question).
        top_k: Number of results to return.
        course_name: Optional filter by course.
        
    Returns:
        List of relevant document snippets.
    """
    global _collection

    if _collection is None:
        get_chroma_client()
        if _collection is None:
            logger.warning("ChromaDB not available, returning empty results")
            return []

    try:
        # Build where filter
        where_filter = None
        if course_name:
            where_filter = {"course_name": course_name}

        results = _collection.query(query_texts=[query],
                                    n_results=top_k,
                                    where=where_filter)

        documents = []
        if results and results.get('documents'):
            for i, doc in enumerate(results['documents'][0]):
                metadata = results['metadatas'][0][i] if results.get(
                    'metadatas') else {}
                documents.append({"content": doc, "metadata": metadata})

        logger.debug(f"Retrieved {len(documents)} documents for query")
        return documents

    except Exception as e:
        logger.error(f"Error retrieving context: {e}")
        return []


def add_to_knowledge_base(document_id: str,
                          content: str,
                          metadata: dict = None) -> bool:
    """
    Add a document to the knowledge base.
    
    Args:
        document_id: Unique identifier for the document.
        content: Document text content.
        metadata: Optional metadata (course_name, problem_id, etc.).
        
    Returns:
        True if successful, False otherwise.
    """
    global _collection

    if _collection is None:
        get_chroma_client()
        if _collection is None:
            logger.warning("ChromaDB not available")
            return False

    try:
        _collection.add(ids=[document_id],
                        documents=[content],
                        metadatas=[metadata or {}])

        logger.info(f"Added document to knowledge base: {document_id}")
        return True

    except Exception as e:
        logger.error(f"Error adding to knowledge base: {e}")
        return False
