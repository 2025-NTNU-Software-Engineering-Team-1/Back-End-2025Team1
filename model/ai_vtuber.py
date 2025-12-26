"""
AI Vtuber API Routes.

This module provides the Flask Blueprint for AI Vtuber endpoints.
"""

from flask import Blueprint, current_app

from mongo import AiApiLog
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

__all__ = ['ai_api', 'skin_api']

ai_api = Blueprint('ai_api', __name__)


@ai_api.route('/chatbot/ask', methods=['POST'])
@login_required
@Request.json('message', 'current_code', 'problem_id', 'course_name')
def ask(user=None,
        message=None,
        current_code='',
        problem_id=None,
        course_name=None):
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
                                               current_code=current_code)
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
