"""
AI Module for Normal-OJ.

This package provides modular AI services including:
- AI Provider integration (Gemini)
- Prompt management
- API Key management and rate limiting
- Conversation history management
- RAG (Retrieval-Augmented Generation)
- AI Vtuber Skin management
"""

from .exceptions import (
    AIError,
    AIServiceError,
    RateLimitExceededError,
    ContextNotFoundError,
)
from .service import call_ai_service
from .key_manager import check_rate_limit
from .context import get_problem_context
from .conversation import get_conversation_history, reset_conversation_history
from .prompts import build_vtuber_prompt, EMOTION_KEYWORDS
from .skin import skin_api

__all__ = [
    # Exceptions
    'AIError',
    'AIServiceError',
    'RateLimitExceededError',
    'ContextNotFoundError',
    # Service
    'call_ai_service',
    # Key Management
    'check_rate_limit',
    # Context
    'get_problem_context',
    # Conversation
    'get_conversation_history',
    'reset_conversation_history',
    # Prompts
    'build_vtuber_prompt',
    'EMOTION_KEYWORDS',
    # Skin API
    'skin_api',
]
