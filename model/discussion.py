from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from flask import Blueprint, current_app, request

from mongo import engine
from mongo.course import Course
from mongo.user import User

from .auth import login_required
from .utils.response import HTTPError, HTTPResponse

__all__ = ['discussion_api']

discussion_api = Blueprint('discussion_api', __name__)

_SUPPORTED_MODES = {
    'new': 'New',
    'hot': 'Hot',
}
_SUPPORTED_PROBLEM_MODES = {
    'all': 'All',
}
_DEFAULT_LIMIT = 20
_MIN_LIMIT = 1
_MAX_LIMIT = 50
_MAX_PAGE = 1000

# 允許執行管理操作（如刪除他人文章）的角色
_STAFF_ROLES = {
    engine.User.Role.TEACHER,
    engine.User.Role.TA,
    engine.User.Role.ADMIN,
}

# 允許修改文章狀態（如置頂、關閉）的角色
_STATUS_ROLES = {
    engine.User.Role.TEACHER,
    engine.User.Role.TA,
    engine.User.Role.ADMIN,
}

_ROLE_LABEL_BY_ENUM = {
    engine.User.Role.ADMIN: 'teacher',
    engine.User.Role.TEACHER: 'teacher',
    engine.User.Role.TA: 'ta',
    engine.User.Role.STUDENT: 'student',
}

_LABEL_TO_ROLE_ENUM = {
    'teacher': engine.User.Role.TEACHER,
    'ta': engine.User.Role.TA,
    'student': engine.User.Role.STUDENT,
}

_STATUS_ACTIONS = {
    'pin': ('is_pinned', True, 'pinned'),
    'unpin': ('is_pinned', False, 'unpinned'),
    'close': ('is_closed', True, 'closed'),
    'reopen': ('is_closed', False, 'open'),
    'solve': ('is_solved', True, 'solved'),
    'unsolve': ('is_solved', False, 'unsolved'),
}

_PERMITTED_ROLES_INT = {0, 1, 3}


@discussion_api.route('/posts', methods=['GET'])
@login_required
def list_discussion_posts(user):
    problem_id = (request.args.get('Problem_Id') or '').strip()
    try:
        limit = _clamp_int(request.args.get('Limit'), _DEFAULT_LIMIT,
                           _MIN_LIMIT, _MAX_LIMIT, 'Limit')
        page = _clamp_int(request.args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    except ValueError as exc:
        return HTTPError(str(exc), 400)

    if problem_id:
        feed = _build_problem_post_feed(user, problem_id, limit, page)
    else:
        try:
            mode = _parse_mode(request.args)
        except ValueError as exc:
            return HTTPError(str(exc), 400)
        feed = _build_feed(user, mode, limit, page)
    return HTTPResponse('Success.', data=feed)


def _parse_mode(args) -> str:
    mode_raw = (args.get('Mode') or 'New').strip().lower()
    if mode_raw not in _SUPPORTED_MODES:
        raise ValueError('Invalid Mode. Available values: New, Hot.')
    return _SUPPORTED_MODES[mode_raw]


def _parse_problem_query_params(args) -> Tuple[str, int, int]:
    mode_raw = (args.get('Mode') or 'All').strip().lower()
    if mode_raw not in _SUPPORTED_PROBLEM_MODES:
        raise ValueError('Invalid Mode. Available values: All.')
    mode = _SUPPORTED_PROBLEM_MODES[mode_raw]
    limit = _clamp_int(args.get('Limit'), _DEFAULT_LIMIT, _MIN_LIMIT,
                       _MAX_LIMIT, 'Limit')
    page = _clamp_int(args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    return mode, limit, page


def _problem_list_error(message: str, status_code: int):
    return HTTPError(message,
                     status_code,
                     data={
                         'Status': 'ERR',
                         'Message': message,
                         'Problems': [],
                     })


def _filter_problems_by_mode(mode: str, user):
    queryset = engine.Problem.objects(
        problem_status=engine.Problem.Visibility.SHOW)
    if mode == 'All':
        return queryset
    # TODO (#9): Implement additional filters (e.g., enrolled courses) once
    # the upstream APIs provide the necessary data.
    return queryset


def _serialize_problem(doc) -> Dict:
    return {
        'Problem_Id': doc.problem_id,
        'Problem_Name': doc.problem_name,
    }


@discussion_api.route('/problems', methods=['GET'])
@login_required
def list_discussion_problems(user):
    try:
        mode, limit, page = _parse_problem_query_params(request.args)
    except ValueError as exc:
        return _problem_list_error(str(exc), 400)

    try:
        queryset = _filter_problems_by_mode(mode, user)
        total = queryset.count()
        skip = (page - 1) * limit
        docs = queryset.order_by('problem_id').skip(skip).limit(limit)
        problems = [_serialize_problem(doc) for doc in docs]
    except Exception:  # pragma: no cover - defensive logging path
        current_app.logger.exception('Failed to fetch discussion problems')
        return _problem_list_error('Failed to fetch problems.', 500)

    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Mode': mode,
                            'Limit': limit,
                            'Page': page,
                            'Total': total,
                            'Problems': problems,
                        })


@discussion_api.route('/problems/<problem_id>/meta', methods=['GET'])
@login_required
def get_discussion_problem_meta(user, problem_id):
    problem = _load_problem(problem_id)
    if not problem:
        return _problem_meta_error()

    meta = _build_problem_meta(problem, user)
    deadline_str = meta['deadline'].isoformat() if meta['deadline'] else None
    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Problem_Name': problem.problem_name,
                            'Role': meta['role_label'],
                            'Deadline': deadline_str,
                            'Code_Allowed': meta['code_allowed'],
                        })


@discussion_api.route('/search', methods=['GET'])
@login_required
def search_discussion_posts(user):
    words_raw = request.args.get('Words')
    if words_raw is None:
        return HTTPError('Words parameter is required.',
                         400,
                         data={
                             'Status': 'ERR',
                             'Post': []
                         })
    words = words_raw.strip()
    try:
        limit = _clamp_int(request.args.get('Limit'), _DEFAULT_LIMIT,
                           _MIN_LIMIT, _MAX_LIMIT, 'Limit')
        page = _clamp_int(request.args.get('Page'), 1, 1, _MAX_PAGE, 'Page')
    except ValueError as exc:
        return HTTPError(str(exc), 400, data={'Status': 'ERR', 'Post': []})

    if words == '':
        return HTTPResponse('Success.', data={'Status': 'OK', 'Post': []})

    try:
        allowed_problem_ids = _get_viewable_problem_ids(user)
        matches = _search_posts(words, allowed_problem_ids)
    except Exception:  # pragma: no cover - defensive logging path
        current_app.logger.exception('Failed to search discussion posts')
        return HTTPError('Failed to search posts.',
                         500,
                         data={
                             'Status': 'ERR',
                             'Post': []
                         })

    total = len(matches)
    start = (page - 1) * limit
    end = start + limit
    window = matches[start:end] if start < total else []
    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': window})


@discussion_api.route('/post', methods=['POST'])
@login_required
def create_discussion_post(user):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response('Invalid JSON payload.',
                               400,
                               extra={'Post_ID': None})

    title = (payload.get('Title') or '').strip()
    content = (payload.get('Content') or '').strip()
    problem_id = str(payload.get('Problem_id') or '').strip()
    missing = [
        field
        for field, value in [('Title',
                              title), ('Content',
                                       content), ('Problem_id', problem_id)]
        if not value
    ]
    if missing:
        return _error_response(
            f"Missing required fields: {', '.join(missing)}",
            400,
            extra={'Post_ID': None})

    if not _can_view_problem_id(user, problem_id):
        return _error_response('Insufficient permission.',
                               403,
                               extra={'Post_ID': None})

    contains_code = bool(payload.get('Contains_Code', False))
    if not _code_sharing_allowed(user, problem_id, contains_code):
        return _error_response(
            'Posting code is not allowed before deadline.',
            403,
            extra={'Post_ID': None},
        )

    category = (payload.get('Category') or '').strip()
    language = (payload.get('Language') or '').strip()

    now = datetime.now()
    try:
        doc = engine.DiscussionPost(
            title=title,
            content=content,
            problem_id=problem_id,
            category=category,
            language=language,
            contains_code=contains_code,
            author=user.obj,
            created_time=now,
            updated_time=now,
        ).save()
    except Exception:  # pragma: no cover - defensive logging path
        current_app.logger.exception('Failed to create discussion post')
        return _error_response('Failed to create discussion post.',
                               500,
                               extra={'Post_ID': None})

    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Post_ID': doc.post_id,
                        })


@discussion_api.route('/posts/<int:post_id>/reply', methods=['POST'])
@login_required
def create_discussion_reply(user, post_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response('Invalid JSON payload.',
                               400,
                               extra={'Reply_ID': None})

    content = (payload.get('Content') or '').strip()
    if not content:
        return _error_response('Content is required.',
                               400,
                               extra={'Reply_ID': None})

    contains_code = bool(payload.get('Contains_Code', False))
    post = engine.DiscussionPost.objects(post_id=post_id).first()
    if not post or post.is_deleted:
        return _error_response('Post not found.',
                               404,
                               extra={'Reply_ID': None})

    if not _can_view_post(user, post):
        return _error_response('Insufficient permission.',
                               403,
                               extra={'Reply_ID': None})

    if not _code_sharing_allowed(user, post.problem_id, contains_code):
        return _error_response(
            'Posting code is not allowed before deadline.',
            403,
            extra={'Reply_ID': None},
        )

    reply_to_raw = payload.get('Reply_To', post_id)
    try:
        reply_to_id = int(reply_to_raw)
    except (TypeError, ValueError):
        return _error_response('Reply_To must be a number.',
                               400,
                               extra={'Reply_ID': None})

    parent_reply = None
    if reply_to_id not in (post_id, 0):
        parent_reply = engine.DiscussionReply.objects(
            post=post, reply_id=reply_to_id, is_deleted=False).first()
        if not parent_reply:
            return _error_response('Reply_To target not found.',
                                   404,
                                   extra={'Reply_ID': None})
    else:
        reply_to_id = post_id

    now = datetime.now()
    try:
        reply = engine.DiscussionReply(
            post=post,
            parent_reply=parent_reply,
            reply_to_id=reply_to_id,
            author=user.obj,
            content=content,
            contains_code=contains_code,
            created_time=now,
        ).save()
        post.update(inc__reply_count=1, updated_time=now)
    except Exception:  # pragma: no cover - defensive logging path
        current_app.logger.exception('Failed to create discussion reply')
        return _error_response('Failed to create discussion reply.',
                               500,
                               extra={'Reply_ID': None})

    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Reply_ID': reply.reply_id,
                        })


@discussion_api.route('/posts/<int:post_id>/like', methods=['POST'])
@login_required
def like_discussion_target(user, post_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response('Invalid JSON payload.',
                               400,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })

    if 'ID' not in payload:
        return _error_response('ID is required.',
                               400,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })
    try:
        target_id = int(payload.get('ID'))
    except (TypeError, ValueError):
        return _error_response('ID must be a number.',
                               400,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })

    action = payload.get('Action')
    if not isinstance(action, bool):
        return _error_response('Action must be a boolean.',
                               400,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })

    post = engine.DiscussionPost.objects(post_id=post_id).first()
    if not post or post.is_deleted:
        return _error_response('Post not found.',
                               404,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })

    if not _can_view_post(user, post):
        return _error_response('Insufficient permission.',
                               403,
                               extra={
                                   'Like_Count': 0,
                                   'Like_Status': False
                               })

    target_post = None
    target_reply = None
    if target_id == post_id:
        target_post = post
    else:
        target_reply = engine.DiscussionReply.objects(
            post=post, reply_id=target_id, is_deleted=False).first()
        if not target_reply:
            return _error_response('Target not found.',
                                   404,
                                   extra={
                                       'Like_Count': 0,
                                       'Like_Status': False,
                                   })

    target_type = 'post' if target_post else 'reply'
    like_qs = engine.DiscussionLike.objects(user=user.obj,
                                            target_type=target_type,
                                            target_id=target_id)
    existing_like = like_qs.first()

    if action:
        if not existing_like:
            engine.DiscussionLike(user=user.obj,
                                  target_type=target_type,
                                  target_id=target_id).save()
            _adjust_like_count(target_post or target_reply, 1)
        like_status = True
    else:
        if existing_like:
            existing_like.delete()
            _adjust_like_count(target_post or target_reply, -1)
        like_status = False

    like_count = _get_like_count(target_post or target_reply)
    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Like_Count': like_count,
                            'Like_Status': like_status,
                        })


@discussion_api.route('/posts/<int:post_id>/status', methods=['POST'])
@login_required
def update_discussion_post_status(user, post_id: int):
    role_value = user.role
    if hasattr(role_value, 'value'):
        role_value = role_value.value
    try:
        role_value = int(role_value)
    except (TypeError, ValueError):
        role_value = engine.User.Role.STUDENT
    if role_value not in _PERMITTED_ROLES_INT:
        return _error_response('Insufficient permission.',
                               403,
                               extra={'New_Status': None})

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response('Invalid JSON payload.',
                               400,
                               extra={'New_Status': None})

    raw_action = (payload.get('Action') or '').strip().lower()
    if not raw_action:
        return _error_response('Action is required.',
                               400,
                               extra={'New_Status': None})

    action = _STATUS_ACTIONS.get(raw_action)
    if not action:
        return _error_response('Unsupported action.',
                               400,
                               extra={'New_Status': None})

    post = engine.DiscussionPost.objects(post_id=post_id).first()
    if not post:
        return _error_response('Post not found.',
                               404,
                               extra={'New_Status': None})
    if not _can_view_post(user, post):
        return _error_response('Insufficient permission.',
                               403,
                               extra={'New_Status': None})

    field, value, new_status = action
    update_payload = {f'set__{field}': value}
    try:
        post.update(**update_payload)
        post.reload(field)
    except Exception:  # pragma: no cover - defensive logging path
        current_app.logger.exception('Failed to update post status')
        return _error_response('Failed to update status.',
                               500,
                               extra={'New_Status': None})

    _log_audit(raw_action, post_id, user, {
        'field': field,
        'value': value,
    })

    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'New_Status': new_status,
                        })


@discussion_api.route('/posts/<int:post_id>/delete', methods=['DELETE'])
@login_required
def delete_discussion_entity(user, post_id: int):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _error_response('Invalid JSON payload.',
                               400,
                               extra={'Message': 'Invalid payload.'})

    type_raw = (payload.get('Type') or '').strip().lower()
    target_id_raw = payload.get('Id')
    if type_raw not in {'post', 'reply'}:
        return _error_response('Type must be either "post" or "reply".',
                               400,
                               extra={'Message': 'Invalid type.'})
    try:
        target_id = int(target_id_raw)
    except (TypeError, ValueError):
        return _error_response('Id must be a number.',
                               400,
                               extra={'Message': 'Invalid id.'})

    post = engine.DiscussionPost.objects(post_id=post_id).first()
    if not post or post.is_deleted:
        return _error_response('Post not found.',
                               404,
                               extra={'Message': 'Post not found.'})

    if not _can_view_post(user, post):
        return _error_response('Insufficient permission.',
                               403,
                               extra={'Message': 'Permission denied.'})

    if type_raw == 'post':
        if target_id != post_id:
            return _error_response('Id must match postId for Type=post.',
                                   400,
                                   extra={'Message': 'Mismatch id.'})
        # 檢查刪除權限：包含作者本人或職員(老師/TA/管理員)
        if not _can_delete_entity(user, post.author):
            return _error_response('Permission denied.',
                                   403,
                                   extra={'Message': 'Permission denied.'})
        if post.is_deleted:
            return _error_response('Post already deleted.',
                                   400,
                                   extra={'Message': 'Already deleted.'})
        post.update(set__is_deleted=True)
        message = 'Post deleted.'
        _log_audit('delete_post', post_id, user, {'target': 'post'})
        return HTTPResponse('Success.',
                            data={
                                'Status': 'OK',
                                'Message': message,
                            })

    # reply branch
    reply = engine.DiscussionReply.objects(post=post,
                                           reply_id=target_id).first()
    if not reply or reply.is_deleted:
        return _error_response('Reply not found.',
                               404,
                               extra={'Message': 'Reply not found.'})
    if not _can_delete_entity(user, reply.author):
        return _error_response('Permission denied.',
                               403,
                               extra={'Message': 'Permission denied.'})

    reply.update(set__is_deleted=True)
    new_count = max((post.reply_count or 0) - 1, 0)
    post.update(set__reply_count=new_count)
    _log_audit('delete_reply', post_id, user, {
        'target': 'reply',
        'reply_id': target_id
    })
    return HTTPResponse('Success.',
                        data={
                            'Status': 'OK',
                            'Message': 'Reply deleted.',
                        })


@discussion_api.route('/posts/<int:post_id>', methods=['GET'])
@login_required
def get_discussion_post_detail(user, post_id: int):
    post = engine.DiscussionPost.objects(post_id=post_id).first()
    if not post or post.is_deleted:
        return _error_response('Post not found.', 404, extra={'Post': []})
    if not _can_view_post(user, post):
        return _error_response('Insufficient permission.',
                               403,
                               extra={'Post': []})

    replies = engine.DiscussionReply.objects(
        post=post, is_deleted=False).order_by('created_time')
    payload = _serialize_discussion_post(post, replies)
    return HTTPResponse('Success.', data={'Status': 'OK', 'Post': [payload]})


def _clamp_int(raw_value: Optional[str], default: int, min_value: int,
               max_value: int, field_name: str) -> int:
    if raw_value in (None, ''):
        return default
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f'{field_name} must be an integer.') from exc
    if parsed < min_value:
        return min_value
    if parsed > max_value:
        return max_value
    return parsed


def _build_feed(user, mode: str, limit: int, page: int) -> Dict:
    allowed_problem_ids = _get_viewable_problem_ids(user)
    if allowed_problem_ids is not None and not allowed_problem_ids:
        return {
            'Status': 'OK',
            'Mode': mode,
            'Limit': limit,
            'Page': page,
            'Total': 0,
            'Posts': [],
        }

    queryset = engine.DiscussionPost.objects(is_deleted=False)
    if allowed_problem_ids is not None:
        queryset = queryset.filter(problem_id__in=list(allowed_problem_ids))

    if mode == 'Hot':
        posts_list = list(queryset)
        posts_list.sort(
            key=lambda p: (-int(p.is_pinned or False), -(p.like_count or 0) -
                           (p.reply_count or 0), -p.created_time.timestamp()))
    else:
        posts_list = list(queryset.order_by('-is_pinned', '-created_time', '-post_id'))

    total = len(posts_list)
    start = (page - 1) * limit
    end = start + limit
    window = posts_list[start:end] if start < total else []

    # 序列化貼文
    serialized_posts = [_serialize_problem_post(post) for post in window]

    return {
        'Status': 'OK',
        'Mode': mode,
        'Limit': limit,
        'Page': page,
        'Total': total,
        'Posts': serialized_posts,
    }


def _build_problem_post_feed(user, problem_id: str, limit: int,
                             page: int) -> Dict:
    allowed_problem_ids = _get_viewable_problem_ids(user)
    if allowed_problem_ids is not None and problem_id not in allowed_problem_ids:
        return {
            'Status': 'OK',
            'Problem_Id': problem_id,
            'Limit': limit,
            'Page': page,
            'Total': 0,
            'Posts': [],
        }
    queryset = engine.DiscussionPost.objects(problem_id=problem_id,
                                             is_deleted=False)
    total = queryset.count()
    start = (page - 1) * limit
    posts = queryset.order_by('-is_pinned', '-created_time',
                              '-post_id').skip(start).limit(limit)
    return {
        'Status': 'OK',
        'Problem_Id': problem_id,
        'Limit': limit,
        'Page': page,
        'Total': total,
        'Posts': [_serialize_problem_post(post) for post in posts],
    }


def _get_viewable_course_ids(user) -> Optional[set]:
    if user.role == engine.User.Role.ADMIN:
        return None
    course_ids = {
        str(course.id)
        for course in getattr(user, 'courses', []) if course
    }
    public_course = Course.get_public()
    if public_course:
        course_ids.add(str(public_course.id))
    return course_ids


def _get_viewable_problem_ids(user) -> Optional[set]:
    course_ids = _get_viewable_course_ids(user)
    if course_ids is None:
        return None
    if not course_ids:
        return set()

    course_refs = engine.Course.objects(id__in=list(course_ids))
    queryset = engine.Problem.objects(
        problem_status=engine.Problem.Visibility.SHOW,
        courses__in=course_refs,
    )
    return {str(problem.problem_id) for problem in queryset}


def _can_view_problem_id(user, problem_id: str) -> bool:
    role_value = user.role
    if hasattr(role_value, 'value'):
        role_value = role_value.value
    try:
        role_value = int(role_value)
    except (TypeError, ValueError):
        role_value = engine.User.Role.STUDENT
    if role_value in _PERMITTED_ROLES_INT:
        return True

    problem = _load_problem(problem_id)
    if not problem:
        return True
    allowed_problem_ids = _get_viewable_problem_ids(user)
    if allowed_problem_ids is None:
        return True
    return str(problem_id) in allowed_problem_ids


def _can_view_post(user, post) -> bool:
    return _can_view_problem_id(user, post.problem_id)


def _collect_posts(course_filter: Optional[Sequence[str]]) -> List[Dict]:
    if course_filter is not None and not course_filter:
        return []
    summaries: List[Dict] = []
    for post in engine.Post.objects:
        thread = post.thread
        if not thread or thread.status == 1:
            continue
        course = thread.course_id
        if not course:
            continue
        if course_filter is not None and str(course.id) not in course_filter:
            continue
        summaries.append(_serialize_post(post, thread, course))
    return summaries


def _search_posts(words: str,
                  problem_filter: Optional[Sequence[str]]) -> List[Dict]:
    if problem_filter is not None and not problem_filter:
        return []
    pattern = re.compile(re.escape(words), re.IGNORECASE)
    matches: List[Tuple[Tuple[float, int], Dict]] = []
    
    queryset = engine.DiscussionPost.objects(is_deleted=False)
    if problem_filter is not None:
        queryset = queryset.filter(problem_id__in=list(problem_filter))
    for post in queryset:
        title = post.title or ''
        content = post.content or ''
        if not (pattern.search(title) or pattern.search(content)):
            continue
        sort_key = (post.created_time.timestamp(), post.post_id)
        matches.append((sort_key, _serialize_search_discussion_post(post)))
    
    matches.sort(key=lambda item: item[0], reverse=True)
    return [data for _, data in matches]

def _serialize_search_discussion_post(post) -> Dict:
    author_name = post.author.username if post.author else ''
    return {
        'Post_Id': post.post_id,
        'Author': author_name,
        'Title': post.title,
        'Created_Time': post.created_time.isoformat(),
        'Like_Count': post.like_count or 0,
        'Reply_Count': post.reply_count or 0,
    }


def _serialize_problem_post(post) -> Dict:
    author_name = post.author.username if post.author else ''
    return {
        'Post_Id': post.post_id,
        'Author': author_name,
        'Title': post.title,
        'Created_Time': post.created_time.isoformat(),
        'Like_Count': post.like_count or 0,
        'Reply_Count': post.reply_count or 0,
        'Is_Pinned': bool(post.is_pinned),
        'Is_Solved': bool(post.is_solved),
        'Is_Closed': bool(post.is_closed),
        'Problem_id': post.problem_id,
    }


def _load_problem(problem_id: str):
    try:
        parsed = int(str(problem_id).strip())
    except (TypeError, ValueError):
        return None
    return engine.Problem.objects(problem_id=parsed).first()


def _build_problem_meta(problem, user) -> Dict:
    role_label, role_enum = _determine_role_for_problem(problem, user)
    deadline = _extract_problem_deadline(problem)
    code_allowed = _is_code_allowed_for_role(role_label, deadline)
    return {
        'role_label': role_label,
        'role_enum': role_enum,
        'deadline': deadline,
        'code_allowed': code_allowed,
    }


def _determine_role_for_problem(problem, user) -> Tuple[str, engine.User.Role]:
    for course in getattr(problem, 'courses', []) or []:
        label = _role_in_course(course, user)
        if label:
            return label, _LABEL_TO_ROLE_ENUM[label]
    fallback_label = _ROLE_LABEL_BY_ENUM.get(user.role, 'student')
    return fallback_label, _LABEL_TO_ROLE_ENUM.get(fallback_label,
                                                   engine.User.Role.STUDENT)


def _role_in_course(course, user) -> Optional[str]:
    if not course:
        return None
    if getattr(course, 'teacher', None) == user.obj:
        return 'teacher'
    tas = getattr(course, 'tas', None) or []
    if user.obj in tas:
        return 'ta'
    student_map = getattr(course, 'student_nicknames', None) or {}
    if user.username in student_map:
        return 'student'
    return None


def _extract_problem_deadline(problem) -> Optional[datetime]:
    deadlines: List[datetime] = []
    for hw in getattr(problem, 'homeworks', []) or []:
        if hw is None:
            continue
        homework = hw
        if not isinstance(hw, engine.Homework):
            try:
                homework = engine.Homework.objects(id=hw.id).first()
            except Exception:  # pragma: no cover - best effort fallback
                homework = None
        if not homework or not getattr(homework, 'duration', None):
            continue
        end_time = getattr(homework.duration, 'end', None)
        if end_time:
            deadlines.append(end_time)
    if not deadlines:
        return None
    return min(deadlines)


def _is_code_allowed_for_role(role_label: str,
                              deadline: Optional[datetime]) -> bool:
    if role_label in ('teacher', 'ta'):
        return True
    if deadline is None:
        return False
    return datetime.now() >= deadline


def _problem_meta_error():
    return HTTPError('Problem not found.',
                     404,
                     data={
                         'Status': 'ERR',
                         'Role': None,
                         'Deadline': None,
                         'Code_Allowed': False,
                     })


def _error_response(message: str,
                    status_code: int,
                    extra: Optional[Dict] = None):
    data = {
        'Status': 'ERR',
        'Message': message,
    }
    if extra:
        data.update(extra)
    return HTTPResponse(message, status_code=status_code, data=data)


def _code_sharing_allowed(user, problem_id: str, contains_code: bool) -> bool:
    if not contains_code:
        return True
    meta = _fetch_problem_meta(problem_id, user)
    role = meta.get('role', user.role)
    code_allowed = meta.get('code_allowed', True)
    if role == engine.User.Role.STUDENT and not code_allowed:
        return False
    return True


def _fetch_problem_meta(problem_id: str, user):
    problem = _load_problem(problem_id)
    if not problem:
        return {
            'role': user.role,
            'code_allowed': True,
        }
    meta = _build_problem_meta(problem, user)
    return {
        'role': meta['role_enum'],
        'code_allowed': meta['code_allowed'],
    }


def _adjust_like_count(target, delta: int):
    if target is None:
        return
    current = target.like_count or 0
    new_value = current + delta
    if new_value < 0:
        new_value = 0
    target.update(set__like_count=new_value)
    target.reload('like_count')


def _get_like_count(target) -> int:
    if target is None:
        return 0
    target.reload('like_count')
    return target.like_count or 0


def _serialize_discussion_post(post, replies_qs) -> Dict:
    author_name = post.author.username if post.author else ''
    replies = [_serialize_reply(reply) for reply in replies_qs]
    return {
        'Post_Id': post.post_id,
        'Title': post.title,
        'Author': author_name,
        'Created_Time': post.created_time.isoformat(),
        'Content': post.content,
        'Like_Count': post.like_count or 0,
        'Reply_Count': post.reply_count,
        'Category': post.category,
        'Is_Solved': bool(post.is_solved),
        'Is_Pinned': bool(post.is_pinned),
        'Is_Closed': bool(post.is_closed),
        'Replies': replies,
    }


def _serialize_reply(reply) -> Dict:
    author_name = reply.author.username if reply.author else ''
    return {
        'Reply_ID': reply.reply_id,
        'Author': author_name,
        'Created_Time': reply.created_time.isoformat(),
        'Content': reply.content,
        'Like_Count': reply.like_count or 0,
        'Reply_To': reply.reply_to_id,
        'Contains_Code': bool(reply.contains_code),
    }


def _log_audit(action: str, post_id: int, user, detail: Dict):
    current_app.logger.info(
        'DISCUSSION_AUDIT action=%s post_id=%s user=%s detail=%s', action,
        post_id, getattr(user, 'username', None), detail)


def _is_staff(user) -> bool:
    return user.role in _STAFF_ROLES


def _can_delete_entity(user, author) -> bool:
    if user.role in _STAFF_ROLES:
        return True
    return author == user.obj


def _serialize_post(post, thread, course) -> Dict:
    author_info = None
    if thread.author:
        author_info = User(thread.author).info
    reply_count = _count_replies(thread.reply or [])
    return {
        'Post_ID': str(post.id),
        'Title': post.post_name,
        'Author': author_info,
        'Course': {
            'Course_ID': str(course.id),
            'Course_Name': course.course_name,
        },
        'Created_Time': thread.created.timestamp(),
        'Updated_Time': thread.updated.timestamp(),
        'Like_Count': getattr(post, 'like_count', 0),
        'Reply_Count': reply_count,
        'Is_Pinned': bool(getattr(post, 'is_pinned', False)),
    }


def _count_replies(children: Iterable) -> int:
    total = 0
    for reply in children:
        if reply.status == 1:
            continue
        total += 1 + _count_replies(reply.reply or [])
    return total


def _sort_posts(posts: List[Dict], mode: str) -> List[Dict]:
    if not posts:
        return []
    if mode == 'Hot':
        key_func = lambda post: (  # noqa: E731 - intentional inline lambda
            post['Is_Pinned'],
            post['Reply_Count'] + post['Like_Count'],
            post['Created_Time'],
        )
    else:
        key_func = lambda post: (post['Is_Pinned'], post['Created_Time'])
    return sorted(posts, key=key_func, reverse=True)
