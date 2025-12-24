"""
RAG (Retrieval-Augmented Generation) Module.

This module provides document retrieval functionality using ChromaDB.
"""

from .retriever import retrieve_context, add_to_knowledge_base

__all__ = [
    'retrieve_context',
    'add_to_knowledge_base',
]
