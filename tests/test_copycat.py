import os
import pathlib
import pytest
from model import *
from mongo import engine, Problem, User
from tests.base_tester import BaseTester
from tests import utils

S_NAMES = {
    'student': 'Chika.Fujiwara',  # base.c base.py
    'student-2': 'Nico.Kurosawa',  # base.cpp base_2.py
}

skip_no_moss_id = pytest.mark.skipif(
    os.environ.get('MOSS_USERID') is None,
    reason="MOSS_USERID not set in environment")


class TestCopyCat(BaseTester):
    # user, course, problem, submission
    def test_copycat(self, forge_client, problem_ids, make_course, submit_once,
                     save_source, tmp_path):
        # 1. 建立課程 (定義 course_name)
        course_name = make_course(
            username="teacher",
            students=S_NAMES,
        ).name

        # 2. 建立題目 (定義 pid)
        pid = problem_ids("teacher", 1, True)[0]

        # 3. 準備原始碼檔案
        src_dir = pathlib.Path('tests/src')
        exts = ['.c', '.cpp', '.py', '.pdf']
        for src in src_dir.iterdir():
            if any([not src.suffix in exts, not src.is_file()]):
                continue
            save_source(
                src.stem,
                src.read_bytes(),
                exts.index(src.suffix),
            )

        # 4. 學生提交作業
        name2code = {
            'student': [('base.c', 0), ('base.py', 2)],
            'student-2': [('base.cpp', 1), ('base_2.py', 2)]
        }
        for name, code in name2code.items():
            for filename, language in code:
                submit_once(
                    name=name,
                    pid=pid,
                    filename=filename,
                    lang=language,
                )

        # 5. 修改提交狀態為 Accepted (status=0)
        engine.Submission.objects.update(status=0)

        # 6. 發送 POST 請求 (產生報告)
        client = forge_client('teacher')
        rv, rv_json, rv_data = self.request(
            client,
            'post',
            '/copycat',
            json={
                'course': course_name,
                'problemId': pid,
                'studentNicknames': {
                    'student': 'student',
                    'student-2': 'student-2',
                },
            },
        )
        assert rv.status_code == 200, rv_json

        # 7. 發送 GET 請求 (取得報告連結)
        # 這一步必須在上面所有步驟完成後才能執行
        client = forge_client('teacher')
        rv, rv_json, rv_data = self.request(
            client, 'get', f'/copycat?course={course_name}&problemId={pid}')

        assert rv.status_code == 200, rv_json
        assert isinstance(rv_data, dict), rv_data

    def test_get_report_before_request(self, client_admin, problem_ids):
        pid = problem_ids("teacher", 1, True)[0]
        rv = client_admin.get(f'/copycat?course=Public&problemId={pid}')
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json(
        )['message'] == 'No report found. Please make a post request to copycat api to generate a report'

    def test_get_report(self, client_admin, problem_ids, monkeypatch):
        from model import copycat

        def mock_get_report_by_url(_, count=[]):
            count.append(87)
            return f'this is a report url {len(count)}'

        monkeypatch.setattr(copycat, 'get_report_by_url',
                            mock_get_report_by_url)
        pid = problem_ids("teacher", 1, True)[0]
        problem = Problem(pid)
        problem.update(moss_status=2)
        rv = client_admin.get(f'/copycat?course=Public&problemId={pid}')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['data'] == {
            "cpp_report": 'this is a report url 1',
            "python_report": 'this is a report url 2'
        }

    def test_is_valid_url(self):
        from model.copycat import is_valid_url
        assert is_valid_url('https://example.com:8787/abc?def=1234&A_A=Q_Q')

    @skip_no_moss_id
    def test_get_report_task(self, monkeypatch, make_course, problem_ids,
                             save_source, submit_once):
        # create course
        course_name = make_course(
            username="teacher",
            students=S_NAMES,
        ).name
        # create problem
        pid = problem_ids("teacher", 1, True)[0]
        # save source code (for submit_once)
        src_dir = pathlib.Path('tests/src')
        exts = ['.c', '.cpp', '.py', '.pdf']
        for src in src_dir.iterdir():
            if any([not src.suffix in exts, not src.is_file()]):
                continue
            save_source(
                src.stem,
                src.read_bytes(),
                exts.index(src.suffix),
            )
        # submission
        name2code = {
            'student': [('base.c', 0), ('base.py', 2)],
            'student-2': [('base.cpp', 1), ('base_2.py', 2)]
        }
        for name, code in name2code.items():
            for filename, language in code:
                submit_once(
                    name=name,
                    pid=pid,
                    filename=filename,
                    lang=language,
                )
        # change all submissions to status 0 (Accepted)
        engine.Submission.objects.update(status=0)
        user = User('teacher')
        from model.copycat import mosspy

        def mock_moss_send(self):
            return f'https://mock.moss/{self.options["l"]}'

        monkeypatch.setattr(mosspy.Moss, 'send', mock_moss_send)

        def mock_moss_download_report(*args, **kwargs):
            pass

        monkeypatch.setattr(mosspy, 'download_report',
                            mock_moss_download_report)

        monkeypatch.setenv('MOSS_USERID', '123')
        from model.copycat import get_report_task
        get_report_task(user, pid, S_NAMES)
        problem = Problem(pid)
        assert problem.moss_status == 2
        assert problem.cpp_report_url == 'https://mock.moss/cc'
        assert problem.python_report_url == 'https://mock.moss/python'

    @skip_no_moss_id
    def test_get_report_task_with_invail_url(
        self,
        monkeypatch,
        problem_ids,
        app,
    ):
        pid = problem_ids("teacher", 1, True)[0]
        user = User('teacher')
        from model.copycat import mosspy

        def mock_moss_send(self):
            return 'invalid://example.com/'

        monkeypatch.setattr(mosspy.Moss, 'send', mock_moss_send)
        monkeypatch.setenv('MOSS_USERID', '123')
        from model.copycat import get_report_task
        get_report_task(user, pid, S_NAMES)
        problem = Problem(pid)
        assert problem.moss_status == 2
        assert problem.cpp_report_url == ''
        assert problem.python_report_url == ''

    def test_get_report_by_url(self, monkeypatch):
        from model.copycat import requests

        class mock_requests_get:

            def __init__(self, text):
                self.text = text

        monkeypatch.setattr(requests, 'get', mock_requests_get)
        from model.copycat import get_report_by_url
        url = 'https://example.com:8787/abc?def=1234&A_A=Q_Q'
        assert get_report_by_url(url) == url

    def test_get_report_by_url_with_invalid_schema(self, monkeypatch):
        from model.copycat import requests

        def mock_requests_get(_):
            raise requests.exceptions.InvalidSchema

        monkeypatch.setattr(requests, 'get', mock_requests_get)
        from model.copycat import get_report_by_url
        url = 'https://example.com:8787/abc?def=1234&A_A=Q_Q'
        assert get_report_by_url(url) == 'No report.'
