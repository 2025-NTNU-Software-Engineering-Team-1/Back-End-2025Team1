"""
Vtuber-specific AI logic including emotion handling and persona.
"""

import json
from typing import Optional

from mongo import AiApiKey, AiTokenUsage

from .prompts import EMOTION_KEYWORDS, build_vtuber_prompt
from .service import call_ai_service
from .key_manager import check_rate_limit, get_model_for_course
from .context import get_problem_context
from .conversation import (
    get_conversation_history,
    format_history_for_ai,
    add_message_to_history,
)
from .exceptions import AIError, RateLimitExceededError, ContextNotFoundError
from .logging import get_logger

logger = get_logger('vtuber')

__all__ = [
    'process_vtuber_request',
    'validate_emotion',
]


def validate_emotion(emotion: Optional[str]) -> str:
    """
    Validate and normalize emotion value.
    
    Args:
        emotion: Raw emotion string from AI response.
        
    Returns:
        Valid emotion string, defaults to 'smile' if invalid.
    """
    if emotion is None:
        return "smile"

    normalized = str(emotion).strip().lower()
    if normalized in EMOTION_KEYWORDS:
        return normalized

    logger.debug(f"Invalid emotion '{emotion}', defaulting to 'smile'")
    return "smile"


def process_vtuber_request(user,
                           course_name: str,
                           problem_id: str,
                           message: str,
                           current_code: str = "") -> dict:
    """
    Process an AI Vtuber request from a student.
    
    This is the main entry point for Vtuber AI interactions.
    
    Args:
        user: User object with username attribute.
        course_name: The course identifier.
        problem_id: The problem identifier.
        message: Student's message/question.
        current_code: Optional code submitted by student.
        
    Returns:
        Response dictionary with AI-generated content.
        
    Raises:
        RateLimitExceededError: If rate limit is exceeded.
        ContextNotFoundError: If problem context not found.
        AIError: If AI service fails.
    """
    # 1. Check rate limit and get API key
    key, error_msg = check_rate_limit(course_name)
    if not key:
        logger.warning(f"AI Request Denied for {user.username}: {error_msg}")
        raise RateLimitExceededError(error_msg)

    # 2. Get problem context
    context = get_problem_context(problem_id, user)
    if not context:
        raise ContextNotFoundError(problem_id)

    # 3. Get conversation history
    raw_history = get_conversation_history(course_name,
                                           user.username,
                                           limit=10)
    history_for_ai = format_history_for_ai(raw_history)

    # 4. Build system prompt
    system_prompt = build_vtuber_prompt(context)

    # 5. Get model name
    model_name = get_model_for_course(course_name)

    # 6. Call AI service
    response_json, in_tokens, out_tokens = call_ai_service(
        api_key_value=key.key_value,
        system_prompt=system_prompt,
        history_messages=history_for_ai,
        user_message=message,
        current_code=current_code,
        model_name=model_name,
        use_structured_output=True)

    # 7. Validate emotions in response
    if isinstance(response_json, dict) and isinstance(
            response_json.get('data'), list):
        for item in response_json['data']:
            if isinstance(item, dict):
                item['emotion'] = validate_emotion(item.get('emotion'))

    # 8. Save transaction
    _save_transaction(course_name=course_name,
                      username=user.username,
                      key_obj=key,
                      user_text=message,
                      ai_response=response_json,
                      input_tokens=in_tokens,
                      output_tokens=out_tokens,
                      problem_id=problem_id)

    logger.info(f"Vtuber request processed for user {user.username}")
    return response_json


def _save_transaction(course_name: str,
                      username: str,
                      key_obj,
                      user_text: str,
                      ai_response: dict,
                      input_tokens: int,
                      output_tokens: int,
                      problem_id: str = None) -> bool:
    """
    Save AI transaction records.
    
    Saves:
    1. Conversation logs (AiApiLog)
    2. Token usage (AiTokenUsage)
    3. Updates key usage counters (AiApiKey)
    """
    try:
        ai_response_text = json.dumps(ai_response, ensure_ascii=False)

        # Get emotion from first response item
        emotion = None
        if isinstance(ai_response, dict) and ai_response.get('data'):
            first_item = ai_response['data'][0] if ai_response['data'] else {}
            emotion = first_item.get('emotion')

        # 1. Save conversation logs
        add_message_to_history(course_name, username, "user", user_text)
        add_message_to_history(course_name, username, "model",
                               ai_response_text, emotion)

        # 2. Save token usage
        key_doc = key_obj.obj if hasattr(key_obj, 'obj') else key_obj
        AiTokenUsage.add_usage(api_key_obj=key_doc,
                               course_name=course_name,
                               input_tokens=input_tokens,
                               output_tokens=output_tokens,
                               problem_id=problem_id)

        # 3. Update key usage counters
        if hasattr(key_obj, 'increment_usage'):
            key_obj.increment_usage(input_tokens, output_tokens)

        logger.debug(
            f"Saved transaction: in={input_tokens}, out={output_tokens}")
        return True

    except Exception as e:
        logger.error(f"Failed to save AI transaction: {str(e)}")
        return False
