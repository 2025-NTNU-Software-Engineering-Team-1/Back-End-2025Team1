"""
API Key management and rate limiting.
"""

from typing import Tuple, Optional

from mongo import AiApiKey, Course

from .exceptions import RateLimitExceededError
from .logging import get_logger

logger = get_logger('key_manager')

__all__ = [
    'get_available_key',
    'get_model_for_course',
]


def get_available_key(
        course_name: str) -> Tuple[Optional[object], Optional[str]]:
    """
    Check and select an available API Key by course_name.
    
    Args:
        course_name: The course identifier.
        
    Returns:
        Tuple of (key_wrapper, error_message).
        If successful, error_message is None.
        If failed, key_wrapper is None and error_message explains why.
    """
    # 1. Get course
    try:
        course = Course(course_name)
        if not course:
            logger.warning(f"Course not found: {course_name}")
            return None, "Course not found"
    except Exception as e:
        logger.error(f"Error fetching course {course_name}: {e}")
        return None, "Course not found"

    # 2. Check if AI is enabled for this course
    if not getattr(course, 'is_ai_vt_enabled', False):
        logger.info(f"AI disabled for course: {course_name}")
        return None, "AI assistant is disabled for this course."

    # 3. Check model configuration
    model = getattr(course, 'ai_model', None)
    if not model or not getattr(model, 'is_active', False):
        logger.warning(f"No active AI model for course: {course_name}")
        return None, "No active AI model configured."

    # 4. Find available keys
    keys = AiApiKey.get_active_keys_by_course_name(course_name)

    if not keys:
        logger.warning(f"No API keys configured for course: {course_name}")
        return None, "No API keys configured."

    # 5. Filter keys that haven't reached the model's RPD limit
    rpd_limit = getattr(model, 'rpd_limit', 1000)
    valid_keys = [k for k in keys if getattr(k, 'rpd', 0) < rpd_limit]

    if not valid_keys:
        logger.warning(
            f"Daily limit ({rpd_limit} RPD) reached for all keys in course: {course_name}"
        )
        return None, f"Daily limit ({rpd_limit} RPD) reached for all keys."

    # 6. Load Balancing: Select the key with the lowest RPD usage
    best_key = min(valid_keys, key=lambda k: getattr(k, 'rpd', 0))
    logger.debug(f"Selected API key with RPD={getattr(best_key, 'rpd', 0)}")

    return best_key, None


def get_model_for_course(course_name: str) -> str:
    """
    Get the AI model name configured for a course.
    
    Args:
        course_name: The course identifier.
        
    Returns:
        Model name string. Defaults to gemini-2.5-flash if not configured.
    """
    from .service import DEFAULT_MODEL

    try:
        course = Course(course_name)
        if course and course.ai_model:
            return course.ai_model.name
    except Exception as e:
        logger.error(f"Error getting model for course {course_name}: {e}")

    return DEFAULT_MODEL
