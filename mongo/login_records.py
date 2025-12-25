"""
Login Records MongoDB operations.
Provides reusable database logic for querying login records.
"""

from typing import Optional, List, Dict, Any
from datetime import datetime
from flask import current_app

from . import engine
from .course import Course

__all__ = ['LoginRecords']


class LoginRecords:
    """Wrapper class for login records operations."""

    @staticmethod
    def get_all(
        offset: int = 0,
        limit: int = 50,
        username_filter: Optional[str] = None,
        success_filter: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Get all login records (Admin only).
        """
        try:
            current_app.logger.info("[LoginRecords] get_all called")

            # Build filter based on username if provided
            if username_filter:
                matching_users = list(
                    engine.User.objects(username__icontains=username_filter))
                if not matching_users:
                    return {'records': [], 'total': 0}
                user_ids = [u.id for u in matching_users]
            else:
                user_ids = None

            # Query login records
            if user_ids is not None:
                records_oid = list(
                    engine.LoginRecords.objects(user_id__in=user_ids))
            else:
                records_oid = list(engine.LoginRecords.objects.all())

            current_app.logger.info(
                f"[LoginRecords] Found {len(records_oid)} total records")

            if success_filter is not None:
                records_oid = [
                    r for r in records_oid if r.success == success_filter
                ]

            total = len(records_oid)

            # Build username lookup
            all_user_ids = {r.user_id for r in records_oid}
            user_id_to_username = {}
            for uid in all_user_ids:
                try:
                    user = engine.User.objects(pk=uid).first()
                    if user:
                        user_id_to_username[str(uid)] = user.username
                except Exception:
                    pass  # Skip invalid ObjectIds

            # Sort and apply pagination
            records_oid.sort(
                key=lambda r: getattr(r, 'timestamp', datetime.min),
                reverse=True)
            paginated = records_oid[offset:offset + limit]

            return {
                'records': [
                    LoginRecords._to_dict(
                        r, user_id_to_username.get(str(r.user_id)))
                    for r in paginated
                ],
                'total':
                total,
            }
        except Exception as e:
            current_app.logger.error(f"[LoginRecords] get_all error: {e}")
            return {'records': [], 'total': 0}

    @staticmethod
    def get_by_user(
        username: str,
        offset: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Get login records for a specific user.
        """
        try:
            user = engine.User.objects(username=username).first()
            if not user:
                return {'records': [], 'total': 0}

            # Query by user ObjectId
            user_id = user.id
            records = list(
                engine.LoginRecords.objects(
                    user_id=user_id).order_by('-timestamp'))

            total = len(records)
            paginated = records[offset:offset + limit]

            return {
                'records':
                [LoginRecords._to_dict(r, username) for r in paginated],
                'total': total,
            }
        except Exception as e:
            current_app.logger.error(f"[LoginRecords] get_by_user error: {e}")
            return {'records': [], 'total': 0}

    @staticmethod
    def get_by_course(
        course_name: str,
        offset: int = 0,
        limit: int = 50,
        username_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get login records for all members of a course.
        """
        try:
            course = Course(course_name)
            if not course or not course.obj:
                return {'records': [], 'total': 0, 'error': 'Course not found'}
        except engine.DoesNotExist:
            return {'records': [], 'total': 0, 'error': 'Course not found'}

        # Get member usernames from course
        member_usernames = set(course.student_nicknames.keys())

        if username_filter:
            member_usernames = {
                u
                for u in member_usernames
                if username_filter.lower() in u.lower()
            }

        if not member_usernames:
            return {'records': [], 'total': 0}

        # Get user documents and ObjectId mapping
        member_users_docs = list(
            engine.User.objects(username__in=list(member_usernames)))
        member_user_ids = {str(u.id): u.username for u in member_users_docs}
        member_ids = [u.id for u in member_users_docs]

        # Query by ObjectId
        login_records_oid = list(
            engine.LoginRecords.objects(user_id__in=member_ids))
        # Query by username
        login_records_name = list(
            engine.LoginRecords.objects(user_id__in=list(member_usernames)))

        # Deduplicate
        seen, login_records = set(), []
        for r in login_records_oid + login_records_name:
            rid = str(getattr(r, 'id', id(r)))
            if rid not in seen:
                seen.add(rid)
                login_records.append(r)

        # Sort by timestamp descending
        login_records.sort(key=lambda r: getattr(r, 'timestamp', datetime.min),
                           reverse=True)

        total = len(login_records)
        paginated = login_records[offset:offset + limit]

        def resolve_username(record):
            if hasattr(record.user_id, 'id'):
                uid_str = str(record.user_id.id)
                return member_user_ids.get(uid_str, 'N/A')
            else:
                uid_str = str(record.user_id)
                return uid_str if uid_str in member_usernames else member_user_ids.get(
                    uid_str, 'N/A')

        return {
            'records':
            [LoginRecords._to_dict(r, resolve_username(r)) for r in paginated],
            'total':
            total,
        }

    @staticmethod
    def get_all_for_csv() -> List[Dict[str, Any]]:
        """Get all login records for CSV export (Admin only)."""
        result = LoginRecords.get_all(offset=0, limit=100000)
        return result['records']

    @staticmethod
    def get_by_user_for_csv(username: str) -> List[Dict[str, Any]]:
        """Get all login records for a specific user for CSV export."""
        result = LoginRecords.get_by_user(username, offset=0, limit=100000)
        return result['records']

    @staticmethod
    def get_by_course_for_csv(course_name: str) -> List[Dict[str, Any]]:
        """Get all login records for a course for CSV export."""
        result = LoginRecords.get_by_course(course_name,
                                            offset=0,
                                            limit=100000)
        return result['records']

    @staticmethod
    def _to_dict(record, username: Optional[str] = None) -> Dict[str, Any]:
        """Convert a LoginRecords document to a dictionary."""
        if username is None:
            # Try to resolve username from user_id
            user_id = record.user_id
            try:
                user = engine.User.objects(pk=user_id).first()
                username = user.username if user else str(user_id)
            except Exception:
                username = str(user_id)

        return {
            'id': str(record.id),
            'username': username or 'N/A',
            'ipAddress': getattr(record, 'ip_addr', ''),
            'success': getattr(record, 'success', False),
            'timestamp': getattr(record, 'timestamp',
                                 datetime.now()).isoformat(),
        }
