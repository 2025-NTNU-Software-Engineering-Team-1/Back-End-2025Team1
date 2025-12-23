from flask import Blueprint, current_app
from .auth import login_required
from .utils import Request, HTTPResponse, HTTPError
from mongo.discussion import Discussion

__all__ = ['discussion_api']

discussion_api = Blueprint('discussion_api', __name__)


def _err(msg, code=400):
    # 確保回傳格式包含 data={'Status': 'ERR'} 以滿足測試
    return HTTPError(msg, code, data={'Status': 'ERR'})


def format_discussion_post(post):
    # (保持你目前的 format_discussion_post 邏輯)
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
    if problem_id is None: problem_id = ''
    return {
        'Post_Id': post.post_id,
        'Author': author_display,
        'Title': post.title,
        'Created_Time': post.created_time.isoformat(),
        'Like_Count': post.like_count or 0,
        'Reply_Count': post.reply_count or 0,
        'Is_Pinned': bool(post.is_pinned),
        'Is_Solved': bool(post.is_solved),
        'Is_Closed': bool(post.is_closed),
        'Problem_Id': problem_id,
    }


@discussion_api.route('/posts', methods=['GET'])
@login_required
@Request.args('Limit', 'Page', 'Problem_Id', 'Mode')
def list_discussion_posts(user, Limit, Page, Problem_Id, Mode):
    # 手動處理型別與預設值，避免裝飾器拋出 400
    try:
        limit = max(1, min(int(Limit or 20), 50))
        page = max(1, min(int(Page or 1), 1000))
    except (ValueError, TypeError):
        return _err('Limit and Page must be integers.', 400)

    mode = (Mode or 'New').strip()
    if mode not in ('New', 'Hot'):
        return _err('Invalid Mode. Available values: New, Hot.', 400)

    try:
        data = Discussion.get_feed(user, mode, limit, page, Problem_Id)
    except ValueError:
        return _err('Invalid parameter.', 400)

    raw_posts = data.get('Posts', [])
    data['Posts'] = [format_discussion_post(post) for post in raw_posts]

    resp_data = {
        'Status': 'OK',
        'Mode': mode,
        'Limit': limit,
        'Page': page,
        **data
    }
    if Problem_Id:
        resp_data.update({
            'Problem_Id': Problem_Id,
            'problemId': Problem_Id,
            'problem_id': Problem_Id
        })
    return HTTPResponse('Success.', data=resp_data)


@discussion_api.route('/problems', methods=['GET'])
@login_required
@Request.args('Limit', 'Page', 'Mode')
def list_discussion_problems(user, Limit, Page, Mode):
    try:
        limit = max(1, min(int(Limit or 20), 50))
        page = max(1, min(int(Page or 1), 1000))
    except (ValueError, TypeError):
        return _err('Limit and Page must be integers.', 400)

    mode = (Mode or 'All').strip()
    if mode.lower() != 'all':
        return _err('Invalid Mode. Available values: All.', 400)

    data = Discussion.get_problems(user, 'All', limit, page)
    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Mode': 'All',
                            'Limit': limit,
                            'Page': page,
                            **data
                        })


@discussion_api.route('/search', methods=['GET'])
@login_required
@Request.args('Words', 'Limit', 'Page')
def search_discussion_posts(user, Words, Limit, Page):
    if Words is None: return _err('Words parameter is required.', 400)
    words = Words.strip()
    if not words:
        return HTTPResponse('Success.', data={'Status': 'OK', 'Post': []})

    try:
        limit = max(1, min(int(Limit or 20), 50))
        page = max(1, min(int(Page or 1), 1000))
    except (ValueError, TypeError):
        return _err('Limit and Page must be integers.', 400)

    posts = Discussion.search_posts(user, words, limit, page)
    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': posts})


@discussion_api.route('/post', methods=['POST'])
@login_required
@Request.json('Title', 'Content', 'Problem_id', 'Category', 'Language',
              'Contains_Code')
def create_discussion_post(user, Title, Content, Problem_id, Category,
                           Language, Contains_Code):
    if not all([Title, Content, Problem_id]):
        return _err('Missing required fields.', 400)

    data, err = Discussion.create_post(user,
                                       str(Title).strip(),
                                       str(Content).strip(),
                                       str(Problem_id).strip(), Category or "",
                                       Language or "", bool(Contains_Code))
    if err:
        code = 403 if 'permission' in err.lower() or 'allowed' in err.lower(
        ) else 400
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/reply', methods=['POST'])
@login_required
@Request.json('Content', 'Reply_To', 'Contains_Code')
def create_discussion_reply(user, post_id, Content, Reply_To, Contains_Code):
    content = str(Content or '').strip()
    if not content: return _err('Content is required.', 400)

    data, err = Discussion.add_reply(
        user, post_id, content, Reply_To if Reply_To is not None else post_id,
        bool(Contains_Code))
    if err:
        code = 404 if 'not found' in err.lower() else 403
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/like', methods=['POST'])
@login_required
@Request.json('ID', 'Action')
def like_discussion_target(user, post_id, ID, Action):
    if ID is None or Action is None: return _err('Invalid ID or Action.', 400)
    data, err = Discussion.toggle_like(user, post_id, int(ID), bool(Action))
    if err:
        code = 404 if 'not found' in err.lower() else 403
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/status', methods=['POST'])
@login_required
@Request.json('Action')
def update_discussion_post_status(user, post_id, Action):
    data, err = Discussion.update_status(user, post_id,
                                         str(Action or '').lower())
    if err:
        code = 400 if 'unsupported' in err.lower() else 403
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>/delete', methods=['DELETE'])
@login_required
@Request.json('Type', 'Id')
def delete_discussion_entity(user, post_id, Type, Id):
    if Id is None: return _err('Invalid Id.', 400)
    data, err = Discussion.delete_entity(user, post_id,
                                         str(Type or '').lower(), int(Id))
    if err:
        code = 404 if 'not found' in err.lower() else (
            400 if 'type' in err.lower() else 403)
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/problems/<problem_id>/meta', methods=['GET'])
@login_required
def get_discussion_problem_meta(user, problem_id):
    data, err = Discussion.get_problem_meta(user, problem_id)
    if err: return _err(err, 404)
    return HTTPResponse('Success.', data={'Status': 'OK', **data})


@discussion_api.route('/posts/<int:post_id>', methods=['GET'])
@login_required
def get_discussion_post_detail(user, post_id):
    data, err = Discussion.get_post_detail(user, post_id)
    if err:
        code = 403 if 'permission' in err.lower() else 404
        return _err(err, code)
    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': [data]})
