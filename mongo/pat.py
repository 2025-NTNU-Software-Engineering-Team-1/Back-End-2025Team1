import secrets
from uuid import uuid4
from typing import List, Optional, Any, Dict, Tuple
from datetime import datetime, timezone
import hashlib
from .base import MongoBase
from . import engine
from .user import Role

__all__ = ['PersonalAccessToken', 'PAT']


class PersonalAccessToken(MongoBase, engine=engine.PersonalAccessToken):
    objects = engine.PersonalAccessToken.objects

    @classmethod
    def generate(
            cls, name: str, owner: str, scope: List[str],
            due_time: Optional[datetime]) -> Tuple[str, 'PersonalAccessToken']:
        """
        Generates a new PAT with strict validation.
        Returns tuple: (plaintext_token, pat_object)
        """
        # 1. Generate secure token
        pat_id = uuid4().hex[:16]
        secret = secrets.token_urlsafe(32)
        plaintext_token = f"noj_pat_{secret}"
        hash_val = cls.hash_token(plaintext_token)

        # 2. Validate due_time (Business Logic)
        if due_time:
            now = datetime.now(timezone.utc)
            if due_time.tzinfo is None:
                due_time = due_time.replace(tzinfo=timezone.utc)
            if due_time <= now:
                raise ValueError("Due_Time must be in the future")

        # 3. Create and Save
        pat = cls.add(pat_id=pat_id,
                      name=name,
                      owner=owner,
                      hash_val=hash_val,
                      scope=scope,
                      due_time=due_time)

        return plaintext_token, pat

    @classmethod
    def add(cls, pat_id: str, name: str, owner: str, hash_val: str,
            scope: List[str],
            due_time: Optional[datetime]) -> 'PersonalAccessToken':
        """Adds a new PAT to the database using a pre-calculated hash value."""
        try:
            pat = cls.engine(
                pat_id=pat_id,
                name=name or "",
                owner=owner,
                hash=hash_val,
                scope=scope or [],
                due_time=due_time,
                created_time=datetime.now(timezone.utc),
                is_revoked=False,
            ).save()
            return cls(pat)
        except Exception as e:
            # Wrap as a generic exception or re-raise
            raise Exception(f"Failed to save PAT to database: {str(e)}")

    def revoke(self, user) -> bool:
        """
        Revokes the token.
        Checking if the user has permission to revoke this token.
        Admin can revoke any token. Owner can revoke their own token.
        """
        if self.is_revoked:
            raise ValueError("Token already revoked")

        # Permission check
        if user.role != Role.ADMIN and user.username != self.owner:
            raise PermissionError("Permission denied")

        try:
            self.update(
                is_revoked=True,
                revoked_by=user.username,
                revoked_time=datetime.now(timezone.utc),
            )
            self.reload()
            return True
        except Exception as e:
            raise Exception(f"Failed to revoke token: {str(e)}")

    @property
    def status(self) -> str:
        """Returns the status of the PAT token."""
        if self.is_revoked:
            return "deactivated"

        if self.due_time:
            # Ensure proper timezone comparison
            now = datetime.now(timezone.utc)
            due_time = self.due_time
            if due_time.tzinfo is None:
                due_time = due_time.replace(tzinfo=timezone.utc)
            if now > due_time:
                return "due"

        return "active"

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to API response format.
        Timestamps are converted to TAIPEI_TIMEZONE for API responses.
        """
        from .engine import TAIPEI_TIMEZONE

        def fmt(dt):
            return dt.astimezone(TAIPEI_TIMEZONE).isoformat() if dt else None

        return {
            "Name": self.name,
            "ID": self.pat_id,
            "Owner": self.owner,
            "Status": self.status.capitalize(),
            "Created": fmt(self.created_time),
            "Due_Time": fmt(self.due_time),
            "Last_Used": fmt(self.last_used_time),
            "Scope": self.scope or [],
        }

    @staticmethod
    def hash_token(token: str) -> str:
        """Computes SHA-256 hash for the Personal Access Token."""
        return hashlib.sha256(token.encode('utf-8')).hexdigest()

    @classmethod
    def get_by_hash(cls, token_hash: str) -> 'PersonalAccessToken':
        """
        Retrieves a PAT by its hash.
        Raises DoesNotExist if not found.
        """

        pat_doc = cls.engine.objects.get(hash=token_hash)
        return cls(pat_doc)

    @staticmethod
    def validate_scope_for_role(scope_set: list, user_role_key,
                                role_scope_map) -> bool:
        """
        Validate if all scopes in scope_set are allowed for the given user role.
        """
        allowed_scopes = role_scope_map.get(user_role_key, [])
        for scope in scope_set:
            if scope not in allowed_scopes:
                return False
        return True


# Alias for brevity
PAT = PersonalAccessToken
