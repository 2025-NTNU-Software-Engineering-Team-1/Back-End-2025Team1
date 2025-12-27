"""
Login Records API routes.
Provides endpoints for viewing login records for Admin, Teacher/TA, and users.
"""

import csv
import io
from flask import Blueprint, Response

from mongo import *
from mongo.user import Role
from .auth import login_required, identity_verify
from .utils import *
from mongo.course import Course

__all__ = ['login_records_api']

login_records_api = Blueprint('login_records_api', __name__)

# =========================== Admin Routes ===========================


@login_records_api.route('/login-records', methods=['GET'])
@login_required
@Request.args('offset', 'limit', 'username', 'success')
def get_all_login_records(
    user,
    offset: str = None,
    limit: str = None,
    username: str = None,
    success: str = None,
):
    """
    Get all login records (Admin only).
    Query params:
        - offset: Number of records to skip (default: 0)
        - limit: Maximum number of records (default: 50)
        - username: Filter by username (partial match)
        - success: Filter by success status (true/false)
    """
    if user.role != Role.ADMIN:
        return HTTPError('Permission denied. Admin only.', 403)

    try:
        offset_int = int(offset) if offset else 0
        limit_int = int(limit) if limit else 50
    except (TypeError, ValueError):
        return HTTPError('offset and limit must be integers', 400)

    success_filter = None
    if success is not None:
        success_filter = success.lower() == 'true'

    result = LoginRecords.get_all(
        offset=offset_int,
        limit=limit_int,
        username_filter=username,
        success_filter=success_filter,
    )

    return HTTPResponse('Success', data=result)


@login_records_api.route('/login-records/download', methods=['GET'])
@login_required
def download_all_login_records(user):
    """Download all login records as CSV (Admin only)."""
    if user.role != Role.ADMIN:
        return HTTPError('Permission denied. Admin only.', 403)

    records = LoginRecords.get_all_for_csv()
    return _generate_csv_response(records, 'all_login_records.csv')


# =========================== User Routes ===========================


@login_records_api.route('/login-records/me', methods=['GET'])
@login_required
@Request.args('offset', 'limit')
def get_my_login_records(
    user,
    offset: str = None,
    limit: str = None,
):
    """
    Get current user's login records.
    Query params:
        - offset: Number of records to skip (default: 0)
        - limit: Maximum number of records (default: 50)
    """
    try:
        offset_int = int(offset) if offset else 0
        limit_int = int(limit) if limit else 50
    except (TypeError, ValueError):
        return HTTPError('offset and limit must be integers', 400)

    result = LoginRecords.get_by_user(
        username=user.username,
        offset=offset_int,
        limit=limit_int,
    )

    return HTTPResponse('Success', data=result)


@login_records_api.route('/login-records/me/download', methods=['GET'])
@login_required
def download_my_login_records(user):
    """Download current user's login records as CSV."""
    records = LoginRecords.get_by_user_for_csv(user.username)
    return _generate_csv_response(records,
                                  f'{user.username}_login_records.csv')


# =========================== Course Routes ===========================


@login_records_api.route('/course/<course_name>/login-records',
                         methods=['GET'])
@login_required
@Request.args('offset', 'limit', 'username')
def get_course_login_records(
    user,
    course_name: str,
    offset: str = None,
    limit: str = None,
    username: str = None,
):
    """
    Get login records for members of a course (Teacher/TA only).
    Query params:
        - offset: Number of records to skip (default: 0)
        - limit: Maximum number of records (default: 50)
        - username: Filter by username (partial match)
    """
    # Check permission: Admin, Teacher, or TA of this course
    try:
        course = Course(course_name)
        if not course or not course.obj:
            return HTTPError('Course not found.', 404)
    except engine.DoesNotExist:
        return HTTPError('Course not found.', 404)

    if not course.check_privilege(user):
        return HTTPError('Permission denied. Teacher or TA only.', 403)

    try:
        offset_int = int(offset) if offset else 0
        limit_int = int(limit) if limit else 50
    except (TypeError, ValueError):
        return HTTPError('offset and limit must be integers', 400)

    result = LoginRecords.get_by_course(
        course_name=course_name,
        offset=offset_int,
        limit=limit_int,
        username_filter=username,
    )

    if 'error' in result:
        return HTTPError(result['error'], 404)

    return HTTPResponse('Success', data=result)


@login_records_api.route('/course/<course_name>/login-records/download',
                         methods=['GET'])
@login_required
def download_course_login_records(user, course_name: str):
    """Download login records for members of a course as CSV (Teacher/TA only)."""
    # Check permission: Admin, Teacher, or TA of this course
    try:
        course = Course(course_name)
        if not course or not course.obj:
            return HTTPError('Course not found.', 404)
    except engine.DoesNotExist:
        return HTTPError('Course not found.', 404)

    if not course.check_privilege(user):
        return HTTPError('Permission denied. Teacher or TA only.', 403)

    records = LoginRecords.get_by_course_for_csv(course_name)
    return _generate_csv_response(records, f'{course_name}_login_records.csv')


# =========================== Helper Functions ===========================


def _generate_csv_response(records, filename):
    """Generate a CSV response from a list of record dictionaries."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Username', 'IP Address', 'Success', 'Timestamp'])

    for record in records:
        writer.writerow([
            record.get('username', ''),
            record.get('ipAddress', ''),
            'Yes' if record.get('success', False) else 'No',
            record.get('timestamp', ''),
        ])

    csv_content = output.getvalue()
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={'Content-disposition': f'attachment; filename={filename}'})
