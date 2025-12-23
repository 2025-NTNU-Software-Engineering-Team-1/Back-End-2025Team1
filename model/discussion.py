from flask import Blueprint, request, current_app

from .auth import login_required
from .utils.response import HTTPError, HTTPResponse
from mongo.discussion import Discussion

__all__ = ['discussion_api']

discussion_api = Blueprint('discussion_api', __name__)

_DEFAULT_LIMIT = 20
_MIN_LIMIT = 1
_MAX_LIMIT = 50
_MAX_PAGE = 1000
_ERR_INVALID_PARAM = 'Invalid parameter.'


def _clamp_int(raw_value, default, min_v, max_v, name):
    if raw_value in (None, ''):
        return default
    try:
        val = int(raw_value)
    except ValueError:
        raise ValueError(f'{name} must be an integer.')
    return max(min_v, min(val, max_v))


def _err(msg, code=400):
    # 統一錯誤回傳格式，滿足測試 assert resp['data']['Status'] == 'ERR'
    return HTTPError(msg, code, data={'Status': 'ERR'})


def format_discussion_post(post):
    author_display = ""
    if getattr(post, 'author', None):
        info = getattr(post.author, 'info', {})

        if isinstance(info, dict):
            author_display = info.get('displayedName', '').strip()

        if not author_display:
            author_display = getattr(post.author, 'username', '')

    problem_id = ''
    try:
        raw = post.to_mongo().to_dict()
        problem_id = raw.get('problemId', post.problem_id)
    except Exception:
        problem_id = getattr(post, 'problem_id', '')

    if problem_id is None:
        problem_id = ''

    created_time = post.created_time.isoformat()
    like_count = post.like_count or 0
    reply_count = post.reply_count or 0

    return {
        'Post_Id': post.post_id,
        'Author': author_display,
        'Title': post.title,
        'Created_Time': created_time,
        'Like_Count': like_count,
        'Reply_Count': reply_count,
        'Is_Pinned': bool(post.is_pinned),
        'Is_Solved': bool(post.is_solved),
        'Is_Closed': bool(post.is_closed),
        'Problem_Id': problem_id,
    }


@discussion_api.route('/posts', methods=['GET'])
@login_required
def list_discussion_posts(user):
    try:
        limit = _clamp_int(request.args.get('Limit'), _DEFAULT_LIMIT,
                           _MIN_LIMIT, _MAX_LIMIT, 'Limit')
        page = _clamp_int(request.args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    except ValueError as exc:
        return _err(str(exc), 400)

    raw_problem_id = request.args.get('Problem_Id')
    problem_id = ''
    if raw_problem_id is not None:
        problem_id = str(raw_problem_id).strip()
    mode = (request.args.get('Mode') or 'New').strip()

    if mode not in ('New', 'Hot'):
        return _err('Invalid Mode. Available values: New, Hot.', 400)

    try:
        data = Discussion.get_feed(user, mode, limit, page, problem_id)
    except ValueError:
        return _err(_ERR_INVALID_PARAM, 400)

    raw_posts = data.get('Posts', [])
    data['Posts'] = [format_discussion_post(post) for post in raw_posts]

    resp_data = {
        'Status': 'OK',
        'Mode': mode,
        'Limit': limit,
        'Page': page,
        **data,
    }
    # 如果 Request 有 Problem_Id，Response 也要帶回去 (滿足測試)
    if problem_id:
        resp_data['Problem_Id'] = problem_id
        resp_data['problemId'] = problem_id
        resp_data['problem_id'] = problem_id

    return HTTPResponse('Success.', data=resp_data)


@discussion_api.route('/problems', methods=['GET'])
@login_required
def list_discussion_problems(user):
    try:
        limit = _clamp_int(request.args.get('Limit'), _DEFAULT_LIMIT,
                           _MIN_LIMIT, _MAX_LIMIT, 'Limit')
        page = _clamp_int(request.args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    except ValueError as e:
        return _err(str(e), 400)

    # 需求: Mode is String. 測試預期 Invalid Mode -> 400
    mode = (request.args.get('Mode') or 'All').strip()
    if mode.lower() != 'all':
        return _err('Invalid Mode. Available values: All.', 400)

    # 統一轉成 'All' 傳入 Model
    mode = 'All'

    try:
        data = Discussion.get_problems(user, mode, limit, page)
    except Exception:
        current_app.logger.exception('Failed to fetch discussion problems')
        return _err('Failed to fetch problems.', 500)

    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Mode': mode,
                            'Limit': limit,
                            'Page': page,
                            **data,
                        })


@discussion_api.route('/problems/<problem_id>/meta', methods=['GET'])
@login_required
def get_discussion_problem_meta(user, problem_id):
    data, err = Discussion.get_problem_meta(user, problem_id)
    if err:
        return _err(err, 404)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/search', methods=['GET'])
@login_required
def search_discussion_posts(user):
    # 測試預期 Missing Words -> 400
    words_raw = request.args.get('Words')
    if words_raw is None:
        return _err('Words parameter is required.', 400)

    words = words_raw.strip()
    if not words:
        return HTTPResponse('Success.', data={'Status': 'OK', 'Post': []})

    try:
        limit = _clamp_int(request.args.get('Limit'), _DEFAULT_LIMIT,
                           _MIN_LIMIT, _MAX_LIMIT, 'Limit')
        page = _clamp_int(request.args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    except ValueError as e:
        return _err(str(e), 400)

    posts = Discussion.search_posts(user, words, limit, page)
    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': posts})


@discussion_api.route('/post', methods=['POST'])
@login_required
def create_discussion_post(user):
    payload = request.get_json(silent=True) or {}
    title = (payload.get('Title') or '').strip()
    content = (payload.get('Content') or '').strip()
    problem_id = str(payload.get('Problem_id') or '').strip()

    if not all([title, content, problem_id]):
        return _err('Missing required fields.', 400)

    data, err = Discussion.create_post(
        user,
        title,
        content,
        problem_id,
        payload.get('Category', ''),
        payload.get('Language', ''),
        payload.get('Contains_Code', False),
    )

    if err:
        code = 403 if ('permission' in err.lower()
                       or 'allowed' in err.lower()) else 400
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/reply', methods=['POST'])
@login_required
def create_discussion_reply(user, post_id):
    payload = request.get_json(silent=True) or {}
    content = (payload.get('Content') or '').strip()
    if not content:
        return _err('Content is required.', 400)

    data, err = Discussion.add_reply(
        user,
        post_id,
        content,
        payload.get('Reply_To', post_id),
        payload.get('Contains_Code', False),
    )

    if err:
        code = 404 if 'not found' in err.lower() else 403
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/like', methods=['POST'])
@login_required
def like_discussion_target(user, post_id):
    payload = request.get_json(silent=True) or {}
    try:
        target_id = int(payload.get('ID'))
        action = bool(payload.get('Action'))
    except (TypeError, ValueError):
        return _err('Invalid ID or Action.', 400)

    data, err = Discussion.toggle_like(user, post_id, target_id, action)
    if err:
        # 測試預期 Target Not Found -> 404
        code = 404 if 'not found' in err.lower() else 403
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/status', methods=['POST'])
@login_required
def update_discussion_post_status(user, post_id):
    payload = request.get_json(silent=True) or {}
    action = (payload.get('Action') or '').strip().lower()

    data, err = Discussion.update_status(user, post_id, action)
    if err:
        # Invalid Action -> 400 in tests
        code = 400 if 'unsupported' in err.lower() else 403
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/delete', methods=['DELETE'])
@login_required
def delete_discussion_entity(user, post_id):
    payload = request.get_json(silent=True) or {}
    type_raw = (payload.get('Type') or '').strip().lower()
    try:
        target_id = int(payload.get('Id'))
    except (TypeError, ValueError):
        return _err('Invalid Id.', 400)

    data, err = Discussion.delete_entity(user, post_id, type_raw, target_id)
    if err:
        # 測試對 Not Found 預期 404，對 type error 預期 400
        code = 403
        if 'not found' in err.lower():
            code = 404
        elif 'invalid type' in err.lower() or 'match' in err.lower():
            code = 400
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>', methods=['GET'])
@login_required
def get_discussion_post_detail(user, post_id):
    data, err = Discussion.get_post_detail(user, post_id)
    if err:
        # 測試預期 Insufficient Permission -> 403
        code = 403 if 'permission' in err.lower() else 404
        return _err(err, code)

    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': [data]})
