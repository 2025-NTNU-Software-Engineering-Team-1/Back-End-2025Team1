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
        "samples": [],
        "last_submission_summary": "No previous submission found.",
        "last_submission_error": "",
        "last_trial_summary": "No previous trial submission found."
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
            status_code = getattr(last_sub, 'status', -1)
            status_str = status_map.get(status_code, "Unknown")
            score = getattr(last_sub, 'score', 0)
            context[
                "last_submission_summary"] = f"Result: {status_str}, Score: {score}/100"
            logger.debug(
                f"Found last submission for user {user.username}: {status_str}"
            )

            # Get error details for non-AC submissions
            if status_code != 0:
                try:
                    # Try to get compile error message
                    if status_code == -2:
                        ce_msg = getattr(last_sub, 'stderr', '') or ''
                        if ce_msg:
                            context[
                                "last_submission_error"] = f"Compile Error: {ce_msg[:500]}"
                    # For other errors, try to get task/case error info
                    elif hasattr(last_sub, 'tasks') and last_sub.tasks:
                        first_failed = None
                        for task in last_sub.tasks:
                            if hasattr(task, 'cases'):
                                for case in task.cases:
                                    case_status = getattr(case, 'status', 0)
                                    if case_status != 0:
                                        first_failed = case
                                        break
                            if first_failed:
                                break
                        if first_failed and hasattr(first_failed, 'status'):
                            context[
                                "last_submission_error"] = f"First failed case status: {status_map.get(first_failed.status, 'Error')}"
                except Exception as e:
                    logger.debug(f"Could not get error details: {e}")
    except Exception as e:
        logger.debug(f"No last submission found for user {user.username}: {e}")

    # Get student's last trial submission status
    try:
        last_trial = TrialSubmission.get_last_trial(problem_id, user.username)
        if last_trial:
            trial_status = getattr(last_trial, 'status', -1)
            trial_score = getattr(last_trial, 'score', 0)
            status_map = {
                0: "Accepted",
                -1: "Wrong Answer / Running",
                -2: "Compile Error",
                1: "Time Limit Exceeded",
                3: "Runtime Error"
            }
            trial_status_str = status_map.get(trial_status, "Unknown")
            context[
                "last_trial_summary"] = f"Trial Result: {trial_status_str}, Score: {trial_score}"
            logger.debug(
                f"Found last trial for user {user.username}: {trial_status_str}"
            )
    except Exception as e:
        logger.debug(f"No last trial found for user {user.username}: {e}")

    logger.info(f"Built context for problem: {context['title']}")
    return context
