from datetime import datetime
from mongo import engine
from mongo.course import Course as CourseWrapper

__all__ = ['Post']

class Post:
    @classmethod
    def _log_action(cls, user, action, target_id=None):
        try:
            engine.DiscussionLog(
                user=user.obj,
                action=action,
                target_type='post',
                target_id=str(target_id) if target_id is not None else None,
            ).save()
        except Exception:
            pass

    @classmethod
    def get_post_data(cls, user, course_name, target_thread_id=None):
        course_obj = CourseWrapper(course_name)
        if not course_obj:
            return None, "Course not found.", 404
        
        user_ref = getattr(user, 'obj', user)
        if not course_obj.permission(user_ref, CourseWrapper.Permission.VIEW):
            return None, "You are not in this course.", 403
        
        data = cls.found_post(course_obj, target_thread_id)
        return data, None, 200

    @classmethod
    def modify_post_logic(cls, method, user, course_name, target_thread_id, title, content, contains_code, problem_id):
        target_course = None
        target_thread = None

        if course_name:
            target_course = CourseWrapper(course_name)
            if not target_course: return "Course not exist.", 404
        elif target_thread_id:
            target_thread = cls.get_thread(target_thread_id)
            if not target_thread: return "Post/reply not exist.", 404
            if target_thread.status: return "Forbidden,the post/reply is deleted.", 403
            target_course = CourseWrapper(target_thread.course_id)
        
        user_ref = getattr(user, 'obj', user)
        capability = target_course.own_permission(user_ref)
        if capability <= 0: return "You are not in this course.", 403

        if method == 'POST':
            # 截止日期檢查
            err_msg = cls._check_deadline_guard(target_course, user_ref, contains_code, problem_id)
            if err_msg: return err_msg, 403
            
            if course_name:
                cls.add_post(course_name, user, content, title)
            else:
                cls.add_reply(target_thread, user, content)
        elif method == 'PUT':
            err = cls.edit_post(target_thread, user, content, title, capability)
            if err: return err, 403
        elif method == 'DELETE':
            err = cls.delete_post(target_thread, user, capability)
            if err: return err, 403

        return None, 200

    @classmethod
    def update_status_logic(cls, user, post_id, action):
        target_thread = cls.get_thread(post_id)
        if not target_thread: return "Post/reply not exist.", 404

        target_course = CourseWrapper(target_thread.course_id)
        user_ref = getattr(user, 'obj', user)
        capability = target_course.own_permission(user_ref)
        if capability <= 0: return "You are not in this course.", 403

        if user.role not in (engine.User.Role.ADMIN, engine.User.Role.TEACHER, engine.User.Role.TA):
            return "Forbidden, you don't have enough permission.", 403

        action = action.upper()
        if action == 'DELETE':
            err = cls.delete_post(target_thread, user, capability)
            return err, (403 if err else 200)

        updates = {'updated': datetime.now()}
        if action == 'PIN': updates['pinned'] = True
        elif action == 'UNPIN': updates['pinned'] = False
        elif action == 'SOLVE': updates['solved'] = True
        elif action == 'CLOSE': updates['closed'] = True
        else: return "Invalid action.", 400
        
        target_thread.update(**updates)
        cls._log_action(user, f"{action}_POST", target_thread.id)
        return None, 200

    @classmethod
    def get_thread(cls, thread_id):
        try:
            return engine.PostThread.objects.get(id=thread_id)
        except engine.DoesNotExist:
            try:
                return engine.Post.objects.get(id=thread_id).thread
            except engine.DoesNotExist:
                return None

    @classmethod
    def _check_deadline_guard(cls, target_course, user_ref, contains_code, problem_id):
        if not (contains_code and problem_id): return None
        capability = target_course.own_permission(user_ref)
        if bool(capability & (CourseWrapper.Permission.GRADE | CourseWrapper.Permission.MODIFY)):
            return None
        
        # 呼叫獲取截止日期的私有方法
        deadline = cls.get_problem_deadline(problem_id)
        if deadline is None: return None
        now = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.now()
        if now < deadline:
            return 'Posting code is not allowed before deadline.'
        return None

    @classmethod
    def get_problem_deadline(cls, problem_id):
        pid = int(problem_id) if str(problem_id).isdigit() else problem_id
        p_obj = engine.Problem.objects(problem_id=pid).first() or engine.Problem.objects(pk=pid).first()
        if p_obj:
            return getattr(p_obj, 'deadline', None) or getattr(p_obj, 'Deadline', None)
        return None

    @classmethod
    def found_thread(cls, target_thread):
        return {
            'Id': str(target_thread.id),
            'Content': target_thread.markdown,
            'Author': target_thread.author.info,
            'Status': target_thread.status,
            'Pinned': getattr(target_thread, 'pinned', False),
            'Closed': getattr(target_thread, 'closed', False),
            'Solved': getattr(target_thread, 'solved', False),
            'Created': target_thread.created.timestamp(),
            'Updated': target_thread.updated.timestamp(),
            'Reply': [cls.found_thread(r) for r in target_thread.reply] if target_thread.reply else []
        }

    @classmethod
    def found_post(cls, course_obj, target_id=None):
        data = []
        for x in course_obj.posts:
            if target_id and str(x.thread.id) != target_id: continue
            data.append({'thread': cls.found_thread(x.thread), 'title': x.post_name})
        return data

    @classmethod
    def add_post(cls, course, user, content, title):
        course_obj = CourseWrapper(course).obj
        now = datetime.now()
        new_thread = engine.PostThread(markdown=content, course_id=course_obj, author=user.obj, created=now, updated=now).save()
        new_post = engine.Post(post_name=title, thread=new_thread).save()
        course_obj.modify(push__posts=new_post)
        cls._log_action(user, 'CREATE_POST', new_thread.id)

    @classmethod
    def add_reply(cls, target_thread, user, content):
        if target_thread.depth + 1 > 2: return
        now = datetime.now()
        new_thread = engine.PostThread(markdown=content, course_id=target_thread.course_id, depth=target_thread.depth+1, created=now, updated=now, author=user.obj).save()
        target_thread.modify(push__reply=new_thread)
        cls._log_action(user, 'CREATE_REPLY', new_thread.id)

    @classmethod
    def edit_post(cls, target_thread, user, content, title, capability, delete=0):
        author = target_thread.author
        if delete:
            if user.obj != author and not (capability & CourseWrapper.Permission.GRADE): return 'Forbidden'
            target_thread.update(set__status=1, set__markdown='*Content was deleted.*')
            cls._log_action(user, 'DELETE_POST', target_thread.id)
        else:
            if user.obj != author and not (capability & CourseWrapper.Permission.MODIFY): return 'Forbidden'
            target_thread.update(set__markdown=content, set__updated=datetime.now())
            cls._log_action(user, 'EDIT_POST', target_thread.id)
        
        target_post = engine.Post.objects(thread=target_thread).first()
        if target_post:
            target_post.update(set__post_name='*The Post was deleted*' if delete else title)

    @classmethod
    def delete_post(cls, target_thread, user, capability):
        return cls.edit_post(target_thread, user, None, None, capability, delete=1)
