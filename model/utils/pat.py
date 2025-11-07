"""
    Utility functions for handling Personal Access Tokens (PATs).
"""

import hashlib
from datetime import datetime, timezone, timedelta
from mongo.engine import PersonalAccessToken, TAIPEI_TIMEZONE
from . import HTTPError

__all__ = [
    'hash_pat_token',
    'get_pat_status',
    'add_pat_to_database',
    '_clean_token',
    'validate_scope_for_role',
    'validate_pat_due_time',
]


def hash_pat_token(pat_token: str) -> str:
    """Computes SHA-256 hash for the Personal Access Token."""
    return hashlib.sha256(pat_token.encode('utf-8')).hexdigest()


def get_pat_status(pat_obj):
    """判斷 PAT token 的狀態"""
    if pat_obj.is_revoked:
        return "deactivated"

    if pat_obj.due_time:
        now = datetime.now(timezone.utc)
        due_time = pat_obj.due_time
        if due_time.tzinfo is None:
            due_time = due_time.replace(tzinfo=timezone.utc)
        if now > due_time:
            return "due"

    return "active"


def add_pat_to_database(pat_id,
                        name,
                        owner,
                        hash_val,
                        scope=None,
                        due_time=None):
    """在資料庫中新增 PAT token"""
    try:
        pat = PersonalAccessToken(
            pat_id=pat_id,
            name=name or "",
            owner=owner,
            hash=hash_val,
            scope=scope or [],
            due_time=due_time,
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        )
        pat.save()
        return pat
    except Exception as e:
        raise Exception(f"Failed to save PAT to database: {str(e)}")


def _clean_token(pat_obj, timezone=TAIPEI_TIMEZONE):
    """
    Convert PersonalAccessToken MongoDB object to API response format.
    
    Timestamps will convert to TAIPEI timezone (UTC+8) in ISO 8601 format by default.
    """
    status = get_pat_status(pat_obj)
    created_time = pat_obj.created_time.astimezone(
        timezone).isoformat() if pat_obj.created_time else None
    due_time = pat_obj.due_time.astimezone(
        timezone).isoformat() if pat_obj.due_time else None
    last_used_time = pat_obj.last_used_time.astimezone(
        timezone).isoformat() if pat_obj.last_used_time else None
    return {
        "Name": pat_obj.name,
        "ID": pat_obj.pat_id,
        "Owner": pat_obj.owner,
        "Status": status.capitalize(),
        "Created": created_time,
        "Due_Time": due_time,
        "Last_Used": last_used_time,
        "Scope": pat_obj.scope or [],
    }


def validate_scope_for_role(scope_set: list, user_role_key,
                            role_scope_map) -> bool:
    """Validate if all scopes in scope_set are allowed for the given user role."""
    allowed_scopes = role_scope_map.get(user_role_key, [])
    for scope in scope_set:
        if scope not in allowed_scopes:
            return False
    return True


def validate_pat_due_time(due_time_str, local_timezone=TAIPEI_TIMEZONE):
    """
    Validates the PAT Due_Time string.
    Returns (datetime_obj, None) on success, or (None, HTTPError) on failure.
    Treat the due_time_str as TAIPEI timezone if no timezone info is provided.
    Then convert to UTC for storage.
    """
    if not due_time_str:
        return None, None

    try:
        due_time_obj = datetime.fromisoformat(
            due_time_str.replace("Z", "+00:00"))
        if due_time_obj.tzinfo is None:
            due_time_obj = due_time_obj.replace(tzinfo=local_timezone)
    except (ValueError, AttributeError):
        return None, HTTPError(
            "Invalid Due_Time format",
            400,
            data={
                "Type": "ERR",
                "Message": "Invalid Due_Time format"
            },
        )

    due_time_obj = due_time_obj.astimezone(timezone.utc)

    now_utc = datetime.now(timezone.utc)

    if due_time_obj <= now_utc:
        return None, HTTPError(
            "Due_Time must be in the future",
            400,
            data={
                "Type": "ERR",
                "Message": "Due_Time must be in the future"
            },
        )

    return due_time_obj, None
