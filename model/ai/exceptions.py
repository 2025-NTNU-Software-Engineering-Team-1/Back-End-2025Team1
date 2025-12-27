"""
AI-specific exceptions.
"""

from .logging import get_logger

logger = get_logger('exceptions')

__all__ = [
    'AIError',
    'AIServiceError',
    'RateLimitExceededError',
    'ContextNotFoundError',
]


class AIError(Exception):
    """Base exception for AI module."""

    def __init__(self, message: str):
        self.message = message
        logger.error(f"AIError: {message}")
        super().__init__(message)


class AIServiceError(AIError):
    """Exception raised when AI provider returns an error."""

    def __init__(self, message: str, status_code: int = None):
        self.status_code = status_code
        logger.error(f"AIServiceError (status={status_code}): {message}")
        super().__init__(message)


class RateLimitExceededError(AIError):
    """Exception raised when rate limit is exceeded."""

    def __init__(self, message: str = "Rate limit exceeded"):
        logger.warning(f"RateLimitExceededError: {message}")
        super().__init__(message)


class ContextNotFoundError(AIError):
    """Exception raised when problem context cannot be found."""

    def __init__(self, problem_id: str = None):
        message = f"Context not found for problem: {problem_id}" if problem_id else "Context not found"
        logger.warning(f"ContextNotFoundError: {message}")
        super().__init__(message)
