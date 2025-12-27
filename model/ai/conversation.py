"""
Conversation history management.
"""

from typing import List, Optional

from mongo import AiApiLog

from .logging import get_logger

logger = get_logger('conversation')

__all__ = [
    'get_conversation_history',
    'reset_conversation_history',
    'format_history_for_ai',
]


def get_conversation_history(course_name: str,
                             username: str,
                             limit: int = 10) -> List[dict]:
    """
    Get conversation history for a student in a course.
    
    Args:
        course_name: The course identifier.
        username: The student's username.
        limit: Maximum number of messages to return (default: 10).
        
    Returns:
        List of message dictionaries with 'role' and 'text' keys.
    """
    try:
        raw_history = AiApiLog.get_history(course_name, username) or []
        recent_history = raw_history[-limit:] if raw_history else []

        result = []
        for log in recent_history:
            role = log.get('role')
            parts = log.get('parts', [])

            # Merge text from multiple parts
            content = ""
            for part in parts:
                if isinstance(part, dict):
                    content += part.get('text', "")

            result.append({"role": role, "text": content})

        logger.debug(
            f"Retrieved {len(result)} history messages for user {username}")
        return result

    except Exception as e:
        logger.error(f"Error fetching history for {username}: {e}")
        return []


def format_history_for_ai(history: List[dict]) -> List[dict]:
    """
    Format conversation history for Gemini API.
    
    Args:
        history: List of message dicts with 'role' and 'text' keys.
        
    Returns:
        List of message objects in Gemini API format.
    """
    formatted = []
    for msg in history:
        formatted.append({
            "role": msg.get('role'),
            "parts": [{
                "text": msg.get('text', '')
            }]
        })
    return formatted


def reset_conversation_history(course_name: str, username: str) -> bool:
    """
    Clear conversation history for a student in a course.
    
    Args:
        course_name: The course identifier.
        username: The student's username.
        
    Returns:
        True if successful, False otherwise.
    """
    try:
        result = AiApiLog.clear_history(course_name, username)
        if result:
            logger.info(
                f"Cleared conversation history for user {username} in course {course_name}"
            )
        else:
            logger.warning(
                f"No history found to clear for user {username} in course {course_name}"
            )
        return result
    except Exception as e:
        logger.error(f"Error clearing history for {username}: {e}")
        return False


def add_message_to_history(course_name: str,
                           username: str,
                           role: str,
                           text: str,
                           emotion: str = None) -> bool:
    """
    Add a message to conversation history.
    
    Args:
        course_name: The course identifier.
        username: The student's username.
        role: Message role ('user' or 'model').
        text: Message content.
        emotion: Optional emotion (for model messages).
        
    Returns:
        True if successful, False otherwise.
    """
    try:
        result = AiApiLog.add_message(course_name, username, role, text,
                                      emotion)
        logger.debug(f"Added {role} message to history for user {username}")
        return result
    except Exception as e:
        logger.error(f"Error adding message to history: {e}")
        return False
