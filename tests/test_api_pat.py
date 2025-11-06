from mongo import engine, User
from tests.base_tester import BaseTester
from tests import utils
import secrets
from datetime import datetime


class TestAPIUserIPs(BaseTester):
    """Test /pat/userips/<course_name> endpoint"""

    def test_export_ip_records_permission_denied(self, client):
        """
        測試沒有 'read:userips' 權限的學生 token 是否會被拒絕。
        """
        student_user = utils.user.create_user(role=engine.User.Role.STUDENT)
        course = utils.course.create_course(students=[student_user])

        from model.utils.pat import add_pat_to_database, hash_pat_token

        token_string = 'noj_pat_test_invalid_scope_token_12345'
        pat_id = secrets.token_hex(8)

        add_pat_to_database(
            pat_id=pat_id,
            name='test_token',
            owner=student_user.username,
            hash_val=hash_pat_token(token_string),
            scope=['read:self'],
            due_time=None,
        )

        res = client.get(
            f'/pat/userips/{course.course_name}',
            headers={'Authorization': f'Bearer {token_string}'},
        )
        assert res.status_code == 403

    def test_export_ip_records_success_with_pat_route(self, app,
                                                      client_teacher, client):
        """
        測試透過 API 建立 PAT，並用其成功下載 CSV。
        """
        scope_to_request = ['read:userips']
        rv = client_teacher.post(
            '/profile/api_token/create',
            json={
                'Name': 'export_success_token',
                'Due_Time': None,
                'Scope': scope_to_request,
            },
        )
        assert rv.status_code == 200
        token = rv.get_json()['data']['Token']
        assert token.startswith('noj_pat_')

        student_user = utils.user.create_user(role=engine.User.Role.STUDENT)
        teacher_user = User('teacher')
        course = utils.course.create_course(teacher=teacher_user,
                                            students=[student_user])

        # 偽造 LoginRecords（注意 user_id 用 username 字串）
        engine.LoginRecords(
            user_id=student_user.username,
            ip_addr='192.168.1.1',
            success=True,
            timestamp=datetime.now(),
        ).save()

        # 建立 problem 並偽造 Submission（最小必填欄位）
        problem = utils.problem.create_problem(owner=teacher_user,
                                               course=course.course_name)
        with app.app_context():
            utils.submission.create_submission(
                user=student_user,
                problem=problem,
                ip_addr='192.168.1.3',
            )

        res = client.get(
            f'/pat/userips/{course.course_name}',
            headers={'Authorization': f'Bearer {token}'},
        )

        assert res.status_code == 200
        assert res.content_type.startswith('text/csv')
        csv_content = res.data.decode('utf-8')
        print(
            f"\n===== CSV (/api/userips/{course.course_name}) =====\n{csv_content}\n===== END CSV =====\n"
        )
        assert 'Login' in csv_content or 'Submission' in csv_content
        assert student_user.username in csv_content
        assert '192.168.1.1' in csv_content or '192.168.1.3' in csv_content

    def test_export_ip_records_course_not_found(self, client_teacher, client):
        """
        測試課程不存在時的錯誤處理
        """
        rv = client_teacher.post(
            '/profile/api_token/create',
            json={
                'Name': 'test_token',
                'Due_Time': None,
                'Scope': ['read:userips'],
            },
        )
        assert rv.status_code == 200
        token = rv.get_json()['data']['Token']

        res = client.get(
            '/pat/userips/NonExistentCourse',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert res.status_code == 404
        assert res.get_json()['message'] == 'Course not found.'

    def test_export_ip_records_empty_course(self, client_teacher, client):
        """
        測試空課程（沒有學生）的 IP 記錄導出
        """
        rv = client_teacher.post(
            '/profile/api_token/create',
            json={
                'Name': 'test_token_empty',
                'Due_Time': None,
                'Scope': ['read:userips'],
            },
        )
        assert rv.status_code == 200
        token = rv.get_json()['data']['Token']

        teacher_user = User('teacher')
        course = utils.course.create_course(teacher=teacher_user, students=[])

        res = client.get(
            f'/pat/userips/{course.course_name}',
            headers={'Authorization': f'Bearer {token}'},
        )

        assert res.status_code == 200
        assert res.content_type.startswith('text/csv')

        csv_content = res.data.decode('utf-8')
        lines = csv_content.strip().split('\n')
        assert len(lines) == 1
        assert 'Type' in lines[0] and 'Username' in lines[0]
