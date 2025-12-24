import re
from datetime import datetime
from typing import Dict, Optional, Tuple

from mongo import engine

__all__ = ['Discussion']

# 常數定義
_STAFF_ROLES = {
    engine.User.Role.TEACHER,
    engine.User.Role.TA,
    engine.User.Role.ADMIN,
}
_PERMITTED_ROLES_INT = {0, 1, 3}

_CODE_BLOCK_MARKER_RE = re.compile(r'```|<code>|</code>', re.IGNORECASE)
_CODE_STRONG_LINE_RE = re.compile(
    r'^\s*(def|class|import|from|#include|public|private|protected|static|'
    r'function|const|let|var)\b|[{};]')
_CODE_WEAK_LINE_RE = re.compile(
    r'\b(if|else|for|while|switch|case|return|break|continue|try|except|'
    r'catch|finally)\b|==|!=|<=|>=|->|=>')


class Discussion:

    @classmethod
    def _role_can_bypass_acl(cls, user) -> bool:
        role_value = (user.role.value
                      if hasattr(user.role, 'value') else user.role)
        try:
            role_value = int(role_value)
        except (TypeError, ValueError):
            role_value = 2  # Default Student
        return role_value in _PERMITTED_ROLES_INT

    @classmethod
    def _detect_contains_code(cls, content: str) -> bool:
        if not content:
            return False
        py_block_re = re.compile(r'(?m)^('
                                 r'\s*for\s+\w[\w,\s]*\s+in\s+.+:\s*$'
                                 r'|\s*(?:if|elif|while)\s+.+:\s*$'
                                 r'|\s*(?:def|class)\s+\w+\s*(?:\(|:).*$'
                                 r'|\s*(?:try|except|finally|with)\b.*:\s*$'
                                 r')\n[ \t]{2,}\S+')
        if py_block_re.search(content):
            return True
        if _CODE_BLOCK_MARKER_RE.search(content):
            return True
        code_like_lines = 0
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            if _CODE_STRONG_LINE_RE.search(line):
                return True
            if _CODE_WEAK_LINE_RE.search(line):
                code_like_lines += 1
                if code_like_lines >= 2:
                    return True
        return False

    @classmethod
    def _get_viewable_problem_ids(cls, user) -> Optional[set]:
        """
        取得使用者有權限觀看的 Problem ID 集合。
        回傳 None 代表是管理員，可看全部。
        """
        if user.role == engine.User.Role.ADMIN:
            return None

        course_refs = [
            course for course in getattr(user, 'courses', []) if course
        ]
        if not course_refs:
            return set()

        queryset = engine.Problem.objects(
            problem_status=engine.Problem.Visibility.SHOW,
            courses__in=course_refs,
        )
        return {str(problem.problem_id) for problem in queryset}

    @classmethod
    def _can_view_problem(cls, user, problem_id: str) -> bool:
        role_value = (user.role.value
                      if hasattr(user.role, 'value') else user.role)
        try:
            role_value = int(role_value)
        except (TypeError, ValueError):
            role_value = 2  # Default Student

        if role_value in _PERMITTED_ROLES_INT:
            return True

        allowed_ids = cls._get_viewable_problem_ids(user)
        if allowed_ids is None:
            return True
        return str(problem_id) in allowed_ids

    @classmethod
    def get_feed(cls,
                 user,
                 mode: str,
                 limit: int,
                 page: int,
                 problem_id: str = None,
                 course_id: str = None) -> Dict:
        allowed_ids = cls._get_viewable_problem_ids(user)
        if cls._role_can_bypass_acl(user):
            allowed_ids = None

        if course_id:
            course = engine.Course.objects(course_name=course_id).first()
            if not course:
                return {'Total': 0, 'Posts': []}
            # 找出該課程下的所有題目 ID
            course_problems = engine.Problem.objects(courses=course)
            course_pids = {str(p.problem_id) for p in course_problems}

            if allowed_ids is None:
                allowed_ids = course_pids
            else:
                allowed_ids &= course_pids

        allow_public_problem = False
        if problem_id is not None:
            problem_id = str(problem_id).strip()
            if not problem_id:
                problem_id = None

        # 若非管理員且無課程
        if allowed_ids is not None and not allowed_ids:
            if not problem_id:
                return {'Total': 0, 'Posts': []}
            if problem_id.isdigit():
                problem = engine.Problem.objects(
                    problem_id=int(problem_id),
                    problem_status=engine.Problem.Visibility.SHOW,
                ).first()
                allow_public_problem = bool(problem)
            if not allow_public_problem and not cls._can_view_problem(
                    user, problem_id):
                return {'Total': 0, 'Posts': []}

        queryset = engine.DiscussionPost.objects(is_deleted=False)

        if allowed_ids is not None and allowed_ids:
            queryset = queryset.filter(problem_id__in=list(allowed_ids))

        if problem_id:
            # 權限檢查：如果指定了題目，要在允許清單內
            if (allowed_ids is not None and problem_id not in allowed_ids
                    and not allow_public_problem):
                return {'Total': 0, 'Posts': []}
            candidates = [problem_id]
            if problem_id.isdigit():
                candidates.append(int(problem_id))
            queryset = queryset.filter(problem_id__in=candidates)

        posts_list = None
        if mode == 'Hot':
            posts_list = list(queryset)
            posts_list.sort(key=lambda p: (
                -int(p.is_pinned or False),
                -(p.like_count or 0) - (p.reply_count or 0),
                -p.created_time.timestamp(),
                -int(p.post_id),
            ), )
        else:
            # New: Pinned DESC, Created DESC, ID DESC
            queryset = queryset.order_by('-is_pinned', '-created_time',
                                         '-post_id')

        total = len(posts_list) if posts_list is not None else queryset.count()

        start = (page - 1) * limit
        end = start + limit

        if posts_list is not None:
            window = posts_list[start:end] if start < total else []
        else:
            window = queryset.skip(start).limit(limit)

        window = list(window)

        return {
            'Total': total,
            'Posts': window,
        }

    @classmethod
    def get_problems(cls,
                     user,
                     mode: str,
                     limit: int,
                     page: int,
                     course_id: str = None) -> Dict:
        # 目前需求 mode 只有 All，未來可擴充
        criteria = {'problem_status': engine.Problem.Visibility.SHOW}

        if course_id:
            course = engine.Course.objects(course_name=course_id).first()
            if not course:
                return {'Total': 0, 'Problems': []}
            criteria['courses'] = course

        if user.role != engine.User.Role.ADMIN:
            course_refs = [
                course for course in getattr(user, 'courses', []) if course
            ]
            if not course_refs:
                return {'Total': 0, 'Problems': []}

            if course_id:
                # 若指定課程，需檢查使用者是否在該課程中
                # course 變數在上面已取得
                if course not in course_refs:
                    return {'Total': 0, 'Problems': []}
            else:
                criteria['courses__in'] = course_refs

        queryset = engine.Problem.objects(**criteria)

        total = queryset.count()
        skip = (page - 1) * limit
        docs = queryset.order_by('problem_id').skip(skip).limit(limit)

        return {
            'Total':
            total,
            'Problems': [{
                'Problem_Id': doc.problem_id,
                'Problem_Name': doc.problem_name,
            } for doc in docs],
        }

    @classmethod
    def _log_action(cls, user, action, target_type=None, target_id=None):
        try:
            engine.DiscussionLog(
                user=user.obj,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
            ).save()
        except Exception:
            # Log failure should not block main flow
            pass

    @classmethod
    def _check_code_deadline(cls, problem,
                             user) -> Tuple[str, bool, Optional[datetime]]:
        """回傳 (role_label, code_allowed, deadline)"""
        # 1. Role Check
        role_label = 'student'
        is_staff = user.role in (
            engine.User.Role.ADMIN,
            engine.User.Role.TEACHER,
            engine.User.Role.TA,
        )

        # Mapping Enum to Label
        if user.role == engine.User.Role.ADMIN:
            role_label = 'teacher'
        elif user.role == engine.User.Role.TEACHER:
            role_label = 'teacher'
        elif user.role == engine.User.Role.TA:
            role_label = 'ta'

        # Check Course Role
        for course in getattr(problem, 'courses', []) or []:
            if not course:
                continue
            # Handle ReferenceField resolution if needed
            if getattr(course, 'teacher', None) == user.obj:
                role_label = 'teacher'
                break
            if user.obj in (getattr(course, 'tas', None) or []):
                role_label = 'ta'
                break

        # 2. Deadline Check
        deadlines = []
        for hw in getattr(problem, 'homeworks', []) or []:
            if not hw:
                continue
            homework = hw
            if not isinstance(hw, engine.Homework):
                homework = engine.Homework.objects(id=hw.id).first()

            if homework and getattr(homework, 'duration', None):
                end = getattr(homework.duration, 'end', None)
                if end:
                    deadlines.append(end)

        deadline = min(deadlines) if deadlines else getattr(
            problem, 'deadline', None)

        # 3. Code Allowed
        if is_staff:
            return role_label, True, deadline

        code_allowed = True
        if getattr(problem, 'allow_code', True) is False:
            code_allowed = False
        elif deadline and datetime.now() < deadline:
            code_allowed = False

        return role_label, code_allowed, deadline

    @classmethod
    def get_problem_meta(cls, user,
                         problem_id) -> Tuple[Optional[Dict], Optional[str]]:
        try:
            pid = int(str(problem_id).strip())
        except ValueError:
            return None, 'Invalid ID'

        problem = engine.Problem.objects(problem_id=pid).first()
        if not problem:
            return None, 'Problem not found.'

        role_label, code_allowed, deadline = cls._check_code_deadline(
            problem, user)

        return {
            'Role': role_label,
            'Deadline': deadline.isoformat() if deadline else None,
            'Code_Allowed': code_allowed,
        }, None

    @classmethod
    def create_post(cls, user, title, content, problem_id, category, language,
                    contains_code_flag):
        if not cls._can_view_problem(user, problem_id):
            return None, 'Insufficient permission.'

        contains_code = bool(contains_code_flag)
        if not contains_code:
            contains_code = cls._detect_contains_code(content)

        if contains_code:
            try:
                pid = int(problem_id)
                problem = engine.Problem.objects(problem_id=pid).first()
                if problem:
                    _, code_allowed, _ = cls._check_code_deadline(
                        problem, user)
                    if not code_allowed:
                        return None, (
                            'Posting code is not allowed before deadline.')
            except ValueError:
                pass

        now = datetime.now()
        post = engine.DiscussionPost(
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

        cls._log_action(user, 'CREATE_POST', 'post', post.post_id)
        return {'Post_ID': post.post_id}, None

    @classmethod
    def search_posts(cls, user, words, limit, page, course_id=None):
        allowed_ids = cls._get_viewable_problem_ids(user)
        if cls._role_can_bypass_acl(user):
            allowed_ids = None

        if course_id:
            course = engine.Course.objects(course_name=course_id).first()
            if not course:
                return []
            course_problems = engine.Problem.objects(courses=course)
            course_pids = {str(p.problem_id) for p in course_problems}

            if allowed_ids is None:
                allowed_ids = course_pids
            else:
                allowed_ids &= course_pids

        if allowed_ids is not None and not allowed_ids:
            return []

        pattern = re.compile(re.escape(words), re.IGNORECASE)
        queryset = engine.DiscussionPost.objects(is_deleted=False)

        if allowed_ids is not None:
            queryset = queryset.filter(problem_id__in=list(allowed_ids))

        matches = []
        for post in queryset:
            if pattern.search(post.title or '') or pattern.search(post.content
                                                                  or ''):
                matches.append({
                    # Sort Key: Time DESC, ID DESC (stable tie-breaker)
                    'sort_key':
                    (post.created_time.timestamp(), int(post.post_id)),
                    'data': {
                        'Post_Id': post.post_id,
                        'Author': post.author.username if post.author else '',
                        'Title': post.title,
                        'Created_Time': post.created_time.isoformat(),
                        'Like_Count': post.like_count or 0,
                        'Reply_Count': post.reply_count or 0,
                    },
                })

        matches.sort(key=lambda x: x['sort_key'], reverse=True)

        start = (page - 1) * limit
        end = start + limit
        return [m['data'] for m in matches[start:end]]

    @classmethod
    def get_post_detail(cls, user, post_id):
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        if not post or post.is_deleted:
            return None, 'Post not found.'

        if not cls._can_view_problem(user, post.problem_id):
            return None, 'Insufficient permission.'

        replies_qs = engine.DiscussionReply.objects(
            post=post, is_deleted=False).order_by('created_time')

        replies_data = [{
            'Reply_Id': r.reply_id,
            'Author': r.author.username if r.author else '',
            'Created_Time': r.created_time.isoformat(),
            'Content': r.content,
            'Like_Count': r.like_count or 0,
            'Reply_To': r.reply_to_id,
            'Contains_Code': bool(r.contains_code),
        } for r in replies_qs]

        data = {
            'Post_Id': post.post_id,
            'Title': post.title,
            'Author': post.author.username if post.author else '',
            'Created_Time': post.created_time.isoformat(),
            'Content': post.content,
            'Like_Count': post.like_count or 0,
            'Reply_Count': post.reply_count,
            'Category': post.category,
            'Is_Solved': bool(post.is_solved),
            'Is_Pinned': bool(post.is_pinned),
            'Is_Closed': bool(post.is_closed),
            'Replies': replies_data,
        }
        return data, None

    @classmethod
    def add_reply(cls, user, post_id, content, reply_to_id,
                  contains_code_flag):
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        if not post or post.is_deleted:
            return None, 'Post not found.'

        if not cls._can_view_problem(user, post.problem_id):
            return None, 'Insufficient permission.'

        contains_code = bool(contains_code_flag)
        if not contains_code:
            contains_code = cls._detect_contains_code(content)

        if contains_code:
            try:
                pid = int(post.problem_id)
                problem = engine.Problem.objects(problem_id=pid).first()
                if problem:
                    _, code_allowed, _ = cls._check_code_deadline(
                        problem, user)
                    if not code_allowed:
                        return None, (
                            'Posting code is not allowed before deadline.')
            except ValueError:
                pass

        parent_reply = None
        target_id = post_id
        if reply_to_id and int(reply_to_id) != post_id:
            target_id = int(reply_to_id)
            parent_reply = engine.DiscussionReply.objects(
                post=post, reply_id=target_id, is_deleted=False).first()
            if not parent_reply:
                return None, 'Reply_To target not found.'

        now = datetime.now()
        reply = engine.DiscussionReply(
            post=post,
            parent_reply=parent_reply,
            reply_to_id=target_id,
            author=user.obj,
            content=content,
            contains_code=contains_code,
            created_time=now,
        ).save()

        post.update(inc__reply_count=1, set__updated_time=now)
        cls._log_action(user, 'CREATE_REPLY', 'reply', reply.reply_id)
        return {'Reply_ID': reply.reply_id}, None

    @classmethod
    def toggle_like(cls, user, post_id, target_id, action):
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        if not post or post.is_deleted:
            return None, 'Post not found.'

        if not cls._can_view_problem(user, post.problem_id):
            return None, 'Insufficient permission.'

        target = None
        target_type = 'post'
        if target_id == post_id:
            target = post
        else:
            target = engine.DiscussionReply.objects(post=post,
                                                    reply_id=target_id,
                                                    is_deleted=False).first()
            target_type = 'reply'

        if not target:
            return None, 'Target not found.'

        existing_like = engine.DiscussionLike.objects(
            user=user.obj,
            target_type=target_type,
            target_id=target_id,
        ).first()

        if action:
            if not existing_like:
                engine.DiscussionLike(
                    user=user.obj,
                    target_type=target_type,
                    target_id=target_id,
                ).save()
                target.update(inc__like_count=1)
                if target_type == 'post':
                    log_action = 'LIKE_POST'
                else:
                    log_action = 'LIKE_REPLY'
                cls._log_action(user, log_action, target_type, target_id)
        else:
            if existing_like:
                existing_like.delete()
                target.reload('like_count')
                if (target.like_count or 0) > 0:
                    target.update(inc__like_count=-1)
                if target_type == 'post':
                    log_action = 'UNLIKE_POST'
                else:
                    log_action = 'UNLIKE_REPLY'
                cls._log_action(user, log_action, target_type, target_id)

        target.reload('like_count')
        return {
            'Like_Count': target.like_count or 0,
            'Like_Status': action,
        }, None

    @classmethod
    def update_status(cls, user, post_id, action_key):
        role_value = (user.role.value
                      if hasattr(user.role, 'value') else user.role)
        if role_value not in _PERMITTED_ROLES_INT:
            return None, 'Insufficient permission.'

        post = engine.DiscussionPost.objects(post_id=post_id).first()
        if not post:
            return None, 'Post not found.'

        actions = {
            'pin': ('is_pinned', True, 'pinned'),
            'unpin': ('is_pinned', False, 'unpinned'),
            'close': ('is_closed', True, 'closed'),
            'reopen': ('is_closed', False, 'open'),
            'solve': ('is_solved', True, 'solved'),
            'unsolve': ('is_solved', False, 'unsolved'),
        }

        if action_key not in actions:
            return None, 'Unsupported action.'

        field, value, status_text = actions[action_key]
        post.update(**{f'set__{field}': value})
        cls._log_action(user, f'{action_key.upper()}_POST', 'post', post_id)

        return {'New_Status': status_text}, None

    @classmethod
    def delete_entity(cls, user, post_id, target_type, target_id):
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        if not post or post.is_deleted:
            return None, 'Post not found.'

        if not cls._can_view_problem(user, post.problem_id):
            return None, 'Insufficient permission.'

        is_staff = user.role in _STAFF_ROLES

        if target_type == 'post':
            if int(target_id) != post_id:
                return None, 'Id must match postId.'
            if not is_staff and post.author != user.obj:
                return None, 'Permission denied.'

            if post.is_deleted:
                return None, 'Post already deleted.'

            post.update(set__is_deleted=True)
            cls._log_action(user, 'DELETE_POST', 'post', post_id)
            return {'Message': 'Post deleted.'}, None

        if target_type == 'reply':
            reply = engine.DiscussionReply.objects(post=post,
                                                   reply_id=target_id).first()
            if not reply or reply.is_deleted:
                return None, 'Reply not found.'

            if not is_staff and reply.author != user.obj:
                return None, 'Permission denied.'

            reply.update(set__is_deleted=True)
            post.reload('reply_count')
            new_count = max((post.reply_count or 0) - 1, 0)
            post.update(set__reply_count=new_count)
            cls._log_action(user, 'DELETE_REPLY', 'reply', target_id)
            return {'Message': 'Reply deleted.'}, None

        return None, 'Invalid type.'
