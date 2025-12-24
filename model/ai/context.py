"""
Problem context collection for AI services.
"""

from typing import Optional

from mongo import Problem, Submission

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
        - input_format: Input format description
        - output_format: Output format description
        - samples: Sample test cases
        - last_submission_summary: Student's last submission status
        
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
        "input_format": getattr(desc, 'input', "") if desc else "",
        "output_format": getattr(desc, 'output', "") if desc else "",
        "samples": [],
        "last_submission_summary": "No previous submission found."
    }

    # Process sample test cases
    if desc and hasattr(desc, 'sample_input') and desc.sample_input:
        limit = 2
        sample_output = getattr(desc, 'sample_output', []) or []
        context["samples"] = [{
            "input": s_in,
            "output": s_out
        } for i, (s_in,
                  s_out) in enumerate(zip(desc.sample_input, sample_output))
                              if i < limit]

    # Get student's last submission status
    try:
        last_sub = Submission.get_last_submission(problem_id, user.username)
        if last_sub:
            status_map = {
                0: "Accepted",
                -1: "Wrong Answer",
                -2: "Compile Error",
                1: "Time Limit Exceeded",
                3: "Runtime Error"
            }
            status_str = status_map.get(getattr(last_sub, 'status', -1),
                                        "Unknown")
            score = getattr(last_sub, 'score', 0)
            context[
                "last_submission_summary"] = f"Result: {status_str}, Score: {score}/100"
            logger.debug(
                f"Found last submission for user {user.username}: {status_str}"
            )
    except Exception as e:
        logger.debug(f"No last submission found for user {user.username}: {e}")

    logger.info(f"Built context for problem: {context['title']}")
    return context
