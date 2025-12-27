import pytest
from datetime import datetime, timedelta
from tests.base_tester import BaseTester, random_string
from mongo import engine


class TestPost(BaseTester):
    '''Test post (Course Bulletin/Forum)'''

    def setup_method(self):
        """徹底清理測試環境，避免 Unique Key 衝突"""
        engine.Course.drop_collection()
        engine.User.drop_collection()
        engine.Post.drop_collection()
        engine.PostThread.drop_collection()
        engine.Problem.drop_collection()
        engine.DiscussionLog.drop_collection()

    def _assert_post_log(self, action, username, target_id):
        log = engine.DiscussionLog.objects(
            action=action).order_by('-timestamp').first()
        assert log is not None
        assert log.user.username == username
        assert log.target_id == str(target_id)

    def _setup_course_and_user(self,
                               course_name='math',
                               student_name='student',
                               ta_name=None):
        """
        建立標準測試環境：
        1. 確保 admin (老師) 存在且 userId 符合系統預期
        2. 確保 TA (助教) 存在並具備正確角色
        3. 建立課程並將學生與助教加入
        4. 雙向同步 courses 參考列表 (這是 own_permission 檢查的核心)
        """
        # 1. 確保 admin 使用者存在
        admin = engine.User.objects(username='admin').first()
        if not admin:
            admin = engine.User(username='admin',
                                role=engine.User.Role.ADMIN,
                                active=True,
                                email='admin@test.com',
                                user_id='admin',
                                md5='test').save()

        # 2. 準備 TA
        tas_docs = []
        if ta_name:
            ta_user = engine.User.objects(username=ta_name).first()
            if not ta_user:
                ta_user = engine.User(username=ta_name,
                                      role=engine.User.Role.TA,
                                      active=True,
                                      email=f'{ta_name}@test.com',
                                      user_id=ta_name,
                                      md5='test').save()
            tas_docs.append(ta_user)

        # 3. 準備學生
        student = engine.User.objects(username=student_name).first()
        if not student:
            student = engine.User(username=student_name,
                                  role=engine.User.Role.STUDENT,
                                  active=True,
                                  email=f'{student_name}@test.com',
                                  user_id=student_name,
                                  md5='test').save()

        # 4. 建立課程
        course = engine.Course(course_name=course_name,
                               teacher=admin,
                               tas=tas_docs,
                               student_nicknames={
                                   student_name: 'nick'
                               }).save()

        # 5. 更新使用者的 courses 參考列表
        student.update(add_to_set__courses=course)
        for ta in tas_docs:
            ta.update(add_to_set__courses=course)

        course.reload()
        return course, student

    def test_add_post(self, client_admin, forge_client):
        course_name = 'math'
        self._setup_course_and_user(course_name=course_name,
                                    student_name='student')

        # 使用 forge_client 重新生成以確保 Token 同步
        client_student = forge_client('student')
        rv = client_student.post('/post',
                                 json={
                                     'course': course_name,
                                     'title': 'Work',
                                     'content': 'Coding.'
                                 })
        assert rv.status_code == 200, rv.get_json()
        rv_get = client_student.get(f'/post/{course_name}')
        data = rv_get.get_json()['data']
        post_id = data[0]['thread']['Id']
        self._assert_post_log('CREATE_POST', 'student', post_id)

    def test_update_post_status_ta(self, client_admin, forge_client):
        ta_name = 'ta_user'
        course_name = 'math_status'

        # 在 setup 階段就建立好 TA 關聯
        course, student = self._setup_course_and_user(course_name=course_name,
                                                      student_name='student_1',
                                                      ta_name=ta_name)

        # 學生發文
        client_student = forge_client('student_1')
        client_student.post('/post',
                            json={
                                'course': course_name,
                                'title': 'H',
                                'content': 'C'
                            })

        rv_get = client_student.get(f'/post/{course_name}')
        res = rv_get.get_json()
        assert res['status'] == 'ok', res
        data = res.get('data', [])
        assert len(data) > 0, "Failed to retrieve student post"
        post_id = data[0]['thread']['Id']

        # TA 執行置頂 (PIN)
        client_ta = forge_client(ta_name)
        rv = client_ta.put(f'/post/status/{post_id}', json={'Action': 'PIN'})

        # 驗證成功回傳
        assert rv.status_code == 200, rv.get_json()

        # 驗證資料庫狀態
        thread = engine.PostThread.objects.get(id=post_id)
        assert thread.pinned is True
        self._assert_post_log('PIN_POST', ta_name, post_id)

    def test_update_post_status_student_forbidden(self, client_admin,
                                                  forge_client):
        course_name = 'math_status_student'
        self._setup_course_and_user(course_name=course_name,
                                    student_name='student_3')
        client_student = forge_client('student_3')
        client_student.post('/post',
                            json={
                                'course': course_name,
                                'title': 'H',
                                'content': 'C'
                            })
        rv_get = client_student.get(f'/post/{course_name}')
        post_id = rv_get.get_json()['data'][0]['thread']['Id']
        rv = client_student.put(f'/post/status/{post_id}',
                                json={'Action': 'PIN'})
        assert rv.status_code == 403, rv.get_json()

    def test_post_code_deadline_guard(self, client_admin, forge_client):
        course_name = 'math_deadline'
        course, student = self._setup_course_and_user(course_name=course_name,
                                                      student_name='student_2')

        # 獲取 admin 使用者作為題目擁有者
        admin = engine.User.objects.get(username='admin')
        deadline = datetime.now() + timedelta(days=1)

        problem = engine.Problem(
            problem_name='P1',
            owner='admin',  # 傳入字串以符合 StringField
            courses=[course]).save()

        # 強制更新截止日期 (針對 mongomock 同步問題)
        engine.Problem._get_collection().update_one(
            {'_id': problem.id}, {'$set': {
                'deadline': deadline
            }})

        client_student = forge_client('student_2')
        rv = client_student.post('/post',
                                 json={
                                     'course': course_name,
                                     'title': 'C',
                                     'content': 'print(1)',
                                     'Contains_Code': True,
                                     'problemId': str(problem.problem_id)
                                 })

        # 學生在截止日前貼程式碼應被拒絕
        assert rv.status_code == 403, rv.get_json()
        assert 'allowed before deadline' in rv.get_json()['message'].lower()

    def test_post_code_deadline_guard_ta_exempt(self, client_admin,
                                                forge_client):
        course_name = 'math_deadline_ta'
        ta_name = 'ta_deadline'
        course, student = self._setup_course_and_user(course_name=course_name,
                                                      student_name='student_4',
                                                      ta_name=ta_name)
        deadline = datetime.now() + timedelta(days=1)
        problem = engine.Problem(problem_name='P2',
                                 owner='admin',
                                 courses=[course]).save()
        engine.Problem._get_collection().update_one(
            {'_id': problem.id}, {'$set': {
                'deadline': deadline
            }})
        client_student = forge_client('student_4')
        rv = client_student.post('/post',
                                 json={
                                     'course': course_name,
                                     'title': 'C',
                                     'content': 'print(1)',
                                     'Contains_Code': True,
                                     'problemId': str(problem.problem_id)
                                 })
        assert rv.status_code == 403, rv.get_json()

        client_ta = forge_client(ta_name)
        rv = client_ta.post('/post',
                            json={
                                'course': course_name,
                                'title': 'C',
                                'content': 'print(1)',
                                'Contains_Code': True,
                                'problemId': str(problem.problem_id)
                            })
        assert rv.status_code == 200, rv.get_json()

    def test_post_delete_audit_log(self, client_admin, forge_client):
        course_name = 'math_delete'
        self._setup_course_and_user(course_name=course_name,
                                    student_name='student_5')
        client_student = forge_client('student_5')
        client_student.post('/post',
                            json={
                                'course': course_name,
                                'title': 'Work',
                                'content': 'Coding.'
                            })
        rv_get = client_student.get(f'/post/{course_name}')
        post_id = rv_get.get_json()['data'][0]['thread']['Id']
        rv = client_student.delete('/post', json={'targetThreadId': post_id})
        assert rv.status_code == 200, rv.get_json()
        self._assert_post_log('DELETE_POST', 'student_5', post_id)
