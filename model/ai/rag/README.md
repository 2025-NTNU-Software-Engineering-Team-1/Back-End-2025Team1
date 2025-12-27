# RAG Module

This package contains the RAG (Retrieval-Augmented Generation) functionality using ChromaDB.

## Components
- `retriever.py`: Document retrieval and storage using ChromaDB's built-in embeddings

## Usage
ChromaDB handles embeddings internally using Sentence Transformers, no separate embedder needed.

## Future Enhancements
- Custom embedding model support (Gemini Text Embedding)
- Automatic problem indexing on creation/update
