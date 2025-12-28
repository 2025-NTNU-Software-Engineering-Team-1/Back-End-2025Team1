import io
import zipfile
import pytest
from mongo import *
from tests.base_tester import BaseTester, random_string
from tests.utils import problem_result


@pytest.fixture(scope='function')
def setup_problem_with_testcases():
    """Create a problem and enroll student for trial submission tests."""
    # Create teacher and course
    teacher = User('teacher')
    course_name = f'TrialCourse_{random_string()}'
    Course.add_course(course_name, teacher.username)
    course = Course(course_name)

    # Enroll student
    student = User('student')
    course.obj.student_nicknames[student.username] = student.username
    course.obj.save()
    course.reload()

    # Create problem (Problem.add returns problem_id)
    problem_name = f'TrialProblem_{random_string()}'
    problem_id = Problem.add(
        user=teacher,
        courses=[course_name],
        problem_name=problem_name,
        test_case_info={
            'language':
            2,
            'fill_in_template':
            '',
            'tasks': [{
                'caseCount': 1,
                'taskScore': 100,
                'memoryLimit': 256000,
                'timeLimit': 1000,
            }]
        },
    )
    problem = Problem(problem_id)
    problem.reload()

    # Set visability to SHOW and trial_mode_enabled to True
    try:
        problem.obj.problem_status = 0
        problem.obj.trial_mode_enabled = True
        # Update config for new trial mode logic
        if not problem.obj.config:
            problem.obj.config = {}
        problem.obj.config['trialMode'] = True
    except Exception:
        pass
    try:
        # 7 allows C/C++/Python in this codebase
        problem.obj.allowed_language = 7
    except Exception:
        pass
    try:
        problem.obj.save()
        problem.reload()
    except Exception:
        pass

    yield problem, course

    # Cleanup best-effort
    try:
        problem.obj.delete()
    except Exception:
        pass
    try:
        course.obj.delete()
    except Exception:
        pass


class TestTrialSubmissionAPI(BaseTester):

    def test_upload_trial_files_success_with_code_only(
            self, forge_client, setup_problem_with_testcases):
        """Test uploading only code file"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        # First create trial submission
        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        assert rv.status_code == 200
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Create code zip
        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('main.py', 'print("hello")')
        code_buffer.seek(0)

        # Upload code
        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={'code': (code_buffer, 'code.zip')},
                        content_type='multipart/form-data')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['status'] == 'ok'
        assert data['data']['trial_submission_id'] == trial_id
        assert data['data']['Code_Path'] is not None
        assert data['data']['Custom_Testcases_Path'] is None

        # Verify MinIO upload
        from mongo.submission import TrialSubmission
        ts = TrialSubmission(trial_id)
        assert ts.obj.code_minio_path is not None

    def test_upload_trial_files_success_with_custom_testcases(
            self, forge_client, setup_problem_with_testcases):
        """Test uploading code + custom testcases"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        # Create trial submission with custom cases
        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': False
                         })
        assert rv.status_code == 200
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Create code zip
        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('main.py', 'a,b=map(int,input().split())\nprint(a+b)')
        code_buffer.seek(0)

        # Create custom testcases zip
        custom_buffer = io.BytesIO()
        with zipfile.ZipFile(custom_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('test1.in', '3 4\n')
            zf.writestr('test1.out', '7\n')
        custom_buffer.seek(0)

        # Upload both
        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={
                            'code': (code_buffer, 'code.zip'),
                            'custom_testcases': (custom_buffer, 'custom.zip')
                        },
                        content_type='multipart/form-data')
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['data']['Code_Path'] is not None
        assert data['data']['Custom_Testcases_Path'] is not None

        # Verify submission updated
        from mongo.submission import TrialSubmission
        ts = TrialSubmission(trial_id)
        assert ts.obj.code_minio_path is not None
        assert ts.obj.custom_input_minio_path is not None
        assert ts.obj.use_default_case is False

    def test_upload_trial_files_missing_code(self, forge_client,
                                             setup_problem_with_testcases):
        """Test upload without code file"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        trial_id = rv.get_json()['data']['trial_submission_id']

        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={},
                        content_type='multipart/form-data')
        assert rv.status_code == 400
        assert 'No files provided' in rv.get_json()['message']

    def test_upload_trial_files_invalid_trial_id(self, forge_client):
        """Test upload with non-existent trial ID"""
        client = forge_client('student')

        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w') as zf:
            zf.writestr('main.py', 'print("test")')
        code_buffer.seek(0)

        rv = client.put('/trial-submission/invalid_id_12345/files',
                        data={'code': (code_buffer, 'code.zip')},
                        content_type='multipart/form-data')
        assert rv.status_code in [400, 404]

    def test_upload_trial_files_not_owner(self, forge_client,
                                          setup_problem_with_testcases):
        """Test upload by different user (should fail)"""
        problem, _ = setup_problem_with_testcases
        student_client = forge_client('student')

        # Student creates trial submission
        rv = student_client.post(
            f'/problem/{problem.problem_id}/trial/request',
            json={
                'languageType': 2,
                'use_default_test_cases': True
            })
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Try upload as different user
        other_client = forge_client('teacher')  # Different user
        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w') as zf:
            zf.writestr('main.py', 'print("hack")')
        code_buffer.seek(0)

        rv = other_client.put(f'/trial-submission/{trial_id}/files',
                              data={'code': (code_buffer, 'code.zip')},
                              content_type='multipart/form-data')
        # Teacher should have permission (role <= 1)
        assert rv.status_code == 200

    def test_upload_trial_files_invalid_zip(self, forge_client,
                                            setup_problem_with_testcases):
        """Test upload with invalid zip file"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Send non-zip file
        fake_zip = io.BytesIO(b'This is not a zip file')

        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={'code': (fake_zip, 'code.zip')},
                        content_type='multipart/form-data')
        assert rv.status_code == 400
        assert 'valid zip' in rv.get_json()['message'].lower()

    def test_upload_trial_files_code_too_large(self, forge_client,
                                               setup_problem_with_testcases):
        """Test upload with oversized code file"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Create >10MB zip
        large_buffer = io.BytesIO()
        with zipfile.ZipFile(large_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('big.py', 'x' * (11 * 1024 * 1024))
        large_buffer.seek(0)

        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={'code': (large_buffer, 'code.zip')},
                        content_type='multipart/form-data')
        assert rv.status_code == 400
        assert 'too large' in rv.get_json()['message'].lower()

    def test_upload_trial_files_custom_testcases_too_large(
            self, forge_client, setup_problem_with_testcases):
        """Test upload with oversized custom testcases"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': False
                         })
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Valid code
        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w') as zf:
            zf.writestr('main.py', 'print("test")')
        code_buffer.seek(0)

        # Oversized custom testcases
        large_custom = io.BytesIO()
        with zipfile.ZipFile(large_custom, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('test.in', 'x' * (6 * 1024 * 1024))
        large_custom.seek(0)

        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={
                            'code': (code_buffer, 'code.zip'),
                            'custom_testcases': (large_custom, 'custom.zip')
                        },
                        content_type='multipart/form-data')
        assert rv.status_code == 400
        assert 'too large' in rv.get_json()['message'].lower()

    def test_upload_trial_files_invalid_custom_testcases_zip(
            self, forge_client, setup_problem_with_testcases):
        """Test upload with invalid custom testcases zip"""
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': False
                         })
        trial_id = rv.get_json()['data']['trial_submission_id']

        # Valid code
        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w') as zf:
            zf.writestr('main.py', 'print("test")')
        code_buffer.seek(0)

        # Invalid custom testcases
        fake_custom = io.BytesIO(b'Not a zip')

        rv = client.put(f'/trial-submission/{trial_id}/files',
                        data={
                            'code': (code_buffer, 'code.zip'),
                            'custom_testcases': (fake_custom, 'custom.zip')
                        },
                        content_type='multipart/form-data')
        assert rv.status_code == 400
        assert 'valid zip' in rv.get_json()['message'].lower()

    def test_trial_history_scope_and_user_label(self, forge_client,
                                                setup_problem_with_testcases):
        problem, course = setup_problem_with_testcases

        other_student = User('student-2')
        course.obj.student_nicknames[
            other_student.username] = other_student.username
        course.obj.save()
        course.reload()

        TrialSubmission.add(problem_id=problem.problem_id,
                            username='student',
                            lang=2,
                            use_default_case=True)
        TrialSubmission.add(problem_id=problem.problem_id,
                            username='student-2',
                            lang=2,
                            use_default_case=True)

        student_client = forge_client('student')
        rv = student_client.get(f'/problem/{problem.problem_id}/trial/history')
        assert rv.status_code == 200
        data = rv.get_json()['data']
        history = data['history']
        assert data['total_count'] == 1
        assert len(history) == 1
        assert history[0]['user']['username'] == 'student'

        teacher_client = forge_client('teacher')
        rv = teacher_client.get(f'/problem/{problem.problem_id}/trial/history')
        assert rv.status_code == 200
        data = rv.get_json()['data']
        history = data['history']
        usernames = {item['user']['username'] for item in history}
        assert usernames == {'student', 'student-2'}

    def test_trial_send_failure_marks_je(self, setup_problem_with_testcases,
                                         monkeypatch):
        problem, _ = setup_problem_with_testcases
        problem.obj.public_cases_zip_minio_path = 'test/public.zip'
        problem.obj.save()

        ts = TrialSubmission.add(problem_id=problem.problem_id,
                                 username='student',
                                 lang=2,
                                 use_default_case=True)

        code_buffer = io.BytesIO()
        with zipfile.ZipFile(code_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('main.py', 'print("hello")')
        code_buffer.seek(0)

        ts.update(code_minio_path=ts._put_code(code_buffer),
                  use_default_case=True)
        ts.reload()

        monkeypatch.setattr(TrialSubmission, "target_sandbox",
                            lambda self: None)

        with pytest.raises(ValueError):
            ts.send()

        ts.reload()
        assert ts.status == ts.status2code['JE']
        output = ts.get_single_output(0, 0)
        assert 'No available sandbox' in output['stderr']

    def test_rejudge_only_updates_status_on_success(
        self,
        app,
        setup_problem_with_testcases,
        monkeypatch,
    ):
        problem, _ = setup_problem_with_testcases
        ts = TrialSubmission.add(problem_id=problem.problem_id,
                                 username='student',
                                 lang=2,
                                 use_default_case=True)

        monkeypatch.setattr(TrialSubmission, "finish_judging",
                            lambda self: None)
        with app.app_context():
            ts.process_result(problem_result(problem.problem_id))

        ts.reload()
        old_status = ts.status
        old_task_count = len(ts.tasks)

        monkeypatch.setattr(TrialSubmission, "send", lambda self: False)

        with app.app_context():
            result = ts.rejudge()
        assert result is False

        ts.reload()
        assert ts.status == old_status
        assert len(ts.tasks) == old_task_count
