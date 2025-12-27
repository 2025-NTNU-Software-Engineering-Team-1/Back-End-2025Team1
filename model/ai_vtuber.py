"""
AI Vtuber API Routes.

This module provides the Flask Blueprint for AI Vtuber endpoints.
"""

from flask import Blueprint, current_app

from mongo import AiApiLog, Problem
from model.auth import login_required
from .utils import Request, HTTPError, HTTPResponse

# Import from new AI module
from .ai import (
    RateLimitExceededError,
    ContextNotFoundError,
    AIError,
    skin_api,
)
from .ai.vtuber import process_vtuber_request
from .ai.conversation import get_conversation_history, reset_conversation_history
from .ai.testcase_generator import generate_testcase

__all__ = ['ai_api', 'skin_api']

ai_api = Blueprint('ai_api', __name__)


@ai_api.route('/chatbot/ask', methods=['POST'])
@login_required
@Request.json('message', 'current_code', 'problem_id', 'course_name',
              'language')
def ask(user=None,
        message=None,
        current_code='',
        problem_id=None,
        course_name=None,
        language='en'):
    """
    Student sends an AI prompt (with History & Emotion support).
    """
    # 1. Validate input
    if not message:
        return HTTPError('Missing message', 400)
    if not problem_id:
        return HTTPError('Missing problem_id', 400)
    if not course_name:
        return HTTPError('Missing course_name', 400)

    # 2. Call AI Processing
    try:
        response_data = process_vtuber_request(user=user,
                                               course_name=course_name,
                                               problem_id=problem_id,
                                               message=message,
                                               current_code=current_code,
                                               language=language or 'en')
        current_app.logger.debug(f"AI Response Data: {response_data}")
        return HTTPResponse(data=response_data)

    except RateLimitExceededError as e:
        return HTTPError(str(e), 403)
    except ContextNotFoundError as e:
        return HTTPError(str(e), 404)
    except AIError as e:
        current_app.logger.error(f"AI Error: {e}")
        return HTTPError(str(e), 500)
    except Exception as e:
        current_app.logger.error(f"Unexpected error: {e}")
        return HTTPError('Internal Server Error', 500)


@ai_api.route('/chatbot/history', methods=['GET'])
@login_required
@Request.args('course_name')
def history(user, course_name):
    """
    Retrieve conversation history.
    GET /api/chatbot/history?course_name=...
    """
    if not course_name:
        return HTTPError('Missing course_name', 400)

    result = get_conversation_history(course_name, user.username)
    return HTTPResponse(data=result)


@ai_api.route('/chatbot/history', methods=['DELETE'])
@login_required
@Request.args('course_name')
def reset_history(user, course_name):
    """
    Clear conversation history for the current user.
    DELETE /api/chatbot/history?course_name=...
    """
    if not course_name:
        return HTTPError('Missing course_name', 400)

    success = reset_conversation_history(course_name, user.username)

    if success:
        current_app.logger.info(f"History cleared for user {user.username}")
        return HTTPResponse(message="History cleared successfully")
    else:
        return HTTPError("Failed to clear history", 500)


@ai_api.route('/generate-testcase', methods=['POST'])
@login_required
@Request.json('problem_id', 'course_name', 'hint', 'language')
def generate_testcase_endpoint(user=None,
                               problem_id=None,
                               course_name=None,
                               hint='',
                               language='en'):
    """
    Generate a test case for a problem using AI.
    POST /api/ai/generate-testcase
    
    Args (JSON body):
        problem_id: The problem ID
        course_name: The course name (for API key lookup)
        hint: Optional hint about what kind of test case to generate
        language: User's language setting (e.g., 'en', 'zh-tw')
        
    Returns:
        JSON with input, expected_output, explanation
    """
    from .ai.key_manager import check_rate_limit
    from .ai.logging import get_logger

    logger = get_logger('testcase_api')

    logger.info(
        f"[TestcaseGen] Request from {user.username}: problem={problem_id}, course={course_name}, lang={language}"
    )

    # Validate input
    if not problem_id:
        logger.warning("[TestcaseGen] Missing problem_id")
        return HTTPError('Missing problem_id', 400)
    if not course_name:
        logger.warning("[TestcaseGen] Missing course_name")
        return HTTPError('Missing course_name', 400)

    try:
        # Get problem
        problem = Problem(problem_id)
        if not problem:
            logger.error(f"[TestcaseGen] Problem not found: {problem_id}")
            return HTTPError('Problem not found', 404)

        # Get API key using same logic as chatbot
        key, error_msg = check_rate_limit(course_name)
        if not key:
            logger.warning(f"[TestcaseGen] No API key available: {error_msg}")
            return HTTPError('No API key configured for this course', 400)

        api_key = key.key_value
        logger.info(f"[TestcaseGen] Using API key: {key.key_name}")

        # Get model from course config (same as chatbot)
        from .ai.key_manager import get_model_for_course
        model = get_model_for_course(course_name)
        logger.info(f"[TestcaseGen] Using model: {model}")

        # Generate test case
        result = generate_testcase(problem_id=str(problem_id),
                                   user=user,
                                   user_hint=hint or '',
                                   api_key=api_key,
                                   model=model,
                                   language=language or 'en')

        logger.info(
            f"[TestcaseGen] Successfully generated testcase for problem {problem_id}"
        )
        return HTTPResponse(data=result)

    except ContextNotFoundError as e:
        logger.error(f"[TestcaseGen] Context not found: {e}")
        return HTTPError(str(e), 404)
    except AIError as e:
        # AIServiceError inherits from AIError
        logger.warning(f"[TestcaseGen] AI service error: {e}")
        status_code = getattr(e, 'status_code', 500)
        if status_code == 429:
            return HTTPError('API quota exceeded. Please try again later.',
                             429)
        return HTTPError(str(e), status_code)
    except ValueError as e:
        logger.error(f"[TestcaseGen] ValueError: {e}")
        return HTTPError(str(e), 400)
    except Exception as e:
        logger.error(f"[TestcaseGen] Unexpected error: {e}", exc_info=True)
        current_app.logger.error(f"Testcase generation error: {e}")
        return HTTPError('Failed to generate test case', 500)
