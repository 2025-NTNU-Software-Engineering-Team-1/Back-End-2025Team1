"""
    Utility functions for handling Personal Access Tokens (PATs).
"""

import hashlib

def hash_pat_token(pat_token: str) -> str:
    """Computes SHA-256 hash for the Personal Access Token."""
    return hashlib.sha256(pat_token.encode('utf-8')).hexdigest()