from flask import Blueprint, request
from .auth import login_required
from .utils import HTTPError, HTTPResponse, Request
from mongo import engine
from mongo.post import Post

__all__ = ['post_api']

post_api = Blueprint('post_api', __name__)


def _get_problem_title(problem_id):
    """取得題目名稱的輔助函式"""
    if not problem_id:
        return ''
    try:
        # 支援整數 ID 或字串 ID
        pid = int(problem_id) if str(problem_id).isdigit() else problem_id
        problem = (engine.Problem.objects(problem_id=pid).first()
                   or engine.Problem.objects(pk=pid).first())
        if not problem:
            return ''
        return (getattr(problem, 'problem_name', None)
                or getattr(problem, 'title', None) or '')
    except Exception:
        return ''


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
# 移除冒號型別宣告，改為純參數名稱，避免 Request 類別解析錯誤
@Request.json('course', 'title', 'content', 'target_thread_id',
              'contains_code', 'problem_id')
@login_required
def modify_post(user, course, title, content, target_thread_id, contains_code,
                problem_id):
    if course == 'Public':
        return HTTPError('You can not add post in system.', 403)

    # 在內部手動處理布林值與整數轉換，確保安全性
    # 這樣即便前端沒傳，也會拿到 None 而不會觸發 400 錯誤
    try:
        actual_contains_code = bool(
            contains_code) if contains_code is not None else False
        actual_problem_id = int(problem_id) if (
            problem_id is not None and str(problem_id).isdigit()) else None
    except (ValueError, TypeError):
        return HTTPError('Invalid type for problem_id or contains_code', 400)

    err_msg, code = Post.modify_post_logic(request.method, user, course,
                                           target_thread_id, title, content,
                                           actual_contains_code,
                                           actual_problem_id)

    if err_msg:
        return HTTPError(err_msg, code)

    if request.method == 'POST':
        problem_title = _get_problem_title(actual_problem_id)
        return HTTPResponse('success.',
                            data={
                                'Problem_Name': problem_title,
                                'problemName': problem_title,
                                'problem_name': problem_title,
                            })
    return HTTPResponse('success.')


@post_api.route('/status/<post_id>', methods=['PUT'])
@Request.json('action')  # 移除 action: str 語法
@login_required
def update_post_status(user, post_id, action):
    # 確保 action 存在且為字串
    if not action:
        return HTTPError('Missing action', 400)

    err_msg, code = Post.update_status_logic(user, post_id, str(action))
    if err_msg:
        return HTTPError(err_msg, code)
    return HTTPResponse('success.')
