from typing import Optional
import math
from flask import Blueprint, request, current_app

from mongo import *
from .auth import *
from .utils import *
from .utils.ai import DEFAULT_AI_MODEL
from mongo.utils import *
from mongo.course import *
from mongo import engine
from datetime import datetime

__all__ = ['course_api']

course_api = Blueprint('course_api', __name__)


def teacher_permission_check(user, course: Course) -> bool:
    is_course_teacher = (course.obj.teacher.pk == user.pk)
    is_ta = any(ta.pk == user.pk for ta in course.obj.tas)
    is_staff = (user.role == Role.ADMIN)
    return is_course_teacher or is_staff or is_ta


@course_api.get('/')
@login_required
def get_courses(user):
    data = [{
        'course': c.course_name,
        'teacher': c.teacher.info,
    } for c in Course.get_user_courses(user)]
    return HTTPResponse('Success.', data=data)


@course_api.get('/summary')
@identity_verify(0)
def get_courses_summary(user):
    courses = [Course(c) for c in Course.get_all()]
    summary = {"courseCount": len(courses), "breakdown": []}

    for course in courses:
        # The user is admin, it won't filter out any problems (it's required)
        problems = Problem.get_problem_list(user, course=course.course_name)
        course_summary = course.get_course_summary(problems)
        course_summary["problemCount"] = len(problems)
        summary["breakdown"].append(course_summary)

    return HTTPResponse("Success.", data=summary)


@course_api.route('/', methods=['POST', 'PUT', 'DELETE'])
@Request.json('course', 'new_course', 'teacher')
@identity_verify(0, 1)
def modify_courses(user, course, new_course, teacher):
    r = None
    if user.role == 1:
        teacher = user.username
    try:
        if request.method == 'POST':
            r = Course.add_course(course, teacher)
        if request.method == 'PUT':
            co = Course(course)
            co.edit_course(user, new_course, teacher)
        if request.method == 'DELETE':
            co = Course(course)
            co.delete_course(user)
    except ValueError:
        return HTTPError('Not allowed name.', 400)
    except NotUniqueError:
        return HTTPError('Course exists.', 400)
    except PermissionError:
        return HTTPError('Forbidden.', 403)
    except engine.DoesNotExist as e:
        return HTTPError(f'{e} not found.', 404)
    return HTTPResponse('Success.')


@course_api.route('/<course_name>', methods=['GET', 'PUT'])
@login_required
def get_course(user, course_name):
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    if not course.permission(user, Course.Permission.VIEW):
        return HTTPError('You are not in this course.', 403)

    @Request.json('TAs', 'student_nicknames')
    def modify_course(TAs, student_nicknames):
        if not course.permission(user, Course.Permission.MODIFY):
            return HTTPError('Forbidden.', 403)
        else:
            tas = []
            for ta in TAs:
                permit_user = User(ta).obj
                if not User(ta):
                    return HTTPResponse(f'User: {ta} not found.', 404)
                tas.append(permit_user)

            for permit_user in set(course.tas) - set(tas):
                course.remove_user(permit_user)
            for permit_user in set(tas) - set(course.tas):
                course.add_user(permit_user)
            course.tas = tas

        try:
            course.update_student_namelist(student_nicknames)
        except engine.DoesNotExist as e:
            return HTTPError(str(e), 404)
        return HTTPResponse('Success.')

    if request.method == 'GET':
        return HTTPResponse(
            'Success.',
            data={
                "teacher": course.teacher.info,
                "TAs": [ta.info for ta in course.tas],
                "students":
                [User(name).info for name in course.student_nicknames]
            },
        )
    else:
        return modify_course()


@course_api.route('/<course_name>/grade/<student>',
                  methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def grading(user, course_name, student):
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)
    if not course.permission(user, Course.Permission.VIEW):
        return HTTPError('You are not in this course.', 403)
    if student not in course.student_nicknames.keys():
        return HTTPError('The student is not in the course.', 404)
    if course.permission(user, Course.Permission.SCORE) and \
        (user.username != student or request.method != 'GET'):
        return HTTPError('You can only view your score.', 403)

    def get_score():
        return HTTPResponse(
            'Success.',
            data=[{
                'title': score['title'],
                'content': score['content'],
                'score': score['score'],
                'timestamp': score['timestamp'].timestamp()
            } for score in course.student_scores.get(student, [])])

    @Request.json('title', 'content', 'score')
    def add_score(title, content, score):
        score_list = course.student_scores.get(student, [])
        if title in [score['title'] for score in score_list]:
            return HTTPError('This title is taken.', 400)
        score_list.append({
            'title': title,
            'content': content,
            'score': score,
            'timestamp': datetime.now()
        })
        course.student_scores[student] = score_list
        course.save()
        return HTTPResponse('Success.')

    @Request.json('title', 'new_title', 'content', 'score')
    def modify_score(title, new_title, content, score):
        score_list = course.student_scores.get(student, [])
        title_list = [score['title'] for score in score_list]
        if title not in title_list:
            return HTTPError('Score not found.', 404)
        index = title_list.index(title)
        if new_title is not None:
            if new_title in title_list:
                return HTTPError('This title is taken.', 400)
            title = new_title
        score_list[index] = {
            'title': title,
            'content': content,
            'score': score,
            'timestamp': datetime.now()
        }
        course.student_scores[student] = score_list
        course.save()
        return HTTPResponse('Success.')

    @Request.json('title')
    def delete_score(title):
        score_list = course.student_scores.get(student, [])
        title_list = [score['title'] for score in score_list]
        if title not in title_list:
            return HTTPError('Score not found.', 404)
        index = title_list.index(title)
        del score_list[index]
        course.student_scores[student] = score_list
        course.save()
        return HTTPResponse('Success.')

    methods = {
        'GET': get_score,
        'POST': add_score,
        'PUT': modify_score,
        'DELETE': delete_score
    }
    return methods[request.method]()


@course_api.route('/<course_name>/scoreboard', methods=['GET'])
@login_required
@Request.args('pids: str', 'start', 'end')
@Request.doc('course_name', 'course', Course)
def get_course_scoreboard(
    user,
    pids: str,
    start: Optional[str],
    end: Optional[str],
    course: Course,
):
    try:
        pids = pids.split(',')
        pids = [int(pid.strip()) for pid in pids]
    except:
        return HTTPError('Error occurred when parsing `pids`.', 400)

    if start:
        try:
            start = float(start)
        except:
            return HTTPError('Type of `start` should be float.', 400)
    if end:
        try:
            end = float(end)
        except:
            return HTTPError('Type of `end` should be float.', 400)

    if not course.permission(user, Course.Permission.GRADE):
        return HTTPError('Permission denied', 403)

    ret = course.get_scoreboard(pids, start, end)

    return HTTPResponse(
        'Success.',
        data=ret,
    )


# =========================================
# AI API Sections
# =========================================


@course_api.route('/<course_name>/aisetting/usage', methods=['GET'])
@identity_verify(Role.TEACHER, Role.ADMIN, Role.TA)
def get_course_ai_usage(user, course_name):
    """
    [Teacher Only] 取得 AI Key 使用量統計頁面資料
    """
    course = Course(course_name)
    if not course:
        return HTTPError('Course not found', 404)

    if not teacher_permission_check(user, course):
        return HTTPError('Permission denied', 403)

    try:
        keys_data = AiApiKey.get_keys_usage_by_course(course_name)
        return HTTPResponse('Get usage success', data={'keys': keys_data})

    except Exception as e:
        current_app.logger.error(f"Error getting AI key usage: {str(e)}")
        return HTTPError(str(e), 500)


@course_api.route('/<course_name>/ai/key', methods=['GET', 'POST'])
@identity_verify(Role.TEACHER, Role.ADMIN, Role.TA)
def course_ai_key_entry(user, course_name):
    """
    GET:    Get AI API Keys for a course.
    POST:   New AI API Key
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found', 404)

    if not teacher_permission_check(user, course):
        return HTTPError('Permission denied', 403)

    # ===== GET: Retrieve Keys =====
    def get_keys():
        try:
            keys = AiApiKey.get_list_by_course(course_name)
            return HTTPResponse('Success', data={'keys': keys})
        except Exception as e:
            return HTTPError(str(e), 500)

    # ===== POST: Add New Key =====
    @Request.json('key_name', 'value', 'is_active')
    def add_key(key_name, value, is_active):
        if not key_name or not value:
            return HTTPError('Missing key_name or value', 400)

        if is_active is None:
            is_active = True

        try:
            new_key = AiApiKey.add_key(course_id=course.id,
                                       key_name=key_name,
                                       key_value=value,
                                       created_by=user,
                                       is_active=is_active)

            return HTTPResponse('Key added.', data={'id': str(new_key.id)})
        except ValueError as ve:
            current_app.logger.error(f"ValueError adding AI key: {str(ve)}")
            return HTTPError(str(ve), 400)
        except NotUniqueError:
            return HTTPError('Key name already exists', 400)
        except Exception as e:
            current_app.logger.error(f"Error adding AI key: {str(e)}")
            return HTTPError(str(e), 500)

    # ===== Method Dispatch =====
    if request.method == 'GET':
        return get_keys()
    elif request.method == 'POST':
        return add_key()


@course_api.route('/<course_name>/ai/key/<key_id>',
                  methods=['DELETE', 'PATCH'])
@identity_verify(Role.TEACHER, Role.ADMIN, Role.TA)
def manage_course_ai_key(user, course_name, key_id):
    """
    Delete or Update an AI API Key for a course.
    """
    # Common Checks
    course = Course(course_name)
    if not course:
        return HTTPError('Course not found', 404)

    if user.role != Role.ADMIN:
        is_teacher = (course.obj.teacher.pk == user.pk)
        is_ta = any(ta.pk == user.pk for ta in course.obj.tas)

        if not (is_teacher or is_ta):
            return HTTPError('Permission denied', 403)

    key = AiApiKey.get_key_by_id(key_id)
    if not key:
        return HTTPError('Key not found', 404)

    if key.course_name != course.obj:
        return HTTPError('Key does not belong to this course', 403)

    # DELETE
    def delete_key():
        try:
            success = AiApiKey.delete_key(key_id)
            if success:
                return HTTPResponse('Key deleted.')
            else:
                return HTTPError('Delete failed', 500)
        except Exception as e:
            current_app.logger.error(f"Error deleting key: {str(e)}")
            return HTTPError(str(e), 500)

    # PATCH
    @Request.json('key_name', 'is_active')
    def update_key(key_name, is_active):
        update_fields = {}

        # Only update fields that are provided
        if key_name is not None:
            update_fields['key_name'] = key_name
        if is_active is not None:
            update_fields['is_active'] = is_active

        if not update_fields:
            return HTTPError('No valid fields to update', 400)

        try:
            success = AiApiKey.update_key(key_id, **update_fields)
            if success:
                return HTTPResponse('Key updated.')
            else:
                return HTTPError('Update failed', 500)
        except Exception as e:
            current_app.logger.error(f"Error updating key: {str(e)}")
            if 'duplicate key error' in str(e) or 'NotUniqueError' in str(
                    type(e)):
                return HTTPError('Key name already exists', 400)
            return HTTPError(str(e), 500)

    if request.method == 'DELETE':
        return delete_key()
    elif request.method == 'PATCH':
        return update_key()


@course_api.route('/<course_name>/ai/key/suggestion', methods=['GET'])
@identity_verify(Role.TEACHER, Role.ADMIN, Role.TA)
@Request.args('model')
def get_key_suggestion(user, course_name, model):
    """
    Teacher Only
    Get suggested number of AI API Keys based on student count and model limits.
    """
    # Scenario:
    # A course has 120 students, using "gemini-2.5-flash-lite" model with 15 RPM limit.
    # Calculation:
    # - Effective RPM (with safety buffer): 15 * 0.5 = 7.5 RPM
    # - Suggested Keys = ceil(120 / 7.5) = 16 keys
    course = Course(course_name)
    if not course:
        return HTTPError('Course not found', 404)

    if not teacher_permission_check(user, course):
        return HTTPError('Permission denied', 403)

    # 1. Model Name
    model_name = model if model else DEFAULT_AI_MODEL

    # 2. Student Count
    student_count = len(
        course.student_nicknames) if course.student_nicknames else 0
    # If no students, suggest 1 key by default
    if student_count == 0:
        return HTTPResponse('Success',
                            data={
                                "student_count": 0,
                                "suggested_key_count": 1,
                                "reason": "No students in course yet."
                            })

    # 3. Get Model RPM Limit
    rpm_limit = AiModel.get_rpm_limit(model_name, default=5)

    # 4. Calculate Suggested Key Count
    safety_factor = 0.5  # 50% of RPM to be safe
    effective_rpm = max(1, rpm_limit * safety_factor)

    suggested_count = math.ceil(student_count / effective_rpm)
    suggested_count = max(1, suggested_count)

    return HTTPResponse(
        'Success',
        data={
            "student_count":
            student_count,
            "suggested_key_count":
            suggested_count,
            "reason":
            f"Based on {student_count} students and {rpm_limit} RPM limit (with safety buffer of {safety_factor})."
        })
