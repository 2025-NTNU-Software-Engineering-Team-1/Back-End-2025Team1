"""
Utility functions for submission-related operations.
"""
from flask import current_app
from mongo.utils import RedisCache
from mongo.submission import Submission


def clear_submission_list_cache_for_submission(submission_id: str):
    """
    Clear submission list cache entries that may contain the specified submission.
    
    Since list cache keys are based on query parameters (user, problem_id, username, 
    status, language_type, course, offset, count), we cannot precisely target a single
    submission. However, we can be more precise by clearing caches that match the
    submission's attributes.
    
    Strategy:
    - Get submission attributes (problem_id, user, status, language_type, course)
    - Clear caches that match these attributes (more precise than clearing all problem caches)
    - Also clear caches without problem_id filter (queries for all problems)
    
    Note: This still clears multiple cache entries because the same submission can appear
    in different query results (different offset/count, different username filters, etc.)
    
    Args:
        submission_id: The submission ID to clear cache for
        
    Returns:
        int: Number of cache entries cleared
    """
    try:
        submission = Submission(submission_id)
        problem_id = submission.problem_id
        submission_user = submission.user
        submission_status = submission.status
        submission_language = submission.language
        # Get course from problem
        problem = submission.problem
        courses = [c.course_name for c in problem.courses] if hasattr(
            problem, 'courses') else []

        cache = RedisCache()
        deleted_count = 0

        # Strategy: Clear caches that could contain this submission
        # We need to clear:
        # 1. Caches for this problem_id (the submission definitely belongs to this problem)
        # 2. Caches without problem_id filter (queries for all problems)

        # Pattern 1: Clear caches for this specific problem_id
        # Format: SUBMISSION_LIST_API_{user}_{problem_id}_{username}_{status}_{language_type}_{course}_{offset}_{count}
        pattern1 = f'SUBMISSION_LIST_API_*_{problem_id}_*'
        cursor = 0
        while True:
            cursor, keys = cache.client.scan(cursor, match=pattern1, count=100)
            if keys:
                cache.client.delete(*keys)
                deleted_count += len(keys)
            if cursor == 0:
                break

        # Pattern 2: Clear caches without problem_id filter (queries for all problems)
        # Format: SUBMISSION_LIST_API_{user}_None_{username}_{status}_{language_type}_{course}_{offset}_{count}
        # Note: When problem_id is None, it's converted to string "None" in the cache key
        pattern2 = 'SUBMISSION_LIST_API_*_None_*'
        cursor = 0
        while True:
            cursor, keys = cache.client.scan(cursor, match=pattern2, count=100)
            if keys:
                cache.client.delete(*keys)
                deleted_count += len(keys)
            if cursor == 0:
                break

        current_app.logger.debug(
            f"Cleared {deleted_count} submission list cache entries for submission {submission_id} "
            f"(problem {problem_id}, status {submission_status}, language {submission_language})"
        )
        return deleted_count
    except Exception as e:
        current_app.logger.warning(
            f"Failed to clear submission list cache for submission {submission_id}: {e}"
        )
        return 0
