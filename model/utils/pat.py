"""
    Utility functions for handling Personal Access Tokens (PATs).
"""

import hashlib
from datetime import datetime, timezone, timedelta
from mongo.engine import PersonalAccessToken


def hash_pat_token(pat_token: str) -> str:
    """Computes SHA-256 hash for the Personal Access Token."""
    return hashlib.sha256(pat_token.encode('utf-8')).hexdigest()


def get_pat_status(pat_obj):
    """判斷 PAT token 的狀態"""
    if pat_obj.is_revoked:
        return "deactivated"  # When revoked by Admin

    if pat_obj.due_time:
        # Ensure both datetimes have timezone info for comparison
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


def _clean_token(pat_obj):
    """Convert PersonalAccessToken MongoDB object to API response format"""
    status = get_pat_status(pat_obj)
    return {
        "Name":
        pat_obj.name,
        "ID":
        pat_obj.pat_id,
        "Owner":
        pat_obj.owner,
        "Status":
        status.capitalize(),  # 'Active', 'Expired', 'Revoked'
        "Created":
        pat_obj.created_time.isoformat() if pat_obj.created_time else None,
        "Due_Time":
        pat_obj.due_time.isoformat() if pat_obj.due_time else None,
        "Last_Used": (pat_obj.last_used_time.isoformat()
                      if pat_obj.last_used_time else None),
        "Scope":
        pat_obj.scope or [],
    }


# Validate if Scope Set is allowed for the user's role
def validate_scope_for_role(scope_set: list, user_role_key,
                            role_scope_map) -> bool:
    """Validate if all scopes in scope_set are allowed for the given user role."""
    allowed_scopes = role_scope_map.get(user_role_key, [])
    for scope in scope_set:
        if scope not in allowed_scopes:
            return False
    return True
