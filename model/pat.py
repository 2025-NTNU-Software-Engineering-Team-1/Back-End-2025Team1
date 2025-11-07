from flask import Blueprint, Response
import csv
import io

from mongo import *
from .auth import *
from .utils import *
from mongo.utils import *
from mongo.course import *
from mongo import engine

__all__ = ['pat_api']

pat_api = Blueprint('pat_api', __name__)


@pat_api.get('/ping')
def api_ping():
    return {'ok': True}


@pat_api.route('/userips/<course_name>', methods=['GET'])
@pat_required('read:userips')
def get_course_user_ips(user, course_name: str):
    """
        Get all login and submission IP records of students in a course.
        Return a CSV file with the records.
    """
    try:
        course = Course(course_name)
        if not course or not course.obj:
            return HTTPError('Course not found.', 404)
    except engine.DoesNotExist:
        return HTTPError('Course not found.', 404)

    member_usernames = set(course.student_nicknames.keys())

    member_users_docs = engine.User.objects(
        username__in=list(member_usernames))
    member_user_ids = {str(u.id): u.username for u in member_users_docs}
    member_ids = [u.id for u in member_users_docs]

    login_records_oid = list(
        engine.LoginRecords.objects(user_id__in=member_ids))
    login_records_name = list(
        engine.LoginRecords.objects(user_id__in=list(member_usernames)))
    seen, login_records = set(), []
    for r in login_records_oid + login_records_name:
        rid = str(getattr(r, 'id', id(r)))
        if rid not in seen:
            seen.add(rid)
            login_records.append(r)

    submission_records = engine.Submission.objects(
        user__in=list(member_users_docs))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'Type', 'Username', 'Timestamp', 'IP Address', 'Success', 'Problem ID'
    ])

    for record in login_records:
        if hasattr(record.user_id, 'id'):
            uid_str = str(record.user_id.id)
            username = member_user_ids.get(uid_str, 'N/A')
        else:
            uid_str = str(record.user_id)
            username = uid_str if uid_str in member_usernames else 'N/A'
        writer.writerow([
            'Login', username,
            getattr(record, 'timestamp', ''),
            getattr(record, 'ip_addr', ''),
            getattr(record, 'success', ''), ''
        ])

    for record in submission_records:
        uname = record.user.username
        if not uname or uname not in member_usernames:
            continue

        writer.writerow([
            'Submission',
            uname,
            getattr(record, 'timestamp', ''),
            getattr(record, 'ip_addr', ''),
            '',
            str(getattr(record, 'problem_id', getattr(record, 'problem', ''))),
        ])

    csv_content = output.getvalue()
    filename = f"{course.course_name}_ip_records.csv"
    return Response(
        csv_content,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"})
