from flask import Blueprint, request
from .auth import login_required
from .utils import HTTPError, HTTPResponse, Request
from mongo import engine
from mongo.post import Post

__all__ = ['post_api']

post_api = Blueprint('post_api', __name__)


def _get_problem_title(problem_id):
    if not problem_id:
        return ''
    pid = int(problem_id) if str(problem_id).isdigit() else problem_id
    problem = (engine.Problem.objects(problem_id=pid).first()
               or engine.Problem.objects(pk=pid).first())
    if not problem:
        return ''
    return (getattr(problem, 'problem_name', None)
            or getattr(problem, 'title', None) or '')


@post_api.route('/<course>', methods=['GET'])
@login_required
def get_post(user, course):
    data, err_msg, code = Post.get_post_data(user, course)
    if err_msg:
        return HTTPError(err_msg, code)
    return HTTPResponse('Success.', data=data)


@post_api.route('/view/<course>/<target_thread_id>', methods=['GET'])
@login_required
def get_single_post(user, course, target_thread_id):
    data, err_msg, code = Post.get_post_data(user, course, target_thread_id)
    if err_msg:
        return HTTPError(err_msg, code)
    return HTTPResponse('Success.', data=data)


@post_api.route('/', methods=['POST', 'PUT', 'DELETE'])
@Request.json('course: str?', 'title: str?', 'content: str?',
              'target_thread_id: str?', 'contains_code: bool?',
              'problem_id: int?')
@login_required
def modify_post(user, course, title, content, target_thread_id, contains_code,
                problem_id):
    if course == 'Public':
        return HTTPError('You can not add post in system.', 403)

    err_msg, code = Post.modify_post_logic(request.method, user, course,
                                           target_thread_id, title, content,
                                           contains_code, problem_id)

    if err_msg:
        return HTTPError(err_msg, code)
    if request.method == 'POST':
        problem_title = _get_problem_title(problem_id)
        return HTTPResponse('success.',
                            data={
                                'Problem_Name': problem_title,
                                'problemName': problem_title,
                                'problem_name': problem_title,
                            })
    return HTTPResponse('success.')


@post_api.route('/status/<post_id>', methods=['PUT'])
@Request.json('action: str')
@login_required
def update_post_status(user, post_id, action):
    err_msg, code = Post.update_status_logic(user, post_id, action)
    if err_msg:
        return HTTPError(err_msg, code)
    return HTTPResponse('success.')
