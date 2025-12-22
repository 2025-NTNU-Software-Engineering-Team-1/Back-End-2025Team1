from flask import Blueprint, current_app

from mongo import *
from model.auth import login_required
from .utils import *

# AI Helper must be explicitly imported
from .utils.ai import *

__all__ = ['ai_api']

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

    # 2. Call AI Processing Helper
    try:
        response_data = process_ai_request(user=user,
                                           course_name=course_name,
                                           problem_id=problem_id,
                                           message=message,
                                           current_code=current_code)
        current_app.logger.debug(f"AI Response Data: {response_data}")
        return HTTPResponse(data=response_data)

    except PermissionError as e:
        return HTTPError(str(e), 403)
    except ValueError as e:
        return HTTPError(str(e), 404)  # Context not found
    except RuntimeError as e:
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

    # Query Logs via wrapper (use course_name)
    logs = AiApiLog.get_history(course_name, user.username) or []

    # Format output
    result = []
    for log in logs:
        role = log.get('role')
        parts = log.get('parts', [])

        # Merge text from multiple parts
        content = ""
        for part in parts:
            if isinstance(part, dict):
                content += part.get('text', "")

        result.append({"role": role, "text": content})

    return HTTPResponse(data=result)
