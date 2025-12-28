"""
Problem context collection for AI services.
"""

from datetime import datetime
from typing import Optional

from mongo import Problem, Submission
from mongo.submission import TrialSubmission

from .exceptions import ContextNotFoundError
from .logging import get_logger

logger = get_logger('context')

__all__ = [
    'get_problem_context',
]


def get_problem_context(problem_id: str, user) -> Optional[dict]:
    """
    Collect context information for a problem.
    
    Args:
        problem_id: The problem identifier.
        user: User object (with username attribute).
        
    Returns:
        Context dictionary containing:
        - title: Problem name
        - description: Problem description
        - hint: Problem hint (if available)
        - current_time: Current time for natural conversation
        - input_format: Input format description
        - output_format: Output format description
        - samples: Sample test cases
        - last_submission_summary: Student's last submission status
        - last_submission_error: Error details from last submission (if non-AC)
        - last_trial_summary: Student's last trial submission status
        
    Raises:
        ContextNotFoundError: If problem cannot be found.
    """
    try:
        p = Problem(problem_id)
        if not p:
            logger.warning(f"Problem not found: {problem_id}")
            raise ContextNotFoundError(problem_id)
    except ContextNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error fetching problem {problem_id}: {e}")
        raise ContextNotFoundError(problem_id)

    # Assemble basic problem information
    desc = getattr(p, 'description', None)
    context = {
        "title": getattr(p, 'problem_name', ""),
        "description": getattr(desc, 'description', "") if desc else "",
        "hint": getattr(desc, 'hint', "") if desc else "",
        "current_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "input_format": getattr(desc, 'input', "") if desc else "",
        "output_format": getattr(desc, 'output', "") if desc else "",
        "samples": p.get_samples(limit=2),
        "last_submission_summary": "No previous submission found.",
        "last_submission_error": "",
        "last_trial_summary": "No previous trial submission found."
    }

    # Get student's last submission status
    try:
        last_sub = Submission.get_last_submission(problem_id, user.username)
        if last_sub:
            score = getattr(last_sub, 'score', 0)
            context[
                "last_submission_summary"] = f"Result: {last_sub.status_str}, Score: {score}/100"
            logger.debug(
                f"Found last submission for user {user.username}: {last_sub.status_str}"
            )

            # Get error details for non-AC submissions
            context["last_submission_error"] = last_sub.get_error_detail()
    except Exception as e:
        logger.debug(f"No last submission found for user {user.username}: {e}")

    # Get student's last trial submission status
    try:
        last_trial = TrialSubmission.get_last_trial(problem_id, user.username)
        if last_trial:
            trial_score = getattr(last_trial, 'score', 0)
            context[
                "last_trial_summary"] = f"Trial Result: {last_trial.status_str}, Score: {trial_score}"
            logger.debug(
                f"Found last trial for user {user.username}: {last_trial.status_str}"
            )
    except Exception as e:
        logger.debug(f"No last trial found for user {user.username}: {e}")

    logger.info(f"Built context for problem: {context['title']}")
    return context
