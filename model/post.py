from flask import Blueprint, request
from datetime import datetime
from mongo import *
from mongo import engine
from .auth import *
from .utils import *
from mongo.utils import *
from mongo.post import *
from mongo.course import *

__all__ = ['post_api']

post_api = Blueprint('post_api', __name__)


@post_api.route('/<course>', methods=['GET'])
@login_required
def get_post(user, course):
    target_course = Course(course)
    if not target_course:
        return HTTPError("Course not found.", 404)
    if not target_course.permission(user, Course.Permission.VIEW):
        return HTTPError('You are not in this course.', 403)
    data = Post.found_post(target_course)
    return HTTPResponse('Success.', data=data)


@post_api.route('/view/<course>/<target_thread_id>', methods=['GET'])
@login_required
def get_single_post(user, course, target_thread_id):
    target_course = Course(course)
    if not target_course:
        return HTTPError("Course not found.", 404)
    if not target_course.permission(user, Course.Permission.VIEW):
        return HTTPError('You are not in this course.', 403)
    data = Post.found_post(target_course, target_thread_id)
    return HTTPResponse('Success.', data=data)


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower_val = value.lower()
        if lower_val == 'true':
            return True
        if lower_val == 'false':
            return False
    return None


def _check_code_deadline(user, target_course, problem_id, contains_code):
    if not contains_code:
        return None

    capability = target_course.own_permission(user)
    if capability & (Course.Permission.GRADE | Course.Permission.MODIFY):
        return None

    if problem_id is None:
        return HTTPError('problemId is required when Contains_Code is true.',
                         400)

    if isinstance(problem_id, str) and problem_id.isdigit():
        problem_id = int(problem_id)

    problem = Problem(problem_id)
    if not problem:
        return HTTPError('Problem not found.', 404)

    deadline = getattr(problem.obj, 'deadline', None)
    if deadline is None and getattr(problem.obj, 'homeworks', None):
        for hw in problem.obj.homeworks:
            hw_end = getattr(getattr(hw, 'duration', None), 'end', None)
            if hw_end is not None:
                deadline = hw_end
                break

    if deadline is None:
        return None

    now = datetime.now(deadline.tzinfo) if getattr(deadline, 'tzinfo',
                                                   None) else datetime.now()
    if now < deadline:
        return HTTPError('Posting code is not allowed before deadline.', 403)
    return None


@post_api.route('/', methods=['POST', 'PUT', 'DELETE'])
@Request.json('course', 'title', 'content', 'target_thread_id',
              'contains_code: bool?', 'problem_id: int?')
@login_required
def modify_post(user, course, title, content, target_thread_id, contains_code,
                problem_id):
    if course == 'Public':
        return HTTPError('You can not add post in system.', 403)

    if course and target_thread_id:
        return HTTPError(
            'Request is fail,course or target_thread_id must be none.', 400)
    elif course:
        course_obj = Course(course)
        if not course_obj:
            return HTTPError('Course not exist.', 404)
        target_course = course_obj
    elif target_thread_id:
        try:
            target_thread = engine.PostThread.objects.get(id=target_thread_id)
        except engine.DoesNotExist:
            try:  # to protect input post id
                target_post = engine.Post.objects.get(id=target_thread_id)
            except engine.DoesNotExist:
                return HTTPError('Post/reply not exist.', 404)
            target_thread = target_post.thread
            target_thread_id = target_thread.id
        if target_thread.status:  # 1 is deleted
            return HTTPResponse('Forbidden,the post/reply is deleted.', 403)
        target_course = Course(target_thread.course_id)
    else:
        return HTTPError(
            'Request is fail,course and target_thread_id are both none.', 400)

    capability = target_course.own_permission(user)
    if capability == 0:
        return HTTPError('You are not in this course.', 403)

    contains_code = _normalize_bool(contains_code) or False
    if request.method == 'POST':
        if problem_id is None:
            data = request.get_json(silent=True) or {}
            problem_id = data.get('problemId') or data.get('Problem_id')
        err = _check_code_deadline(user, target_course, problem_id,
                                   contains_code)
        if err is not None:
            return err
    if request.method == 'POST':
        # add reply
        if course:
            r = Post.add_post(course, user, content, title)
        # add course post
        elif target_thread_id:
            r = Post.add_reply(target_thread, user, content)
    if request.method == 'PUT':
        if course:
            return HTTPError(
                "Request is fail,you should provide target_thread_id replace course.",
                400)
        r = Post.edit_post(target_thread, user, content, title, capability)
    if request.method == 'DELETE':
        if course:
            return HTTPError(
                "Request is fail,you should provide target_thread_id replace course.",
                400)
        r = Post.delete_post(target_thread, user, capability)
    if r is not None:
        return HTTPError(r, 403)
    return HTTPResponse('success.')


@post_api.route('/status/<post_id>', methods=['PUT'])
@Request.json('action: str')
@login_required
def update_post_status(user, post_id, action):
    try:
        target_thread = engine.PostThread.objects.get(id=post_id)
    except engine.DoesNotExist:
        try:
            target_post = engine.Post.objects.get(id=post_id)
        except engine.DoesNotExist:
            return HTTPError('Post/reply not exist.', 404)
        target_thread = target_post.thread

    target_course = Course(target_thread.course_id)
    capability = target_course.own_permission(user)
    if capability == 0:
        return HTTPError('You are not in this course.', 403)

    author = getattr(target_thread, 'author', None)
    is_author = bool(author
                     and getattr(author, 'username', None) == user.username)
    can_manage = is_author or bool(capability
                                   & Course.Permission.GRADE) or bool(
                                       capability & Course.Permission.MODIFY)
    if not can_manage:
        return HTTPError('Forbidden, you don\'t have enough permission.', 403)

    action = action.upper()
    if action not in ('PIN', 'UNPIN', 'SOLVE', 'CLOSE', 'DELETE'):
        return HTTPError('Invalid action.', 400)

    if action == 'DELETE':
        r = Post.delete_post(target_thread, user, capability)
        if r is not None:
            return HTTPError(r, 403)
        return HTTPResponse('success.')

    if action == 'PIN':
        target_thread.pinned = True
    elif action == 'UNPIN':
        target_thread.pinned = False
    elif action == 'SOLVE':
        target_thread.solved = True
    elif action == 'CLOSE':
        target_thread.closed = True

    target_thread.updated = datetime.now()
    target_thread.save()
    return HTTPResponse('success.')
