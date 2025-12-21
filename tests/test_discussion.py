from datetime import datetime, timedelta

from tests.base_tester import BaseTester, random_string
from mongo import Course, engine


class TestDiscussion(BaseTester):

    def setup_method(self):
        engine.DiscussionPost.drop_collection()
        engine.DiscussionReply.drop_collection()
        engine.DiscussionLike.drop_collection()

    def _create_discussion_post(self, client, **overrides):
        if 'Problem_id' not in overrides:
            public_course = Course.get_public()
            problem = self._create_problem(f'Auto-{random_string(4)}',
                                           courses=[public_course.obj])
            overrides['Problem_id'] = str(problem.problem_id)
        payload = {
            'Title': 'Discuss Problem',
            'Content': 'Initial content',
            'Problem_id': 'P-001',
            'Category': 'General',
            'Language': 'Python',
            'Contains_Code': False,
        }
        payload.update(overrides)
        rv = client.post('/discussion/post', json=payload)
        data = rv.get_json()
        assert rv.status_code == 200, data
        return data['data']['Post_ID']

    def _create_course_with_student(self):
        course_name = f'discussion-{random_string(4)}'
        Course.add_course(course_name, 'teacher')
        course = Course(course_name)
        course.update_student_namelist({'student': 'student'})
        return course

    def _create_problem(self, name: str, **overrides):
        payload = {
            'problem_name':
            name,
            'owner':
            overrides.pop('owner', 'teacher'),
            'problem_status':
            overrides.pop('problem_status', engine.Problem.Visibility.SHOW),
        }
        payload.update(overrides)
        return engine.Problem(**payload).save()

    def _create_problem_for_course(self, course, name=None):
        if name is None:
            name = f'Problem-{random_string(4)}'
        return self._create_problem(name, courses=[course.obj])

    def _reset_problem_collection(self):
        engine.Problem.drop_collection()

    def _create_problem_with_homework(self, deadline):
        course_name = f'meta-course-{random_string(4)}'
        Course.add_course(course_name, 'teacher')
        course = Course(course_name)
        course.update_student_namelist({'student': 'student'})
        problem = engine.Problem(problem_name=f'Meta-{random_string(4)}',
                                 owner='teacher',
                                 courses=[course.obj]).save()
        duration = engine.Duration(start=datetime.now() - timedelta(days=1),
                                   end=deadline)
        homework = engine.Homework(homework_name=f'HW-{random_string(4)}',
                                   course_id=str(course.obj.id),
                                   duration=duration,
                                   problem_ids=[problem.problem_id],
                                   student_status={}).save()
        problem.update(add_to_set__homeworks=homework)
        course.update(push__homeworks=homework)
        return problem, course

    def test_discussion_posts_paginated_new(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        for idx in range(7):
            self._create_discussion_post(
                teacher_client,
                Problem_id=str(problem.problem_id),
                Title=f'Post {idx}',
                Content=f'Body {idx}',
            )

        # create posts in another course that the student cannot view
        other_course = f'discussion-{random_string(4)}'
        Course.add_course(other_course, 'teacher')
        other_course_obj = Course(other_course)
        other_problem = self._create_problem_for_course(other_course_obj)
        for idx in range(2):
            self._create_discussion_post(
                teacher_client,
                Problem_id=str(other_problem.problem_id),
                Title=f'Other {idx}',
                Content=f'Other body {idx}',
            )

        student_client = forge_client('student')
        rv = student_client.get('/discussion/posts',
                                query_string={
                                    'Mode': 'New',
                                    'Limit': 3,
                                    'Page': 2,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Limit'] == 3
        assert data['Page'] == 2
        assert data['Total'] == 7
        assert len(data['Posts']) == 3
        assert [p['Title']
                for p in data['Posts']] == ['Post 3', 'Post 2', 'Post 1']

    def test_discussion_posts_hot_sorting(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        titles = ['Alpha', 'Beta', 'Gamma']
        post_ids = {}
        for title in titles:
            post_id = self._create_discussion_post(
                teacher_client,
                Problem_id=str(problem.problem_id),
                Title=title,
                Content=f'{title} body',
            )
            post_ids[title] = post_id

        for _ in range(2):
            teacher_client.post(f'/discussion/posts/{post_ids["Beta"]}/reply',
                                json={
                                    'Content': 'reply beta',
                                })
        teacher_client.post(f'/discussion/posts/{post_ids["Gamma"]}/reply',
                            json={
                                'Content': 'reply gamma',
                            })

        student_client = forge_client('student')
        rv = student_client.get('/discussion/posts',
                                query_string={
                                    'Mode': 'Hot',
                                    'Limit': 3,
                                    'Page': 1,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        posts = payload['data']['Posts']
        titles_order = [p['Title'] for p in posts]
        assert titles_order[:3] == ['Beta', 'Gamma', 'Alpha']
        reply_counts = {p['Title']: p['Reply_Count'] for p in posts}
        assert reply_counts['Beta'] == 2
        assert reply_counts['Gamma'] == 1
        assert reply_counts['Alpha'] == 0

    def test_discussion_search_by_words(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        self._create_discussion_post(
            teacher_client,
            Problem_id=str(problem.problem_id),
            Title='Alpha Title',
            Content='Some body',
        )
        self._create_discussion_post(
            teacher_client,
            Problem_id=str(problem.problem_id),
            Title='Boring Title',
            Content='Magic keyword inside body',
        )
        student_client = forge_client('student')
        rv = student_client.get('/discussion/search',
                                query_string={
                                    'Words': 'magic',
                                    'Limit': 5,
                                    'Page': 1,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Status'] == 'OK'
        assert len(data['Post']) == 1
        assert data['Post'][0]['Title'] == 'Boring Title'

    def test_discussion_search_empty_result(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        for idx in range(2):
            self._create_discussion_post(
                teacher_client,
                Problem_id=str(problem.problem_id),
                Title=f'Post {idx}',
                Content=f'Body {idx}',
            )
        student_client = forge_client('student')
        rv = student_client.get('/discussion/search',
                                query_string={
                                    'Words': 'NoMatch',
                                    'Limit': 5,
                                    'Page': 1,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Status'] == 'OK'
        assert data['Post'] == []

    def test_discussion_search_pagination(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        for idx in range(5):
            self._create_discussion_post(
                teacher_client,
                Problem_id=str(problem.problem_id),
                Title=f'Magic {idx}',
                Content='Magic words everywhere',
            )
        student_client = forge_client('student')
        rv = student_client.get('/discussion/search',
                                query_string={
                                    'Words': 'magic',
                                    'Limit': 2,
                                    'Page': 2,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        posts = payload['data']['Post']
        assert len(posts) == 2
        # 確認是第 3、4 筆結果（依建立時間排序，新貼文在前）
        expected_titles = ['Magic 2', 'Magic 1']
        assert [post['Title'] for post in posts] == expected_titles

    def test_discussion_search_visibility(self, forge_client):
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        other_course_name = f'discussion-{random_string(4)}'
        Course.add_course(other_course_name, 'teacher')
        other_course = Course(other_course_name)
        other_problem = self._create_problem_for_course(other_course)

        teacher_client = forge_client('teacher')
        self._create_discussion_post(
            teacher_client,
            Problem_id=str(problem.problem_id),
            Title='Visible keyword',
            Content='keyword in visible course',
        )
        self._create_discussion_post(
            teacher_client,
            Problem_id=str(other_problem.problem_id),
            Title='Hidden keyword',
            Content='keyword in hidden course',
        )

        student_client = forge_client('student')
        rv = student_client.get('/discussion/search',
                                query_string={
                                    'Words': 'keyword',
                                    'Limit': 10,
                                    'Page': 1,
                                })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        posts = payload['data']['Post']
        assert len(posts) == 1
        assert posts[0]['Title'] == 'Visible keyword'

    def test_discussion_search_missing_words(self, forge_client):
        student_client = forge_client('student')
        rv = student_client.get('/discussion/search')
        payload = rv.get_json()
        assert rv.status_code == 400, payload
        assert payload['data']['Status'] == 'ERR'
        assert payload['data']['Post'] == []

    def test_discussion_problem_list_basic(self, forge_client):
        client = forge_client('student')
        self._reset_problem_collection()
        try:
            names = ['Alpha', 'Beta', 'Gamma']
            for name in names:
                self._create_problem(name)
            self._create_problem(
                'Hidden', problem_status=engine.Problem.Visibility.HIDDEN)

            rv = client.get('/discussion/problems')
            payload = rv.get_json()
            assert rv.status_code == 200, payload
            data = payload['data']
            assert data['Status'] == 'OK'
            assert [p['Problem_Name'] for p in data['Problems']] == names
            assert all('Problem_Id' in item for item in data['Problems'])
        finally:
            self._reset_problem_collection()

    def test_discussion_problem_list_pagination(self, forge_client):
        client = forge_client('student')
        self._reset_problem_collection()
        try:
            for idx in range(5):
                self._create_problem(f'Problem {idx}')

            rv = client.get('/discussion/problems',
                            query_string={
                                'Limit': 2,
                                'Page': 2,
                            })
            payload = rv.get_json()
            assert rv.status_code == 200, payload
            items = payload['data']['Problems']
            assert [item['Problem_Name']
                    for item in items] == ['Problem 2', 'Problem 3']
        finally:
            self._reset_problem_collection()

    def test_discussion_problem_list_invalid_mode(self, forge_client):
        client = forge_client('student')
        self._reset_problem_collection()
        try:
            rv = client.get('/discussion/problems',
                            query_string={'Mode': 'Unknown'})
            payload = rv.get_json()
            assert rv.status_code == 400, payload
            assert payload['data']['Status'] == 'ERR'
            assert payload['data']['Problems'] == []
        finally:
            self._reset_problem_collection()

    def test_discussion_problem_list_mode_case_insensitive(self, forge_client):
        client = forge_client('student')
        self._reset_problem_collection()
        try:
            self._create_problem('Solo')
            rv = client.get('/discussion/problems',
                            query_string={'Mode': 'ALL'})
            payload = rv.get_json()
            assert rv.status_code == 200, payload
            assert payload['data']['Status'] == 'OK'
            assert len(payload['data']['Problems']) == 1
        finally:
            self._reset_problem_collection()

    def test_discussion_posts_filtered_by_problem(self, forge_client):
        client = forge_client('student')
        course = self._create_course_with_student()
        problem_target = str(
            self._create_problem_for_course(course).problem_id)
        other_problem = str(self._create_problem_for_course(course).problem_id)
        for idx in range(3):
            self._create_discussion_post(client,
                                         Problem_id=problem_target,
                                         Title=f'Topic {idx}')
        self._create_discussion_post(client,
                                     Problem_id=other_problem,
                                     Title='Other topic')

        rv = client.get('/discussion/posts',
                        query_string={'Problem_Id': problem_target})
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Status'] == 'OK'
        assert data['Problem_Id'] == problem_target
        assert len(data['Posts']) == 3
        assert all(post['Title'].startswith('Topic') for post in data['Posts'])

    def test_discussion_posts_by_problem_pagination(self, forge_client):
        client = forge_client('student')
        course = self._create_course_with_student()
        problem_target = str(
            self._create_problem_for_course(course).problem_id)
        for idx in range(5):
            self._create_discussion_post(client,
                                         Problem_id=problem_target,
                                         Title=f'Pag {idx}')

        rv = client.get('/discussion/posts',
                        query_string={
                            'Problem_Id': problem_target,
                            'Limit': 2,
                            'Page': 2,
                        })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        titles = [post['Title'] for post in payload['data']['Posts']]
        assert titles == ['Pag 2', 'Pag 1']

    def test_discussion_posts_problem_id_priority(self, forge_client):
        client = forge_client('student')
        course = self._create_course_with_student()
        problem_target = str(
            self._create_problem_for_course(course).problem_id)
        self._create_discussion_post(client,
                                     Problem_id=problem_target,
                                     Title='Priority topic')

        rv = client.get('/discussion/posts',
                        query_string={
                            'Problem_Id': problem_target,
                            'Mode': 'Hot',
                        })
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Problem_Id'] == problem_target
        assert len(data['Posts']) == 1

    def test_discussion_posts_problem_id_empty(self, forge_client):
        client = forge_client('student')
        rv = client.get('/discussion/posts',
                        query_string={'Problem_Id': 'PX-NO-POST'})
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Status'] == 'OK'
        assert data['Posts'] == []

    def test_discussion_problem_meta_student_before_deadline(
            self, forge_client):
        client = forge_client('student')
        deadline = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
        problem, _ = self._create_problem_with_homework(deadline)

        rv = client.get(f'/discussion/problems/{problem.problem_id}/meta')
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Status'] == 'OK'
        assert data['Role'] == 'student'
        assert data['Code_Allowed'] is False
        assert datetime.fromisoformat(data['Deadline']) == deadline

    def test_discussion_problem_meta_student_after_deadline(
            self, forge_client):
        client = forge_client('student')
        deadline = (datetime.now() - timedelta(days=1)).replace(microsecond=0)
        problem, _ = self._create_problem_with_homework(deadline)

        rv = client.get(f'/discussion/problems/{problem.problem_id}/meta')
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Role'] == 'student'
        assert data['Code_Allowed'] is True

    def test_discussion_problem_meta_ta_before_deadline(self, forge_client):
        ta_name = f'ta-{random_string(4)}'
        ta_user = self.add_user(ta_name, role=engine.User.Role.TA)
        deadline = (datetime.now() + timedelta(days=1)).replace(microsecond=0)
        problem, course = self._create_problem_with_homework(deadline)
        course.add_user(ta_user.obj)
        course.update(push__tas=ta_user.obj)

        client = forge_client(ta_name)
        rv = client.get(f'/discussion/problems/{problem.problem_id}/meta')
        payload = rv.get_json()
        assert rv.status_code == 200, payload
        data = payload['data']
        assert data['Role'] == 'ta'
        assert data['Code_Allowed'] is True

    def test_discussion_problem_meta_not_found(self, forge_client):
        client = forge_client('student')
        rv = client.get('/discussion/problems/999999/meta')
        payload = rv.get_json()
        assert rv.status_code == 404, payload
        assert payload['data']['Status'] == 'ERR'

    def test_create_discussion_post_success(self, forge_client):
        client = forge_client('student')
        payload = {
            'Title': 'Discuss Problem A',
            'Content': 'Here is my thought process',
            'Problem_id': 'P-100',
            'Category': 'General',
            'Language': 'Python',
            'Contains_Code': False,
        }
        rv = client.post('/discussion/post', json=payload)
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Status'] == 'OK'
        post_id = resp['data']['Post_ID']
        doc = engine.DiscussionPost.objects(problem_id='P-100').first()
        assert doc is not None
        assert doc.post_id == post_id
        assert doc.title == payload['Title']
        assert doc.author.username == 'student'

    def test_create_discussion_post_missing_fields(self, forge_client):
        client = forge_client('student')
        rv = client.post('/discussion/post', json={
            'Title': 'Only title',
        })
        resp = rv.get_json()
        assert rv.status_code == 400, resp
        assert resp['data']['Status'] == 'ERR'
        assert resp['data']['Post_ID'] is None

    def test_create_discussion_post_code_flag_blocked(self, forge_client,
                                                      monkeypatch):
        from model import discussion

        def fake_meta(problem_id, user):
            return {
                'role': engine.User.Role.STUDENT,
                'code_allowed': False,
            }

        monkeypatch.setattr(discussion, '_fetch_problem_meta', fake_meta)

        client = forge_client('student')
        rv = client.post('/discussion/post',
                         json={
                             'Title': 'Code leak',
                             'Content': 'print("solution")',
                             'Problem_id': 'P-200',
                             'Contains_Code': True,
                         })
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'
        assert resp['data']['Post_ID'] is None

    def test_create_discussion_post_code_detected_blocked(self, forge_client,
                                                          monkeypatch):
        from model import discussion

        def fake_meta(problem_id, user):
            return {
                'role': engine.User.Role.STUDENT,
                'code_allowed': False,
            }

        monkeypatch.setattr(discussion, '_fetch_problem_meta', fake_meta)

        client = forge_client('student')
        rv = client.post('/discussion/post',
                         json={
                             'Title': 'Code leak',
                             'Content': 'def solve():\n    return 1',
                             'Problem_id': 'P-201',
                             'Contains_Code': False,
                         })
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'
        assert resp['data']['Post_ID'] is None

    def test_reply_discussion_post_success(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.post(f'/discussion/posts/{post_id}/reply',
                         json={'Content': 'Nice idea'})
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Status'] == 'OK'
        reply_id = resp['data']['Reply_ID']
        doc = engine.DiscussionReply.objects(reply_id=reply_id).first()
        assert doc is not None
        assert doc.parent_reply is None
        assert doc.post.post_id == post_id
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        assert post.reply_count == 1

    def test_reply_discussion_post_not_found(self, forge_client):
        client = forge_client('student')
        rv = client.post('/discussion/posts/999999/reply',
                         json={'Content': 'Hello'})
        resp = rv.get_json()
        assert rv.status_code == 404, resp
        assert resp['data']['Status'] == 'ERR'

    def test_reply_discussion_post_nested_reply(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        first = client.post(f'/discussion/posts/{post_id}/reply',
                            json={
                                'Content': 'First reply'
                            }).get_json()
        first_id = first['data']['Reply_ID']
        rv = client.post(f'/discussion/posts/{post_id}/reply',
                         json={
                             'Reply_To': first_id,
                             'Content': 'Reply to reply'
                         })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        second_id = resp['data']['Reply_ID']
        doc = engine.DiscussionReply.objects(reply_id=second_id).first()
        assert doc.parent_reply.reply_id == first_id
        assert doc.reply_to_id == first_id

    def test_reply_discussion_post_code_blocked(self, forge_client,
                                                monkeypatch):
        from model import discussion

        def fake_meta(problem_id, user):
            return {
                'role': engine.User.Role.STUDENT,
                'code_allowed': False,
            }

        monkeypatch.setattr(discussion, '_fetch_problem_meta', fake_meta)
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.post(f'/discussion/posts/{post_id}/reply',
                         json={
                             'Content': 'print("ans")',
                             'Contains_Code': True,
                         })
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'

    def test_reply_discussion_post_code_detected_blocked(self, forge_client,
                                                         monkeypatch):
        from model import discussion

        def fake_meta(problem_id, user):
            return {
                'role': engine.User.Role.STUDENT,
                'code_allowed': False,
            }

        monkeypatch.setattr(discussion, '_fetch_problem_meta', fake_meta)
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.post(f'/discussion/posts/{post_id}/reply',
                         json={
                             'Content': 'for i in range(3):\n    print(i)',
                             'Contains_Code': False,
                         })
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'

    def test_like_post_first_time(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.post(f'/discussion/posts/{post_id}/like',
                         json={
                             'ID': post_id,
                             'Action': True,
                         })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Like_Count'] == 1
        assert resp['data']['Like_Status'] is True
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        assert post.like_count == 1

    def test_like_post_idempotent(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        client.post(f'/discussion/posts/{post_id}/like',
                    json={
                        'ID': post_id,
                        'Action': True,
                    })
        rv = client.post(f'/discussion/posts/{post_id}/like',
                         json={
                             'ID': post_id,
                             'Action': True,
                         })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Like_Count'] == 1
        assert resp['data']['Like_Status'] is True
        assert engine.DiscussionLike.objects(target_type='post',
                                             target_id=post_id).count() == 1

    def test_unlike_post(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        client.post(f'/discussion/posts/{post_id}/like',
                    json={
                        'ID': post_id,
                        'Action': True,
                    })
        rv = client.post(f'/discussion/posts/{post_id}/like',
                         json={
                             'ID': post_id,
                             'Action': False,
                         })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Like_Count'] == 0
        assert resp['data']['Like_Status'] is False
        assert engine.DiscussionLike.objects(target_type='post',
                                             target_id=post_id).count() == 0

    def test_like_reply_target_not_found(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.post(f'/discussion/posts/{post_id}/like',
                         json={
                             'ID': 999999,
                             'Action': True,
                         })
        resp = rv.get_json()
        assert rv.status_code == 404, resp
        assert resp['data']['Status'] == 'ERR'

    def test_get_discussion_post_with_replies(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        first_reply = client.post(f'/discussion/posts/{post_id}/reply',
                                  json={
                                      'Content': 'First'
                                  }).get_json()
        first_id = first_reply['data']['Reply_ID']
        client.post(f'/discussion/posts/{post_id}/reply',
                    json={
                        'Content': 'Second',
                        'Reply_To': first_id,
                    })
        rv = client.get(f'/discussion/posts/{post_id}')
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        data = resp['data']
        assert data['Status'] == 'OK'
        assert len(data['Post']) == 1
        post = data['Post'][0]
        assert post['Post_Id'] == post_id
        assert post['Reply_Count'] == 2
        assert [r['Content'] for r in post['Replies']] == ['First', 'Second']
        assert post['Replies'][1]['Reply_To'] == first_id

    def test_get_discussion_post_without_replies(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.get(f'/discussion/posts/{post_id}')
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        post = resp['data']['Post'][0]
        assert post['Replies'] == []
        assert post['Reply_Count'] == 0

    def test_get_discussion_post_not_found(self, forge_client):
        client = forge_client('student')
        rv = client.get('/discussion/posts/99999')
        resp = rv.get_json()
        assert rv.status_code == 404, resp
        assert resp['data']['Status'] == 'ERR'
        assert resp['data']['Post'] == []

    def test_get_discussion_post_permission_denied(self, forge_client):
        course_name = f'discussion-{random_string(4)}'
        Course.add_course(course_name, 'teacher')
        course = Course(course_name)
        problem = self._create_problem_for_course(course)
        teacher_client = forge_client('teacher')
        post_id = self._create_discussion_post(
            teacher_client,
            Problem_id=str(problem.problem_id),
        )

        student_client = forge_client('student')
        rv = student_client.get(f'/discussion/posts/{post_id}')
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'

    def test_manage_post_status_pin_cycle(self, forge_client):
        teacher_client = forge_client('teacher')
        post_id = self._create_discussion_post(teacher_client)
        rv = teacher_client.post(f'/discussion/posts/{post_id}/status',
                                 json={'Action': 'Pin'})
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['New_Status'] == 'pinned'
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        assert post.is_pinned is True
        rv = teacher_client.post(f'/discussion/posts/{post_id}/status',
                                 json={'Action': 'Unpin'})
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['New_Status'] == 'unpinned'
        post.reload('is_pinned')
        assert post.is_pinned is False

    def test_manage_post_status_invalid_action(self, forge_client):
        teacher_client = forge_client('teacher')
        post_id = self._create_discussion_post(teacher_client)
        rv = teacher_client.post(f'/discussion/posts/{post_id}/status',
                                 json={'Action': 'Unknown'})
        resp = rv.get_json()
        assert rv.status_code == 400, resp
        assert resp['data']['Status'] == 'ERR'

    def test_manage_post_status_permission_denied(self, forge_client):
        student_client = forge_client('student')
        post_id = self._create_discussion_post(student_client)
        rv = student_client.post(f'/discussion/posts/{post_id}/status',
                                 json={'Action': 'Pin'})
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'

    def test_ta_full_permissions(self, forge_client):
        username = 'admin'
        if not engine.User.objects(username=username).first():
            self.add_user(username, role=0)
        admin_user = engine.User.objects(username=username).first()
        admin_user.role = 0
        admin_user.save()

        student_client = forge_client('student')
        course = self._create_course_with_student()
        problem = self._create_problem_for_course(course)
        post_id = self._create_discussion_post(
            student_client,
            Problem_id=str(problem.problem_id),
            Title='Student Post',
            Content='Text',
        )

        try:
            admin_client = forge_client(username)
        except TypeError:
            admin_client = forge_client(username, 'admin')

        rv = admin_client.post(f'/discussion/posts/{post_id}/status',
                               json={'Action': 'Pin'})
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['New_Status'] == 'pinned'

        rv = admin_client.delete(f'/discussion/posts/{post_id}/delete',
                                 json={
                                     'Type': 'post',
                                     'Id': post_id,
                                 })
        resp = rv.get_json()
        assert rv.status_code == 200, resp

    def test_delete_post_student_self(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.delete(f'/discussion/posts/{post_id}/delete',
                           json={
                               'Type': 'post',
                               'Id': post_id,
                           })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Status'] == 'OK'
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        assert post.is_deleted is True
        rv_detail = client.get(f'/discussion/posts/{post_id}')
        assert rv_detail.status_code == 404

    def test_delete_post_student_not_owner(self, forge_client):
        teacher_client = forge_client('teacher')
        post_id = self._create_discussion_post(teacher_client)
        student_client = forge_client('student')
        rv = student_client.delete(f'/discussion/posts/{post_id}/delete',
                                   json={
                                       'Type': 'post',
                                       'Id': post_id,
                                   })
        resp = rv.get_json()
        assert rv.status_code == 403, resp
        assert resp['data']['Status'] == 'ERR'

    def test_delete_reply_teacher(self, forge_client):
        student_client = forge_client('student')
        post_id = self._create_discussion_post(student_client)
        reply_resp = student_client.post(f'/discussion/posts/{post_id}/reply',
                                         json={
                                             'Content': 'reply'
                                         }).get_json()
        reply_id = reply_resp['data']['Reply_ID']
        teacher_client = forge_client('teacher')
        rv = teacher_client.delete(f'/discussion/posts/{post_id}/delete',
                                   json={
                                       'Type': 'reply',
                                       'Id': reply_id,
                                   })
        resp = rv.get_json()
        assert rv.status_code == 200, resp
        assert resp['data']['Status'] == 'OK'
        reply = engine.DiscussionReply.objects(reply_id=reply_id).first()
        assert reply.is_deleted is True
        post = engine.DiscussionPost.objects(post_id=post_id).first()
        assert post.reply_count == 0

    def test_delete_invalid_type(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.delete(f'/discussion/posts/{post_id}/delete',
                           json={
                               'Type': 'unknown',
                               'Id': post_id,
                           })
        resp = rv.get_json()
        assert rv.status_code == 400, resp
        assert resp['data']['Status'] == 'ERR'

    def test_delete_reply_not_found(self, forge_client):
        client = forge_client('student')
        post_id = self._create_discussion_post(client)
        rv = client.delete(f'/discussion/posts/{post_id}/delete',
                           json={
                               'Type': 'reply',
                               'Id': 9999,
                           })
        resp = rv.get_json()
        assert rv.status_code == 404, resp
        assert resp['data']['Status'] == 'ERR'
