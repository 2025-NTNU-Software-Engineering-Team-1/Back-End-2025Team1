from typing import Optional
import math
from flask import Blueprint, request, current_app

from mongo import *
from .auth import *
from .utils import *
from .utils.ai import DEFAULT_AI_MODEL
from mongo.utils import perm
from mongo.course import *
from mongo import engine
from datetime import datetime

__all__ = ['course_api']

course_api = Blueprint('course_api', __name__)


@course_api.get('/')
@login_required(pat_scope=['read:courses'])
def get_courses(user):
    data = [{
        'course': c.course_name,
        'teacher': c.teacher.info,
        'color': c.color,
        'emoji': c.emoji,
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
@Request.json('course', 'new_course', 'teacher', 'color', 'emoji')
@identity_verify(0, 1)
def modify_courses(user, course, new_course, teacher, color, emoji):
    r = None
    if user.role == Role.TEACHER:
        teacher = user.username
    try:
        if request.method == 'POST':
            r = Course.add_course(course, teacher, color=color, emoji=emoji)
        if request.method == 'PUT':
            co = Course(course)
            co.edit_course(user, new_course, teacher, color=color, emoji=emoji)
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


@course_api.route('/join', methods=['POST'])
@login_required
@Request.json('course_code')
def join_course_by_code(user, course_code):
    """
    Join a course by course code (as student).
    """
    if not course_code:
        return HTTPError('Course code is required.', 400)

    course = Course.get_by_code(course_code)
    if not course:
        return HTTPError('Invalid course code.', 404)

    try:
        course.join_by_code(user, code=course_code)
        return HTTPResponse('Successfully joined course.',
                            data={'course': course.course_name})
    except ValueError as e:
        return HTTPError(str(e), 400)
    except PermissionError as e:
        return HTTPError(str(e), 403)
    except engine.DoesNotExist as e:
        return HTTPError(f'{e} not found.', 404)


@course_api.route('/<course_name>', methods=['GET', 'PUT'])
@login_required(pat_scope=['read:courses'])
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
                [User(name).info for name in course.student_nicknames],
                "color": course.color,
                "emoji": course.emoji,
            },
        )
    else:
        return modify_course()


@course_api.route('/<course_name>/member/<username>/role', methods=['PUT'])
@login_required
@Request.json('role')
def change_member_role(user, course_name, username, role):
    """
    Change a member's role within the course.
    Only teachers and admins can do this (not TAs).
    
    Roles in course context:
    - 'student': Regular student
    - 'ta': Teaching Assistant
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    # Only course teacher or admin can change roles (not TAs)
    is_course_teacher = (course.obj.teacher.pk == user.pk)
    is_admin = (user.role == Role.ADMIN)
    is_global_teacher_ta = (user.role == Role.TEACHER and user.pk
                            in [ta.pk for ta in (course.tas or [])])

    if not (is_course_teacher or is_admin or is_global_teacher_ta):
        return HTTPError(
            'Only course teacher or admin can change member roles.', 403)

    target_user = User(username)
    if not target_user:
        return HTTPError('User not found.', 404)

    # Cannot change the course teacher's role
    if target_user.obj == course.teacher:
        return HTTPError('Cannot change course teacher role.', 400)

    # Validate role
    if role not in ['student', 'ta']:
        return HTTPError('Invalid role. Must be "student" or "ta".', 400)

    # Check if user is in the course
    is_ta = target_user.obj in course.tas
    is_student = username in course.student_nicknames

    if not (is_ta or is_student):
        return HTTPError('User is not a member of this course.', 400)

    # Perform role change
    if role == 'ta':
        if is_ta:
            return HTTPResponse('User is already a TA.')
        # Move from student to TA
        if is_student:
            del course.student_nicknames[username]
        if target_user.obj not in course.tas:
            course.tas.append(target_user.obj)
            course.add_user(target_user.obj)
    elif role == 'student':
        if is_student and not is_ta:
            return HTTPResponse('User is already a student.')
        # Move from TA to student
        if is_ta:
            course.tas = [ta for ta in course.tas if ta != target_user.obj]
        if username not in course.student_nicknames:
            course.student_nicknames[username] = username
            course.add_user(target_user.obj)

    course.save()
    return HTTPResponse('Role updated successfully.',
                        data={
                            'username': username,
                            'new_role': role
                        })


@course_api.route('/<course_name>/member/<username>', methods=['DELETE'])
@login_required
def remove_course_member(user, course_name, username):
    """
    Remove a member from the course.
    Only teachers and admins can do this (not TAs).
    Cannot remove the course teacher.
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    # Only course teacher or admin can remove members (not TAs)
    is_course_teacher = (course.obj.teacher.pk == user.pk)
    is_admin = (user.role == Role.ADMIN)

    if not (is_course_teacher or is_admin):
        return HTTPError('Only course teacher or admin can remove members.',
                         403)

    target_user = User(username)
    if not target_user:
        return HTTPError('User not found.', 404)

    # Cannot remove the course teacher
    if target_user.obj == course.teacher:
        return HTTPError('Cannot remove course teacher.', 400)

    # Check if user is in the course
    is_ta = target_user.obj in course.tas
    is_student = username in course.student_nicknames

    if not (is_ta or is_student):
        return HTTPError('User is not a member of this course.', 400)

    # Remove from TAs if applicable
    if is_ta:
        course.tas = [ta for ta in course.tas if ta != target_user.obj]

    # Remove from students if applicable
    if is_student:
        del course.student_nicknames[username]

    # Remove course from user's course list
    course.remove_user(target_user.obj)
    course.save()

    return HTTPResponse('Member removed successfully.',
                        data={'username': username})


@course_api.route('/<course_name>/members', methods=['POST'])
@login_required
@Request.json('usernames: list')
def add_existing_members(user, course_name, usernames):
    """
    Add existing users to the course as students.
    Only teachers and admins can do this.
    
    Request body: { "usernames": ["user1", "user2"] }
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    # Only course teacher or admin can add members
    is_course_teacher = (course.obj.teacher.pk == user.pk)
    is_admin = (user.role == Role.ADMIN)

    if not (is_course_teacher or is_admin):
        return HTTPError('Only course teacher or admin can add members.', 403)

    if not usernames or not isinstance(usernames, list):
        return HTTPError('usernames must be a non-empty list.', 400)

    added = []
    already_in = []
    not_found = []

    for username in usernames:
        target_user = User(username)
        if not target_user:
            not_found.append(username)
            continue

        # Check if user is already in the course
        is_ta = target_user.obj in course.tas
        is_student = username in course.student_nicknames
        is_teacher = target_user.obj == course.teacher

        if is_ta or is_student or is_teacher:
            already_in.append(username)
            continue

        # Add user as student
        course.student_nicknames[username] = username
        course.add_user(target_user.obj)
        added.append(username)

    if added:
        course.save()

    return HTTPResponse('Users processed.',
                        data={
                            'added': added,
                            'already_in': already_in,
                            'not_found': not_found
                        })


@course_api.route('/<course_name>/search-users', methods=['GET'])
@login_required
@Request.args('q')
def search_users_for_course(user, course_name, q):
    """
    Search for users that can be added to the course.
    Only teachers and admins can do this.
    
    Query params: ?q=searchterm
    Returns users matching the search term that are not already in the course.
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    # Only course teacher or admin can search
    is_course_teacher = (course.obj.teacher.pk == user.pk)
    is_admin = (user.role == Role.ADMIN)

    if not (is_course_teacher or is_admin):
        return HTTPError('Only course teacher or admin can search users.', 403)

    if not q or len(q) < 1:
        return HTTPResponse('No search query.', data=[])

    # Search users by username or email
    matching_users = User.search(q)

    # Get current course members
    member_usernames = course.get_member_usernames()

    # Filter out existing members
    results = []
    for u in matching_users:
        if u.username not in member_usernames:
            # role is already an int or Enum that behaves like int
            role_val = u.role
            if hasattr(role_val, 'value'):
                role_val = role_val.value

            results.append({
                'username':
                u.username,
                'displayedName':
                u.profile.displayed_name if u.profile else u.username,
                'role':
                role_val if role_val is not None else 2
            })

    return HTTPResponse('Success.', data=results)


@course_api.route('/<course_name>/code', methods=['GET', 'POST', 'DELETE'])
@login_required
def manage_course_code(user, course_name):
    """
    Manage course code for joining.
    GET: Get current course code
    POST: Generate a new course code
    DELETE: Remove course code (disable joining by code)
    """
    course = Course(course_name)

    if not course:
        return HTTPError('Course not found.', 404)

    if not course.permission(user, Course.Permission.GRADE):
        return HTTPError('Permission denied.', 403)

    if request.method == 'GET':
        auth_codes_info = [{
            'code':
            ac.code,
            'max_usage':
            ac.max_usage,
            'current_usage':
            ac.current_usage,
            'is_active':
            ac.is_active,
            'created_at':
            ac.created_at.isoformat() if ac.created_at else None
        } for ac in course.auth_codes]

        return HTTPResponse('Success.',
                            data={
                                'course_code': course.course_code,
                                'auth_codes': auth_codes_info
                            })

    elif request.method == 'POST':
        # Check if creating new global code or auth code
        data = request.json or {}
        if not isinstance(data, dict):
            return HTTPError('Invalid payload.', 400)
        max_usage = data.get('max_usage')

        if max_usage is not None:
            # Create new auth code
            try:
                max_usage = int(max_usage)
                if max_usage < 0: raise ValueError
            except ValueError:
                return HTTPError('Invalid max_usage.', 400)

            auth_code = course.add_auth_code(user, max_usage)
            return HTTPResponse('Authorization code generated.',
                                data={'code': auth_code.code})
        else:
            # Generate a new course code (Legacy)
            new_code = Course.generate_course_code()
            course.course_code = new_code
            course.save()
            return HTTPResponse('Course code generated.',
                                data={'course_code': new_code})

    elif request.method == 'DELETE':
        # Remove course code (Legacy)
        course.course_code = None
        course.save()
        return HTTPResponse('Course code removed.')


@course_api.route('/<course_name>/code/<code>', methods=['DELETE'])
@login_required
def delete_course_code(user, course_name, code):
    """
    Remove specific authorization code.
    """
    course = Course(course_name)
    if not course:
        return HTTPError('Course not found.', 404)

    if not course.permission(user, Course.Permission.MODIFY):
        return HTTPError('Permission denied.', 403)

    if course.remove_auth_code(code):
        return HTTPResponse('Authorization code removed.')
    else:
        return HTTPError('Authorization code not found.', 404)


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


@course_api.route('/<course_name>/ai/settings', methods=['GET'])
@login_required
def get_course_ai_settings(user, course_name):
    """
    Get Course AI Settings
    """
    course = Course(course_name)
    if not course:
        return HTTPError('Course not found', 404)

    if not course.permission(user, Course.Permission.VIEW):
        return HTTPError('You are not in this course.', 403)

    return HTTPResponse('Success', data=course.get_ai_settings())


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

    if not course.check_privilege(user):
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

    if not course.check_privilege(user):
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

    if not course.check_privilege(user):
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
