import io
from zipfile import ZipFile
from typing import Optional
import pytest
import itertools
import pathlib
import io
import zipfile
import inspect
from datetime import datetime, timedelta
from pprint import pprint
from mongo import *
from mongo import engine
from mongo.utils import MinioClient
from .base_tester import BaseTester
from .utils import *
from tests import utils

pytestmark = pytest.mark.usefixtures("setup_minio")

A_NAMES = [
    'teacher',
    'admin',
    'teacher-2',
]
S_NAMES = {
    'student': 'Chika.Fujiwara',
    'student-2': 'Nico.Kurosawa',
}


@pytest.fixture(autouse=True)
def submission_testcase_setup(
    save_source,
    make_course,
):
    BaseTester.setup_class()
    # save base source
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
    # create courses
    for name in A_NAMES:
        make_course(
            username=name,
            students=S_NAMES,
        )
    yield
    BaseTester.teardown_class()


@pytest.mark.usefixtures("setup_minio")
class SubmissionTester:
    # all submission count
    init_submission_count = 8
    submissions = []


@pytest.fixture
def zip_problem(problem_ids):
    pid = problem_ids('teacher', 1, True)[0]
    prob = Problem(pid)
    prob.update(config__acceptedFormat='zip')
    prob.reload('config')
    return pid


def _write_zip(path: pathlib.Path, files: dict[str, str]):
    with zipfile.ZipFile(path, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def _zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


class FakeSizedBytesIO:

    def __init__(self, data: bytes, fake_size: int):
        self._buf = io.BytesIO(data)
        self._fake_size = fake_size
        self._last_seek_fake = False

    def _should_fake(self):
        for frame in inspect.stack():
            if frame.function == '_check_zip_submission_payload':
                return True
        return False

    def read(self, *args, **kwargs):
        return self._buf.read(*args, **kwargs)

    def readinto(self, b):
        return self._buf.readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET):
        if whence == io.SEEK_END and offset == 0 and self._should_fake():
            self._buf.seek(0, io.SEEK_END)
            self._last_seek_fake = True
            return self._fake_size
        res = self._buf.seek(offset, whence)
        self._last_seek_fake = False
        return res

    def tell(self) -> int:
        if self._last_seek_fake:
            return self._fake_size
        return self._buf.tell()

    def readable(self):
        return True

    def seekable(self):
        return True

    def close(self):
        self._buf.close()


class TestUserGetSubmission(SubmissionTester):

    @classmethod
    @pytest.fixture(autouse=True)
    def on_create(cls, submit, problem_ids):
        # create 2 problem for each teacher or admin
        pids = [problem_ids(name, 2, True) for name in A_NAMES]
        pids = itertools.chain(*pids)
        # get online problem ids
        pids = [pid for pid in pids if Problem(pid).problem_status == 0]
        # get a course name
        cls.courses = [Problem(pid).courses[0].course_name for pid in pids]
        pids = itertools.cycle(pids)
        names = itertools.cycle(S_NAMES.keys())
        # create submissions
        cls.submissions = submit(
            names,
            pids,
            cls.init_submission_count,
        )
        # check submission count
        assert len([*itertools.chain(*cls.submissions.values())
                    ]) == cls.init_submission_count, cls.submissions
        yield
        # clear
        cls.submissions = []

    def test_normal_get_submission_list(self, forge_client):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission?offset=0&count={self.init_submission_count}',
        )
        assert rv.status_code == 200, rv_json
        assert 'submissionCount' in rv_data
        assert len(rv_data['submissions']) == self.init_submission_count // 2
        excepted_field_names = {
            'submissionId',
            'problemId',
            'user',
            'status',
            'score',
            'runTime',
            'memoryUsage',
            'languageType',
            'timestamp',
            'lastSend',
            'ipAddr',
        }
        for s in rv_data['submissions']:
            assert len(excepted_field_names - set(s.keys())) == 0

    @pytest.mark.parametrize('offset, count', [
        (0, 1),
        (SubmissionTester.init_submission_count // 4, 1),
    ])
    def test_get_truncated_submission_list(self, forge_client, offset, count):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset={offset}&count={count}',
        )
        assert rv.status_code == 200, rv_json
        assert len(rv_data['submissions']) == 1

    def test_get_submission_list_with_maximun_offset(self, forge_client):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset={self.init_submission_count}&count=1',
        )
        assert rv.status_code == 200, rv_json
        assert len(rv_data['submissions']) == 0, rv_data

    def test_get_all_submission(self, forge_client):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            '/submission/?offset=0&count=-1',
        )

        assert rv.status_code == 200, rv_json
        # only get online submissions
        assert len(rv_data['submissions']) == self.init_submission_count // 2

        offset = self.init_submission_count // 2
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset={offset}&count=-1',
        )

        assert rv.status_code == 200, rv_json
        assert len(
            rv_data['submissions']) == (self.init_submission_count // 2 -
                                        offset)

    def test_get_submission_list_over_db_size(self, forge_client):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset=0&count={self.init_submission_count ** 2}',
        )

        assert rv.status_code == 200, rv_json
        assert len(rv_data['submissions']) == self.init_submission_count // 2

    def test_get_submission_without_login(self, client):
        for _id in self.submissions.values():
            rv = client.get(f'/submission/{_id}')
            pprint(rv.get_json())
            assert rv.status_code == 403, client.cookie_jar

    def test_normal_user_get_others_submission(self, forge_client):
        '''
        let student get all other's submission
        '''
        ids = []
        for name in (set(S_NAMES) - set(['student'])):
            ids.extend(self.submissions[name])

        client = forge_client('student')
        for _id in ids:
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'get',
                f'/submission/{_id}',
            )
            assert 'code' not in rv_data, Submission(_id).user.username
            assert rv.status_code == 200

    def test_get_self_submission(self, client_student):
        ids = self.submissions['student']
        assert len(ids) != 0

        for _id in ids:
            rv, _, rv_data = BaseTester.request(
                client_student,
                'get',
                f'/submission/{_id}',
            )
            assert rv.status_code == 200

            # check for fields
            except_fields = {
                'problemId',
                'languageType',
                'timestamp',
                'status',
                'tasks',
                'score',
                'runTime',
                'memoryUsage',
                'code',
                'ipAddr',
            }
            missing_field = except_fields - set(rv_data.keys())
            assert len(missing_field) == 0, missing_field

    @pytest.mark.parametrize('offset, count', [(-1, 2), (2, -2)])
    def test_get_submission_list_with_out_ranged_negative_arg(
        self,
        forge_client,
        offset,
        count,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset={offset}&count={count}',
        )
        assert rv.status_code == 400

    @pytest.mark.parametrize(
        'key, except_val',
        [
            ('status', -1),
            ('languageType', 0),
            # TODO: need special test for username field
            # TODO: test for problem id filter
        ])
    def test_get_submission_list_by_filter(
        self,
        forge_client,
        key,
        except_val,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset=0&count=-1&{key}={except_val}',
        )

        assert rv.status_code == 200, rv_json
        assert len(
            rv_data['submissions']) != 0, engine.Submission.objects.to_json()
        assert all(map(lambda x: x[key] == except_val,
                       rv_data['submissions'])) == True

    def test_get_submission_list_by_course_filter(
        self,
        forge_client,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset=0&count=-1&course=aaa',
        )
        # No submissions found cause "aaa" doesn't exist
        assert rv.status_code == 200, rv.get_json()
        assert len(rv_data['submissions']) == 0
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/?offset=0&count=-1&course={self.courses[0]}',
        )
        assert rv.status_code == 200
        assert len(rv_data['submissions']) == 2

    def test_user_get_high_score(
        self,
        forge_client,
        submit_once,
    ):
        # get all problems that user can view
        pids = [
            p.problem_id for p in Problem.get_problem_list(User('student'))
        ]
        assert len(pids) != 0
        pid = pids[0]
        # get current high score
        rv, rv_json, rv_data = BaseTester.request(
            forge_client('student'),
            'get',
            f'/problem/{pid}/high-score',
        )
        assert rv.status_code == 200, rv_json
        assert rv_data['score'] == 0, [*engine.Submission.objects]
        # create a new handwritten submission
        submission_id = submit_once(
            name='student',
            pid=pid,
            filename='main.pdf',
            lang=3,
        )
        for score in (100, 87, 60):
            # modify this submission's score
            rv, rv_json, rv_data = BaseTester.request(
                forge_client('teacher'),
                'put',
                f'/submission/{submission_id}/grade',
                json={'score': score},
            )
            assert rv.status_code == 200, rv_json
            # check the high score again
            rv, rv_json, rv_data = BaseTester.request(
                forge_client('student'),
                'get',
                f'/problem/{pid}/high-score',
            )
            assert rv.status_code == 200, rv_json
            assert rv_data['score'] == score, [*engine.Submission.objects]

    def test_user_get_submission_cache(
        self,
        submit_once,
    ):
        # get one pid that student can submit
        pid = Problem.get_problem_list(User('student'))[0].problem_id
        # create a submission and read the result
        submission_id = submit_once(
            name='student',
            pid=pid,
            filename='base.c',
            lang=0,
        )
        submission_result = Submission(submission_id).to_dict()
        assert submission_result['status'] == -1, submission_result
        # forge fake submission result
        problem = Problem(pid).obj
        assert problem
        case_result = {
            'exitCode': 0,
            'status': 'WA',
            'stdout': '',
            'stderr': '',
            'execTime': 87,
            'memoryUsage': 87,
        }
        fake_results = [[case_result] * task.case_count
                        for task in problem.test_case.tasks]
        # simulate judging and see whether the result is updated
        Submission(submission_id).process_result(fake_results)
        submission_result = Submission(submission_id).to_dict()
        assert submission_result['status'] == 1, submission_result


class TestTeacherGetSubmission(SubmissionTester):
    pids = []

    @classmethod
    @pytest.fixture(autouse=True)
    def on_create(cls, problem_ids, submit):
        # make submissions
        cls.pids = []
        for name in A_NAMES:
            cls.pids.extend(problem_ids(name, 3, True, -1))
        names = itertools.cycle(['admin'])
        submit(
            names,
            itertools.cycle(cls.pids),
            cls.init_submission_count,
        )

    def test_teacher_can_get_offline_submission(self, forge_client):
        client = forge_client('teacher')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            '/submission?offset=0&count=-1',
        )
        except_count = len(engine.Submission.objects)
        assert len(rv_data['submissions']) == except_count, rv_json

    def test_teacher_can_view_students_source(self, forge_client):
        teacher_name = 'teacher'
        client = forge_client(teacher_name)
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            '/submission?offset=0&count=-1',
        )

        problems = [Problem(pid).obj for pid in self.pids]
        problems = {p.problem_id for p in problems if p.owner == teacher_name}
        submission_ids = [
            s['submissionId'] for s in rv_data['submissions']
            if s['problemId'] in problems
        ]

        for _id in submission_ids:
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'get',
                f'/submission/{_id}',
            )
            assert 'code' in rv_data, rv_data


class TestCreateSubmission(SubmissionTester):
    pid = None

    @classmethod
    @pytest.fixture(autouse=True)
    def on_create(cls, problem_ids):
        cls.pid = problem_ids('teacher', 1, True)[0]
        yield
        cls.pid = None

    def post_payload(
        self,
        language: int = 0,
        problem_id: Optional[int] = None,
    ):
        return {
            'problemId': problem_id or self.pid,
            'languageType': language,
        }

    @pytest.mark.parametrize(
        'lang, ext',
        zip(
            range(3),
            ['.c', '.cpp', '.py'],
        ),
    )
    def test_normal_submission(
        self,
        forge_client,
        get_source,
        lang,
        ext,
    ):
        client = forge_client('student')
        # first claim a new submission to backend server
        # recieve response, which include the submission id
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(lang),
        )
        assert rv.status_code == 200, rv_json
        assert 'submissionId' in rv_data, rv_data
        # second, post my source code to server. after that,
        # my submission will send to sandbox to be judged
        files = {
            'code': (
                get_source(f'base{ext}'),
                'code',
            )
        }
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        rv_json = rv.get_json()
        assert rv.status_code == 200, rv_json

    def test_user_db_submission_field_content(
        self,
        forge_client,
    ):
        # get submission length
        before_len = len(User('student').submissions)
        # create a submission
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(),
        )

        # get user's data
        user = User('student')
        pprint(user.to_mongo())
        pprint(rv_json)

        assert user
        assert rv.status_code == 200
        assert len(user.submissions) == before_len + 1

    def test_wrong_language_type(
        self,
        forge_client,
        get_source,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(2),  # 2 for py3
        )
        files = {
            'code': (
                get_source('base.c'),
                'code',
            )
        }
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        rv_json = rv.get_json()
        # file extension doesn't equal we claimed before
        assert rv.status_code == 400, rv_json

    def test_wrong_file_type(self, forge_client, get_source, problem_ids):
        pid = problem_ids('teacher', 1, True, 0, 2)[0]
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(3, pid),
        )
        files = {
            'code': (
                get_source('main2.pdf'),
                'code',
            )
        }
        print(rv_json)
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        rv_json = rv.get_json()
        # file is not PDF
        assert rv.status_code == 400, rv_json

    def test_empty_source(
        self,
        forge_client,
        get_source,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(),
        )

        files = {'code': (None, 'code')}
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        rv_json = rv.get_json()

        assert rv.status_code == 400, rv_json

    @pytest.mark.parametrize(
        'lang, ext',
        zip(
            range(3),
            ['.c', '.cpp', '.py'],
        ),
    )
    def test_no_source_upload(
        self,
        forge_client,
        lang,
        ext,
        get_source,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(),
        )
        assert rv.status_code == 200, rv_data
        files = {'c0d3': (get_source(f'base{ext}'), 'code')}
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        assert rv.status_code == 400, rv_data

    def test_submit_to_others(
        self,
        forge_client,
        get_source,
    ):
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(),
        )
        assert rv.status_code == 200, rv_json

        client = forge_client('student-2')
        submission_id = rv_data['submissionId']
        files = {
            'code': (
                get_source('base.cpp'),
                'd1w5q6dqw',
            )
        }
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'put',
            f'/submission/{submission_id}',
            data=files,
        )

        assert rv.status_code == 403, rv_json

    def test_function_only_zip_mode_conflict(
        self,
        forge_client,
        get_source,
        problem_ids,
    ):
        pid = problem_ids('teacher', 1, True)[0]
        prob = Problem(pid)
        prob.update(config__acceptedFormat='zip')
        prob.reload('config')
        prob.update(config__executionMode='functionOnly')
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            '/submission',
            json=self.post_payload(problem_id=pid),
        )
        files = {
            'code': (
                get_source('base.c'),
                'code',
            )
        }
        rv = client.put(
            f'/submission/{rv_data["submissionId"]}',
            data=files,
        )
        assert rv.status_code == 400, rv.get_json()

    def test_reach_rate_limit(self, client_student):
        # set rate limit to 5 sec
        Submission.config().update(rate_limit=5)
        post_json = self.post_payload(1)
        client_student.post(
            '/submission',
            json=post_json,
        )

        for _ in range(10):
            rv = client_student.post(
                '/submission',
                json=post_json,
            )

            assert rv.status_code == 429, rv.get_json()
        # recover rate limit
        Submission.config().update(rate_limit=0)

    @pytest.mark.parametrize(
        'user, response',
        [('student', 403), ('teacher', 200)],
    )
    def test_reach_quota(self, problem_ids, forge_client, user, response):
        pid = problem_ids('teacher', 1, True, 0, 0, 10)[0]
        post_json = self.post_payload(0, pid)
        client = forge_client(user)

        for i in range(10):
            rv = client.post(
                '/submission',
                json=post_json,
            )
            assert rv.status_code == 200, (i, rv.get_json())

        rv = client.get(f'/problem/view/{pid}')
        assert rv.status_code == 200
        assert rv.get_json()['data']['submitCount'] == 10

        rv = client.post(
            '/submission',
            json=post_json,
        )
        assert rv.status_code == response, rv.get_json()

    def test_normally_rejudge(self, forge_client, submit_once):
        submission_id = submit_once('student', self.pid, 'base.c', 0)
        client = forge_client('admin')
        # rejudge it many times
        for _ in range(5):
            # make a fake finish submission
            Submission(submission_id).process_result(problem_result(self.pid))
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'get',
                f'/submission/{submission_id}/rejudge',
            )
            assert rv.status_code == 200, rv_json

    def test_reach_file_size_limit(
        self,
        forge_client,
        save_source,
        get_source,
    ):
        save_source('big', b'%PDF-' + b'a' * (10**7) + b'<(_ _)>', 0)
        client = forge_client('student')

        rv, rv_json, rv_data = BaseTester.request(
            client,
            'post',
            f'/submission',
            json=self.post_payload(),
        )
        submission_id = rv_data['submissionId']
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'put',
            f'/submission/{submission_id}',
            data={
                'code': (
                    get_source('big.c'),
                    'aaaaa',
                ),
            },
        )
        print(rv_json)
        assert rv.status_code == 400

    def test_submission_main_code_path(
        self,
        submit_once,
        forge_client,
    ):
        s = Submission(
            submit_once(
                name='student',
                pid=self.pid,
                filename='base.c',
                lang=0,
            ))
        assert bool(s)
        s_code = open(s.main_code_path()).read()
        code = open('tests/src/base.c').read()

        def clean_code_for_compare(text):
            # Remove all newline symbol (\r, \n) and Tab characters
            # then remove all extra spaces
            return "".join(text.split()).strip()

        code = clean_code_for_compare(code)
        s_code = clean_code_for_compare(s_code)

        assert code == s_code, (s.main_code_path(), s_code)

    def test_reupload_code_should_fail(
        self,
        submit_once,
        forge_client,
        get_source,
    ):
        submission_id = submit_once(
            name='student',
            pid=self.pid,
            filename='base.c',
            lang=0,
        )
        client = forge_client('student')
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'put',
            f'/submission/{submission_id}',
            data={
                'code': (
                    get_source('base.c'),
                    'code',
                ),
            },
        )
        assert rv.status_code == 403, rv_json
        assert 'has been uploaded' in str(rv_json['message'])


@pytest.mark.skip(reason='handwritten submissions will be deprecated')
class TestHandwrittenSubmission(SubmissionTester):
    pid = None

    @classmethod
    @pytest.fixture(autouse=True)
    def on_create(cls, problem_ids):
        cls.pid = problem_ids('teacher', 1, True, 0, 2)[0]
        yield
        cls.pid = None

    @property
    def comment_paths(self):
        return itertools.cycle([
            'tests/handwritten/comment.pdf',
            'tests/handwritten/main.pdf',
        ])

    def comment(self, p):
        '''
        get a comment to upload

        Args:
            p: the comment file path
        '''
        return {
            'comment': (
                open(p, 'rb'),
                'comment.pdf',
            ),
        }

    def test_handwritten_submission(self, client_student, client_teacher):
        # first claim a new submission to backend server
        post_json = {'problemId': self.pid, 'languageType': 3}
        # recieve response, which include the submission id
        # and a token to validate next request
        rv, rv_json, rv_data = BaseTester.request(
            client_student,
            'post',
            '/submission',
            json=post_json,
        )
        assert rv.status_code == 200, rv_json
        assert sorted(rv_data.keys()) == sorted(['submissionId'])
        self.submission_id = rv_data["submissionId"]

        # second, post my homework to server. after that,
        # my submission will be judged by my requests later
        pdf_dir = pathlib.Path('tests/handwritten/main.pdf.zip')
        files = {
            'code': (
                open(pdf_dir, 'rb'),
                'code',
            )
        }
        rv = client_student.put(
            f'/submission/{self.submission_id}',
            data=files,
        )
        rv_json = rv.get_json()
        assert rv.status_code == 200, rv_json

        # third, read the student's upload
        rv = client_student.get(f'/submission/{self.submission_id}/pdf/upload')
        assert rv.status_code == 200, rv.get_json()

        # fourth, grade the submission
        rv = client_teacher.put(
            f'/submission/{self.submission_id}/grade',
            json={'score': 87},
        )
        assert rv.status_code == 200, rv.get_json()

        # fifth, send a wrong file to the submission
        pdf_dir = pathlib.Path('tests/src/base.c')
        files = {
            'comment': (
                open(pdf_dir, 'rb'),
                'comment',
            )
        }
        rv = client_teacher.put(
            f'/submission/{self.submission_id}/comment',
            data=files,
        )
        assert rv.status_code == 400, rv.get_json()

        # sixth, send the comment.pdf to the submission
        pdf_dir = pathlib.Path('tests/handwritten/comment.pdf')
        files = {
            'comment': (
                open(pdf_dir, 'rb'),
                'comment',
            )
        }
        rv = client_teacher.put(
            f'/submission/{self.submission_id}/comment',
            data=files,
        )
        assert rv.status_code == 200, rv.get_json()

        # seventh, get the submission info
        rv = client_student.get(f'/submission/{self.submission_id}')
        rv_json = rv.get_json()
        assert rv.status_code == 200, rv_json
        assert rv_json['data']['score'] == 87

        # eighth, get the submission comment
        rv = client_student.get(
            f'/submission/{self.submission_id}/pdf/comment')
        assert rv.status_code == 200

        # submit again will only replace the old one
        rv, rv_json, rv_data = BaseTester.request(
            client_student,
            'post',
            '/submission',
            json=post_json,
        )
        self.submission_id = rv_data["submissionId"]
        pdf_dir = pathlib.Path('tests/handwritten/main.pdf.zip')
        files = {
            'code': (
                open(pdf_dir, 'rb'),
                'code',
            )
        }
        rv = client_student.put(
            f'/submission/{self.submission_id}',
            data=files,
        )
        assert rv.status_code == 200, rv.get_json()

        # see if the student and thw teacher can get the submission
        rv = client_student.get(f'/submission?offset=0&count=-1')
        rv_json = rv.get_json()
        assert rv.status_code == 200, rv_json
        assert len(rv_json['data']['submissions']) == 1

        rv = client_teacher.get(f'/submission?offset=0&count=-1')
        rv_json = rv.get_json()
        assert rv.status_code == 200, rv_json
        assert len(rv_json['data']['submissions']) == 1

    @pytest.mark.parametrize(
        'user_a, user_b, status_code',
        [
            # student can view self score
            ('student', 'student', 200),
            # normal user can not view other's score
            ('student-2', 'student', 403),
            # teacher can view student's score
            ('student-2', 'teacher', 200),
            # also the admin
            ('student-2', 'admin', 200),
        ],
    )
    def test_handwritten_submission_score_visibility(
        self,
        forge_client,
        submit_once,
        user_a,
        user_b,
        status_code,
    ):
        '''
        test whether a `user_b` can view the `user_a`'s handwritten submission score
        '''
        submission_id = submit_once(user_a, self.pid, 'main.pdf', 3)
        client = forge_client(user_b)
        rv, rv_json, rv_data = BaseTester.request(
            client,
            'get',
            f'/submission/{submission_id}',
        )
        assert rv.status_code == status_code, rv_json

    def test_update_existing_comment(
        self,
        forge_client,
        submit_once,
    ):
        # create a handwritten submission
        submission_id = submit_once('student', self.pid, 'main.pdf', 3)
        client = forge_client('teacher')
        # try upload comment 5 times
        for _, p in zip(range(5), self.comment_paths):
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'put',
                f'/submission/{submission_id}/comment',
                data=self.comment(p),
            )
            assert rv.status_code == 200, rv_json
            # check comment content
            rv = client.get(f'/submission/{submission_id}/pdf/comment')
            assert rv.status_code == 200, rv.get_json()
            assert rv.data == open(p, 'rb').read()

    def test_comment_for_different_submissions(
        self,
        forge_client,
        submit_once,
    ):
        # try many times
        for _, p in zip(range(5), self.comment_paths):
            # create a new handwritten submission
            submission_id = submit_once(
                name='student',
                pid=self.pid,
                filename='main.pdf',
                lang=3,
            )
            # comment it
            client = forge_client('teacher')
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'put',
                f'/submission/{submission_id}/comment',
                data=self.comment(p),
            )
            assert rv.status_code == 200, rv_json
            # student get feedback
            client = forge_client('student')
            rv, rv_json, rv_data = BaseTester.request(
                client,
                'get',
                f'/submission/{submission_id}/pdf/comment',
            )
            assert rv.status_code == 200, rv_json
            assert rv.data == open(p, 'rb').read(), p


class TestSubmissionConfig(SubmissionTester):

    def test_get_config(self, client_admin):
        rv = client_admin.get(f'/submission/config')
        json = rv.get_json()
        assert rv.status_code == 200

    def test_edit_config(self, client_admin):
        rv = client_admin.put(
            f'/submission/config',
            json={
                'rateLimit':
                10,
                'sandboxInstances': [{
                    'name': 'Test',
                    'url': 'http://sandbox:6666',
                    'token': 'AAAAA',
                }]
            },
        )
        json = rv.get_json()
        assert rv.status_code == 200, json
        rv = client_admin.get(f'/submission/config')
        json = rv.get_json()
        assert rv.status_code == 200, json
        assert json['data'] == {
            'rateLimit':
            10,
            'sandboxInstances': [{
                'name': 'Test',
                'url': 'http://sandbox:6666',
                'token': 'AAAAA',
            }]
        }


class TestZipSubmissionMode:

    def test_zip_submission_returns_download_url(
        self,
        app,
        client_student,
        zip_problem,
        tmp_path,
    ):
        archive = _write_zip(
            tmp_path / 'zip-src.zip',
            {
                'Makefile': 'all:\n\t@true\n',
                'main.c': 'int main(){return 0;}',
            },
        )
        with app.app_context():
            submission = Submission.add(
                problem_id=zip_problem,
                username='student',
                lang=0,
                timestamp=datetime.now(),
                ip_addr='127.0.0.1',
            )
            with archive.open('rb') as fp:
                assert submission.submit(fp) is True
            submission_id = submission.id
        rv, _, data = BaseTester.request(
            client_student,
            'get',
            f'/submission/{submission_id}',
        )
        assert rv.status_code == 200
        assert 'codeDownloadUrl' in data
        assert data['code'] is None
        assert isinstance(data['codeDownloadUrl'],
                          str) and data['codeDownloadUrl'].startswith('http')
        with app.app_context():
            direct_url = Submission(submission_id).get_main_code()
            assert isinstance(direct_url,
                              str) and direct_url.startswith('http')

    def test_zip_submission_rejects_large_archive(
        self,
        app,
        zip_problem,
    ):
        with app.app_context():
            submission = Submission.add(
                problem_id=zip_problem,
                username='student',
                lang=0,
                timestamp=datetime.now(),
                ip_addr='127.0.0.1',
            )
            data = _zip_bytes({'main.c': 'int main(){return 0;}'})
            large_file = FakeSizedBytesIO(
                data,
                fake_size=1024 * 1024 * 1024 + 1,
            )
            with pytest.raises(ValueError) as exc:
                submission.submit(large_file)
            assert 'code file size too large' in str(exc.value)


def test_student_cannot_view_WA_submission_output(forge_client, app):
    student = utils.user.create_user()
    problem = utils.problem.create_problem(
        test_case_info=utils.problem.create_test_case_info(
            language=0,
            task_len=1,
        ))
    WA = 1
    with app.app_context():
        submission = utils.submission.create_submission(
            user=student,
            problem=problem,
            status=WA,
        )
        utils.submission.add_fake_output(submission)
    client = forge_client(student.username)
    rv = client.get(f'/submission/{submission.id}/output/0/0')
    assert rv.status_code == 403, rv.get_json()


def test_student_can_view_CE_submission_output(forge_client, app):
    student = utils.user.create_user()
    problem = utils.problem.create_problem(
        test_case_info=utils.problem.create_test_case_info(
            language=0,
            task_len=1,
        ))
    CE = 2
    with app.app_context():
        submission = utils.submission.create_submission(
            user=student,
            problem=problem,
            status=CE,
        )
        utils.submission.add_fake_output(submission)
    client = forge_client(student.username)
    rv = client.get(f'/submission/{submission.id}/output/0/0')
    assert rv.status_code == 200, rv.get_json()
    expected = submission.get_single_output(0, 0)
    assert expected == rv.get_json()['data']


def test_cannot_view_output_out_of_index(app, forge_client):
    with app.app_context():
        user = utils.user.create_user()
        course = utils.course.create_course()
        problem = utils.problem.create_problem(course=course)
        submission = utils.submission.create_submission(
            user=user,
            problem=problem,
        )
    client = forge_client(course.teacher.username)
    rv = client.get(f'/submission/{submission.id}/output/100/100')
    assert rv.status_code == 400, rv.get_json()
    assert rv.get_json()['message'] == 'task not exist'


def _create_submission_with_artifact(app, artifact_tasks):
    with app.app_context():
        owner = utils.user.create_user(role=1)
        course = utils.course.create_course(teacher=owner)
        student = utils.user.create_user(course=course)
        test_case_info = utils.problem.create_test_case_info(
            language=0,
            task_len=1,
            case_count_range=(2, 2),
        )
        description = {
            'description': 'artifact problem',
            'input': '',
            'output': '',
            'hint': '',
            'sampleInput': [],
            'sampleOutput': [],
        }
        problem_id = Problem.add(
            user=owner,
            courses=[course.course_name],
            problem_name='artifact-problem',
            status=0,
            description=description,
            tags=[],
            type=0,
            test_case_info=test_case_info,
        )
        problem = Problem(problem_id)
        problem.config['artifactCollection'] = artifact_tasks
        problem.save()
        problem.reload('config')
        submission = utils.submission.create_submission(
            user=student,
            problem=problem,
            status=0,
        )
        artifact_data = io.BytesIO()
        with ZipFile(artifact_data, 'w') as zf:
            zf.writestr('stdout', b'stdout')
            zf.writestr('stderr', b'stderr')
        artifact_data.seek(0)
        minio_client = MinioClient()
        output_path = submission._generate_output_minio_path(0, 0)
        data = artifact_data.getvalue()
        minio_client.client.put_object(
            minio_client.bucket,
            output_path,
            io.BytesIO(data),
            len(data),
            part_size=5 * 1024 * 1024,
            content_type='application/zip',
        )
        case = engine.CaseResult(
            status=0,
            exec_time=10,
            memory_usage=128,
            output_minio_path=output_path,
        )
        task = engine.TaskResult(
            status=0,
            exec_time=10,
            memory_usage=128,
            score=100,
            cases=[case],
        )
        submission.tasks = [task]
        submission.status = 0
        submission.score = 100
        submission.exec_time = 10
        submission.memory_usage = 128
        submission.save()
        submission.reload()
        return submission, student, course, owner


def _create_submission_with_compiled_binary(
    app,
    *,
    enable_compilation=True,
    with_binary=True,
):
    with app.app_context():
        owner = utils.user.create_user(role=1)
        course = utils.course.create_course(teacher=owner)
        student = utils.user.create_user(course=course)
        test_case_info = utils.problem.create_test_case_info(
            language=0,
            task_len=1,
            case_count_range=(1, 1),
        )
        description = {
            'description': 'compiled binary problem',
            'input': '',
            'output': '',
            'hint': '',
            'sampleInput': [],
            'sampleOutput': [],
        }
        problem_id = Problem.add(
            user=owner,
            courses=[course.course_name],
            problem_name='compiled-binary-problem',
            status=0,
            description=description,
            tags=[],
            type=0,
            test_case_info=test_case_info,
            config={'compilation': enable_compilation},
        )
        problem = Problem(problem_id)
        submission = utils.submission.create_submission(
            user=student,
            problem=problem,
            status=0,
        )
        if with_binary:
            submission.set_compiled_binary(b'\x00compiled-binary')
        submission.reload()
        return submission, student, owner


def test_download_task_artifact_zip_success(app, forge_client):
    submission, _, _, owner = _create_submission_with_artifact(app, [0])
    client = forge_client(owner.username)
    rv = client.get(f'/submission/{submission.id}/artifact/zip/0')
    assert rv.status_code == 200, rv.get_json()
    with ZipFile(io.BytesIO(rv.data)) as zf:
        names = sorted(zf.namelist())
    assert 'task_00/case_00/stdout' in names
    assert 'task_00/case_00/stderr' in names
    assert all(name.startswith('task_00/') for name in names)


def test_download_task_artifact_zip_disabled(app, forge_client):
    submission, _, _, owner = _create_submission_with_artifact(app, [])
    client = forge_client(owner.username)
    rv = client.get(f'/submission/{submission.id}/artifact/zip/0')
    assert rv.status_code == 404, rv.get_json()
    assert rv.get_json()['message'] == 'artifact not available for this task'


def test_download_task_artifact_zip_permission_denied(app, forge_client):
    submission, student, _, _ = _create_submission_with_artifact(app, [0])
    stranger = utils.user.create_user()
    client = forge_client(stranger.username)
    rv = client.get(f'/submission/{submission.id}/artifact/zip/0')
    assert rv.status_code == 403, rv.get_json()


def test_download_task_artifact_zip_invalid_task(app, forge_client):
    submission, _, _, owner = _create_submission_with_artifact(app, [0])
    client = forge_client(owner.username)
    rv = client.get(f'/submission/{submission.id}/artifact/zip/5')
    assert rv.status_code == 404, rv.get_json()
    assert rv.get_json()['message'] == 'task not exist'


def test_download_compiled_binary_success(app, forge_client):
    submission, student, _ = _create_submission_with_compiled_binary(
        app, enable_compilation=True, with_binary=True)
    client = forge_client(student.username)
    rv = client.get(f'/submission/{submission.id}/artifact/compiledBinary')
    assert rv.status_code == 200, rv.get_json()
    assert rv.data == b'\x00compiled-binary'


def test_download_compiled_binary_not_enabled(app, forge_client):
    submission, student, _ = _create_submission_with_compiled_binary(
        app, enable_compilation=False, with_binary=True)
    client = forge_client(student.username)
    rv = client.get(f'/submission/{submission.id}/artifact/compiledBinary')
    assert rv.status_code == 404, rv.get_json()
    assert rv.get_json()['message'] == 'compiled binary not available'


def test_download_compiled_binary_not_found(app, forge_client):
    submission, student, _ = _create_submission_with_compiled_binary(
        app, enable_compilation=True, with_binary=False)
    client = forge_client(student.username)
    rv = client.get(f'/submission/{submission.id}/artifact/compiledBinary')
    assert rv.status_code == 404, rv.get_json()
    assert rv.get_json()['message'] == 'compiled binary not found'


def test_download_compiled_binary_permission_denied(app, forge_client):
    submission, _, owner = _create_submission_with_compiled_binary(
        app, enable_compilation=True, with_binary=True)
    stranger = utils.user.create_user()
    client = forge_client(stranger.username)
    rv = client.get(f'/submission/{submission.id}/artifact/compiledBinary')
    assert rv.status_code == 403, rv.get_json()


def test_upload_case_artifact_api(app, forge_client):
    with app.app_context():
        submission, _, _, owner = _create_submission_with_artifact(app, [0])
        token = Submission.config().sandbox_instances[0].token
        client = app.test_client()
        payload = b'PK\x03\x04'  # dummy zip header start
        rv = client.put(
            f'/submission/{submission.id}/artifact/upload/case',
            query_string={
                'task': 0,
                'case': 0,
                'token': token
            },
            data=payload,
            content_type='application/zip',
        )
        assert rv.status_code == 200, rv.get_json()
        submission.reload()
        path = submission.tasks[0].cases[0].output_minio_path
        assert path
        data = MinioClient().download_file(path)
        assert data.startswith(payload)


def test_upload_compiled_binary_api(app):
    with app.app_context():
        submission, _, _ = _create_submission_with_compiled_binary(
            app, enable_compilation=True, with_binary=False)
        token = Submission.config().sandbox_instances[0].token
        client = app.test_client()
        binary = b'\x00\x01binary'
        rv = client.put(
            f'/submission/{submission.id}/artifact/upload/binary',
            query_string={'token': token},
            data=binary,
            content_type='application/octet-stream',
        )
        assert rv.status_code == 200, rv.get_json()
        submission.reload()
        assert submission.has_compiled_binary()
        assert submission.get_compiled_binary().read() == binary


def test_get_late_seconds_with_homework(client_admin, problem_ids):
    pid = problem_ids('teacher', 1, True)[0]
    course = Course(engine.Course.objects(teacher='teacher').first())
    end_time = datetime.now() - timedelta(hours=1)
    utils.homework.add_homework(
        user=course.teacher,
        course=course.course_name,
        hw_name='late-hw',
        problem_ids=[pid],
        markdown='',
        scoreboard_status=0,
        start=None,
        end=end_time.timestamp(),
        penalty='',
    )
    submission = Submission.add(problem_id=pid, username='student', lang=1)
    submission.update(timestamp=end_time + timedelta(minutes=30))
    resp = client_admin.get(
        f'/submission/{submission.id}/late-seconds',
        query_string={
            'token': Submission.config().sandbox_instances[0].token,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert data['lateSeconds'] >= 1800


def test_get_late_seconds_without_homework(client_admin, problem_ids):
    pid = problem_ids('teacher', 1, False)[0]
    submission = Submission.add(problem_id=pid, username='student', lang=1)
    resp = client_admin.get(
        f'/submission/{submission.id}/late-seconds',
        query_string={
            'token': Submission.config().sandbox_instances[0].token,
        },
    )
    assert resp.status_code == 200
    data = resp.get_json()['data']
    assert data['lateSeconds'] == -1
