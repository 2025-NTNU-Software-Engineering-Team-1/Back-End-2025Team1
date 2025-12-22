from flask import Blueprint, request
from .auth import login_required
from .utils import HTTPError, HTTPResponse, Request
from mongo.post import Post

# 必須加上這行，否則 model/__init__.py 會報錯
__all__ = ['post_api']

post_api = Blueprint('post_api', __name__)


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
    return HTTPResponse('success.')


@post_api.route('/status/<post_id>', methods=['PUT'])
@Request.json('action: str')
@login_required
def update_post_status(user, post_id, action):
    err_msg, code = Post.update_status_logic(user, post_id, action)
    if err_msg:
        return HTTPError(err_msg, code)
    return HTTPResponse('success.')
