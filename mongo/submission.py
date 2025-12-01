from __future__ import annotations
import io
import os
import pathlib
import secrets
from mongo.utils import generate_ulid
import logging
from typing import (
    Any,
    Dict,
    Optional,
    Union,
    List,
    TypedDict,
)
import enum
import tempfile
import requests as rq
from hashlib import md5
from bson.son import SON
from flask import current_app
from tempfile import NamedTemporaryFile
from datetime import date, datetime, timedelta
from zipfile import ZipFile, is_zipfile, BadZipFile
import abc

from . import engine
from .base import MongoBase
from .user import User
from .problem import Problem
from .homework import Homework
from .course import Course
from .utils import RedisCache, MinioClient

__all__ = [
    'SubmissionConfig',
    'Submission',
    'TrialSubmission',
    'JudgeQueueFullError',
    'TestCaseNotFound',
    'SubmissionCodeNotFound',
]

# TODO: modular token function


def gen_key(_id):
    return f'stoekn_{_id}'


def gen_token():
    return secrets.token_urlsafe()


# Errors
class JudgeQueueFullError(Exception):
    '''
    when sandbox task queue is full
    '''


class TestCaseNotFound(Exception):
    '''
    when a problem's testcase havn't been uploaded
    '''
    __test__ = False

    def __init__(self, problem_id):
        self.problem_id = problem_id

    def __str__(self):
        return f'{Problem(self.problem_id)}\'s testcase is not found'


class SubmissionCodeNotFound(Exception):
    '''
    when a submission's code is not found
    '''


class SubmissionResultOutput(TypedDict):
    '''
    output of a submission result, including stdout and stderr
    '''
    stdout: str | bytes
    stderr: str | bytes


class SubmissionConfig(MongoBase, engine=engine.SubmissionConfig):
    TMP_DIR = pathlib.Path(
        os.getenv(
            'SUBMISSION_TMP_DIR',
            tempfile.TemporaryDirectory(suffix='noj-submisisons').name,
        ), )

    def __init__(self, name: str):
        self.name = name


class BaseSubmission(abc.ABC):
    '''
    Base class for General and Test Submissions
    '''

    class Permission(enum.IntFlag):
        VIEW = enum.auto()  # view submission info
        UPLOAD = enum.auto()  # student can re-upload
        FEEDBACK = enum.auto()  # student can view homework feedback
        COMMENT = enum.auto()  # teacher or TAs can give comment
        REJUDGE = enum.auto()  # teacher or TAs can rejudge submission
        GRADE = enum.auto()  # teacher or TAs can grade homework
        VIEW_OUTPUT = enum.auto()
        OTHER = VIEW
        STUDENT = OTHER | UPLOAD | FEEDBACK
        MANAGER = STUDENT | COMMENT | REJUDGE | GRADE | VIEW_OUTPUT

    _config = None

    # def __init__(self, submission_id):
    #    self.submission_id = str(submission_id)

    @abc.abstractmethod
    def __str__(self):
        raise NotImplementedError

    @property
    def id(self):
        '''
        convert mongo ObjectId to hex string for serialize
        '''
        return str(self.obj.id)

    @property
    def problem_id(self) -> int:
        return self.problem.problem_id

    @property
    def username(self) -> str:
        return self.user.username

    @property
    def status2code(self):
        return {
            'AC': 0,
            'WA': 1,
            'CE': 2,
            'TLE': 3,
            'MLE': 4,
            'RE': 5,
            'JE': 6,
            'OLE': 7,
        }

    @property
    def handwritten(self):
        return self.language == 3

    @property
    def tmp_dir(self) -> pathlib.Path:
        tmp_dir = self.config().TMP_DIR
        tmp_dir.mkdir(exist_ok=True)
        tmp_dir = tmp_dir / self.username / self.id
        tmp_dir.mkdir(exist_ok=True, parents=True)
        return tmp_dir

    def calculate_late_seconds(self) -> int:
        """Return late seconds relative to homework deadlines. -1 表示無作業資訊。"""
        problem = self.problem
        username = self.username
        pid = str(self.problem_id)
        candidates = []
        for hw_ref in problem.homeworks:
            hw = Homework(hw_ref.id) if hasattr(hw_ref,
                                                "id") else Homework(hw_ref)
            if not hw:
                continue
            student_status = hw.student_status.get(username)
            if not student_status or pid not in student_status:
                continue
            end_time = hw.duration.end
            if end_time is None:
                continue
            delta = (self.timestamp - end_time).total_seconds()
            late = int(delta) if delta > 0 else 0
            candidates.append(late)
        return min(candidates) if candidates else -1

    @property
    def main_code_ext(self):
        lang2ext = {0: '.c', 1: '.cpp', 2: '.py', 3: '.pdf'}
        return lang2ext[self.language]

    @property
    def submission_mode(self) -> int:
        return getattr(self.problem.test_case, 'submission_mode', 0) or 0

    @property
    def is_zip_mode(self) -> bool:
        return self.submission_mode == 1

    @property
    def execution_mode(self) -> str:
        config = getattr(self.problem, 'config', {}) or {}
        return config.get('executionMode', 'general')

    @property
    def is_function_only_mode(self) -> bool:
        return self.execution_mode == 'functionOnly'

    def _validate_execution_mode_constraints(self):
        if self.is_function_only_mode and self.is_zip_mode:
            raise ValueError(
                'function-only problems only accept code submissions')

    def main_code_path(self) -> str:
        # handwritten submission didn't provide this function
        if self.handwritten:
            return
        if self.is_zip_mode:
            return self.get_code_download_url()
        # get excepted code name & temp path
        ext = self.main_code_ext
        path = self.tmp_dir / f'main{ext}'
        # check whether the code has been generated
        if not path.exists():
            if (z := self._get_code_zip()) is None:
                raise SubmissionCodeNotFound
            with z as zf:
                path.write_text(zf.read(f'main{ext}').decode('utf-8'))
        # return absolute path
        return str(path.absolute())

    @classmethod
    def config(cls):
        if not cls._config:
            cls._config = SubmissionConfig('submission')
        if not cls._config:
            cls._config.save()
        return cls._config.reload()

    def get_single_output(
        self,
        task_no: int,
        case_no: int,
        text: bool = True,
    ) -> SubmissionResultOutput:
        try:
            case = self.tasks[task_no].cases[case_no]
        except IndexError:
            raise FileNotFoundError('task not exist')
        ret = {}
        try:
            with ZipFile(self._get_output_raw(case)) as zf:
                ret = {k: zf.read(k) for k in ('stdout', 'stderr')}
                if text:
                    ret = {k: v.decode('utf-8') for k, v in ret.items()}
        except AttributeError:
            raise AttributeError('The submission is still in pending')
        return ret

    def _get_output_raw(self, case: engine.CaseResult) -> io.BytesIO:
        '''
        get a output blob of a submission result
        '''
        if case.output_minio_path is not None:
            # get from minio
            minio_client = MinioClient()
            try:
                resp = minio_client.client.get_object(
                    minio_client.bucket,
                    case.output_minio_path,
                )
                return io.BytesIO(resp.read())
            finally:
                if 'resp' in locals():
                    resp.close()
                    resp.release_conn()
        # fallback to gridfs
        return case.output

    def delete_output(self, *args):
        '''
        delete stdout/stderr of this submission

        Args:
            args: ignored value, don't mind
        '''
        for task in self.tasks:
            for case in task.cases:
                if case.output:
                    case.output.delete()
                case.output_minio_path = None
        self.save()

    @abc.abstractmethod
    def _get_droppable_fields(self) -> set:
        # 'code' and 'output' are common
        return {'code', 'output'}

    def delete(self, *keeps):
        '''
        delete submission and its related file

        Args:
            keeps:
                the field name you want to keep, e.g.
                {'comment', 'code', 'output'}
                other value will be ignored
        '''
        drops = self._get_droppable_fields() - {*keeps}
        del_funcs = {
            'output': self.delete_output,
        }

        def default_del_func(d):
            # Check if field exists and is not None before deleting
            if hasattr(self.obj, d) and self.obj[d]:
                if hasattr(self.obj[d], 'delete'):
                    self.obj[d].delete()
                else:
                    self.logger.warning(f"Field {d} has no delete method.")

        for d in drops:
            del_funcs.get(d, default_del_func)(d)
        self.obj.delete()

    def sandbox_resp_handler(self, resp):
        # judge queue is currently full
        def on_500(resp):
            raise JudgeQueueFullError

        # backend send some invalid data
        def on_400(resp):
            raise ValueError(resp.text)

        # send a invalid token
        def on_403(resp):
            raise ValueError('invalid token')

        h = {
            500: on_500,
            403: on_403,
            400: on_400,
            200: lambda r: True,
        }
        try:
            return h[resp.status_code](resp)
        except KeyError:
            self.logger.error('can not handle response from sandbox')
            self.logger.error(
                f'status code: {resp.status_code}\n'
                f'headers: {resp.headers}\n'
                f'body: {resp.text}', )
            return False

    def target_sandbox(self):
        load = 10**3  # current min load
        tar = None  # target
        for sb in self.config().sandbox_instances:
            try:
                resp = rq.get(f'{sb.url}/status', timeout=1)
                if not resp.ok:
                    self.logger.warning(f'sandbox {sb.name} status exception')
                    self.logger.warning(
                        f'status code: {resp.status_code}\n '
                        f'body: {resp.text}', )
                    continue
                resp_json = resp.json()
                if resp_json['load'] < load:
                    load = resp_json['load']
                    tar = sb
            except rq.exceptions.RequestException as e:
                self.logger.warning(f'sandbox {sb.name} is unreachable: {e}')
                continue
        return tar

    def _check_code(self, file):
        if not file:
            return 'no file'
        if not is_zipfile(file):
            try:
                file.seek(0)
            except (OSError, AttributeError):
                pass
            return 'not a valid zip file'
        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass
        if self.is_zip_mode:
            return self._check_zip_submission_payload(file)
        return self._check_standard_submission_payload(file)

    def _check_standard_submission_payload(self, file):
        MAX_SIZE = 10**7
        with ZipFile(file) as zf:
            infos = zf.infolist()

            size = sum(i.file_size for i in infos)
            if size > MAX_SIZE:
                return 'code file size too large'

            if len(infos) != 1:
                return 'more than one file in zip'
            name, ext = os.path.splitext(infos[0].filename)
            if name != 'main':
                return 'only accept file with name \'main\''
            if ext != ['.c', '.cpp', '.py', '.pdf'][self.language]:
                return f'invalid file extension, got {ext}'
            if ext == '.pdf':
                with zf.open('main.pdf') as pdf:
                    if pdf.read(5) != b'%PDF-':
                        return 'only accept PDF file.'
        file.seek(0)
        return None

    def _check_zip_submission_payload(self, file):
        limit = 1024 * 1024 * 1024  # 1GB
        try:
            file.seek(0, os.SEEK_END)
            size = file.tell()
        except (OSError, AttributeError):
            size = None
        finally:
            try:
                file.seek(0)
            except (OSError, AttributeError):
                pass
        if size is not None and size > limit:
            return 'code file size too large (limit 1GB)'
        try:
            file.seek(0)
        except (OSError, AttributeError):
            pass
        return None

    def rejudge(self) -> bool:
        '''
        rejudge this submission
        '''
        # delete output file
        self.delete_output()
        # turn back to haven't be judged
        self.update(
            status=-1,
            last_send=datetime.now(),
            tasks=[],
        )
        if current_app.config['TESTING']:
            return True
        return self.send()  # Calls subclass's send()

    def _generate_code_minio_path(self):
        return f'submissions/{generate_ulid()}.zip'

    def _put_code(self, code_file) -> str:
        '''
        put code file to minio, return the object name
        '''
        if (err := self._check_code(code_file)) is not None:
            raise ValueError(err)

        minio_client = MinioClient()
        path = self._generate_code_minio_path()
        minio_client.client.put_object(
            minio_client.bucket,
            path,
            code_file,
            -1,
            part_size=5 * 1024 * 1024,
            content_type='application/zip',
        )
        return path

    def _ensure_code_minio_path(self) -> Optional[str]:
        if self.code_minio_path:
            return self.code_minio_path
        raw = self._get_code_raw()
        if raw is None:
            return None
        buf = io.BytesIO(b"".join(raw))
        path = self._put_code(buf)
        self.update(code_minio_path=path)
        self.reload()
        return path

    def get_code_download_url(
        self, expires: timedelta = timedelta(minutes=10)) -> Optional[str]:
        path = self._ensure_code_minio_path()
        if path is None:
            return None
        minio_client = MinioClient()
        return minio_client.client.get_presigned_url(
            'GET',
            minio_client.bucket,
            path,
            expires=expires,
        )

    @abc.abstractmethod
    def submit(self, *args, **kwargs) -> bool:
        '''
        prepare data for submit code to sandbox and then send it
        '''
        raise NotImplementedError

    @abc.abstractmethod
    def send(self) -> bool:
        '''
        send code to sandbox
        '''
        raise NotImplementedError

    @abc.abstractmethod
    def _calculate_task_score(self, task_index: int, status: int) -> int:
        '''
        Calculate score for a given task based on its status.
        '''
        raise NotImplementedError

    @abc.abstractmethod
    def finish_judging(self):
        '''
        Post-processing after results are received.
        e.g. update homework, stats, etc.
        '''
        raise NotImplementedError

    def process_result(self,
                       tasks: list,
                       static_analysis: Optional[dict] = None,
                       checker: Optional[dict] = None,
                       scoring: Optional[dict] = None,
                       status_override: Optional[str] = None):
        '''
        process results from sandbox

        Args:
            tasks:
                a 2-dim list of the dict with schema
                {
                    'exitCode': int,
                    'status': str,
                    'stdout': str,
                    'stderr': str,
                    'execTime': int,
                    'memoryUsage': int
                }
            static_analysis:
                optional static analysis payload from sandbox
        '''
        self.logger.info(f'recieve {self} result')
        processed_tasks = []
        minio_client = MinioClient()

        sa_updates = {}
        checker_updates = {}
        scoring_updates = {}
        scoring_score_override = None
        scoring_status_code = None
        if static_analysis:
            sa_status = static_analysis.get('status', '').lower()
            if sa_status == 'skip':
                sa_updates.update(
                    sa_status=None,
                    sa_message=static_analysis.get('message'),
                    sa_report=static_analysis.get('report'),
                )
            else:
                sa_updates.update(
                    sa_status=0 if sa_status == 'pass' else 1,
                    sa_message=static_analysis.get('message'),
                    sa_report=static_analysis.get('report'),
                )
            report_path = static_analysis.get('reportPath')
            report_text = static_analysis.get('report') or ''
            if report_path:
                sa_updates['sa_report_path'] = report_path
            elif report_text:
                # upload report text to minio for later download
                minio_client = MinioClient()
                object_name = f'static-analysis/{self.id}_{generate_ulid()}.txt'
                minio_client.upload_file_object(
                    io.BytesIO(report_text.encode('utf-8')),
                    object_name=object_name,
                    length=len(report_text.encode('utf-8')),
                    content_type='text/plain',
                )
                sa_updates['sa_report_path'] = object_name
        if checker:
            messages = checker.get('messages') or []
            summary_parts = []
            for msg in messages:
                case_no = msg.get('case')
                status = msg.get('status')
                text = msg.get('message') or ''
                if not text:
                    continue
                prefix = f"{case_no}: " if case_no is not None else ''
                status_part = f"[{status}]" if status else ''
                summary_parts.append(f"{prefix}{status_part} {text}".strip())
            if summary_parts:
                checker_updates['checker_summary'] = "\n".join(summary_parts)
            artifacts = checker.get('artifacts') or {}
            artifact_path = (artifacts.get('checkResultPath')
                             or artifacts.get('path')
                             or artifacts.get('checkerPath'))
            artifact_text = artifacts.get('checkResult')
            if artifact_path:
                checker_updates['checker_artifacts_path'] = artifact_path
            elif artifact_text:
                object_name = f'checker/{self.id}_{generate_ulid()}.txt'
                data = artifact_text.encode('utf-8')
                minio_client.upload_file_object(
                    io.BytesIO(data),
                    object_name=object_name,
                    length=len(data),
                    content_type='text/plain',
                )
                checker_updates['checker_artifacts_path'] = object_name
        if scoring:
            if scoring.get('score') is not None:
                try:
                    scoring_score_override = int(scoring.get('score'))
                except (TypeError, ValueError):
                    scoring_score_override = 0
            scoring_status = scoring.get('status')
            if scoring_status:
                scoring_status_code = self.status2code.get(scoring_status)
            message = scoring.get('message')
            if message:
                scoring_updates['scoring_message'] = message
            breakdown = scoring.get('breakdown')
            if isinstance(breakdown, dict):
                scoring_updates['scoring_breakdown'] = breakdown
            artifacts = scoring.get('artifacts') or {}
            artifact_path = (artifacts.get('path')
                             or artifacts.get('scorerPath')
                             or artifacts.get('checkResultPath'))
            artifact_text = (artifacts.get('text') or artifacts.get('stdout')
                             or artifacts.get('stderr'))
            if artifact_path:
                scoring_updates['scorer_artifacts_path'] = artifact_path
            elif artifact_text:
                object_name = f'scorer/{self.id}_{generate_ulid()}.txt'
                data = artifact_text.encode('utf-8')
                minio_client.upload_file_object(
                    io.BytesIO(data),
                    object_name=object_name,
                    length=len(data),
                    content_type='text/plain',
                )
                scoring_updates['scorer_artifacts_path'] = object_name

        for i, task_cases in enumerate(tasks):
            # process cases
            cases = []
            for j, case in enumerate(task_cases):
                # we don't need exit code
                del case['exitCode']
                # convert status into integer
                case['status'] = self.status2code.get(case['status'], -3)

                # save stdout/stderr
                fds = ['stdout', 'stderr']
                tf = NamedTemporaryFile(delete=False)
                with ZipFile(tf, 'w') as zf:
                    for fd in fds:
                        content = case.pop(fd)
                        if content is None:
                            self.logger.error(
                                f'key {fd} not in case result {self} {i:02d}{j:02d}'
                            )
                        zf.writestr(fd, content
                                    or "")  # Ensure content is not None
                tf.seek(0)

                # upload to minio
                output_minio_path = self._generate_output_minio_path(i, j)
                minio_client.client.put_object(
                    minio_client.bucket,
                    output_minio_path,
                    io.BytesIO(tf.read()),
                    -1,
                    part_size=5 * 1024 * 1024,  # 5MB
                    content_type='application/zip',
                )

                # convert dict to document
                cases.append(
                    engine.CaseResult(
                        status=case['status'],
                        exec_time=case['execTime'],
                        memory_usage=case['memoryUsage'],
                        output_minio_path=output_minio_path,
                    ))

            # process task
            status = max(c.status for c in cases) if cases else -3
            exec_time = max(c.exec_time for c in cases) if cases else -1
            memory_usage = max(c.memory_usage for c in cases) if cases else -1

            # Calculate score using subclass-defined logic
            score = self._calculate_task_score(i, status)

            processed_tasks.append(
                engine.TaskResult(
                    status=status,
                    exec_time=exec_time,
                    memory_usage=memory_usage,
                    score=score,
                    cases=cases,
                ))

        tasks = processed_tasks
        status = max(t.status for t in tasks) if tasks else -3
        exec_time = max(t.exec_time for t in tasks) if tasks else -1
        memory_usage = max(t.memory_usage for t in tasks) if tasks else -1

        final_score = sum(task.score for task in tasks)
        if scoring_score_override is not None:
            final_score = scoring_score_override

        final_status = status
        override_status = status_override or None
        if override_status:
            override_status = self.status2code.get(override_status,
                                                   final_status)
        if scoring_status_code is not None:
            override_status = scoring_status_code
        if override_status is not None:
            final_status = override_status

        self.update(
            score=final_score,
            status=final_status,
            tasks=tasks,
            exec_time=exec_time,
            memory_usage=memory_usage,
            **sa_updates,
            **checker_updates,
            **scoring_updates,
        )
        self.reload()
        self.finish_judging()  # Call subclass's finish_judging
        return True

    def _generate_output_minio_path(self, task_no: int, case_no: int) -> str:
        '''
        generate a output file path for minio
        '''
        return f'submissions/task{task_no:02d}_case{case_no:02d}_{generate_ulid()}.zip'

    @staticmethod
    @abc.abstractmethod
    def count():
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def filter(
        cls,
        user,
        offset: int = 0,
        count: int = -1,
        problem: Optional[Union[Problem, int]] = None,
        q_user: Optional[Union[User, str]] = None,
        status: Optional[int] = None,
        language_type: Optional[Union[List[int], int]] = None,
        course: Optional[Union[Course, str]] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
        sort_by: Optional[str] = None,
        with_count: bool = False,
        ip_addr: Optional[str] = None,
    ):
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def add(cls, *args, **kwargs):
        '''
        Insert a new submission into db
        '''
        raise NotImplementedError

    @classmethod
    def assign_token(cls, submission_id, token=None):
        '''
        generate a token for the submission
        '''
        if token is None:
            token = gen_token()
        RedisCache().set(gen_key(submission_id), token)
        return token

    @classmethod
    def verify_token(cls, submission_id, token):
        cache = RedisCache()
        key = gen_key(submission_id)
        s_token = cache.get(key)
        if s_token is None:
            return False
        s_token = s_token.decode('ascii')
        valid = secrets.compare_digest(s_token, token)
        if valid:
            cache.delete(key)
        return valid

    def to_dict(self) -> Dict[str, Any]:
        ret = self._to_dict()
        # Convert Bson object to python dictionary
        ret = ret.to_dict()
        return ret

    def _to_dict(self) -> SON:
        ret = self.to_mongo()
        _ret = {
            'problemId': ret['problem'],
            'user': self.user.info,
            'submissionId': str(self.id),
            'timestamp': self.timestamp.timestamp(),
            'lastSend': self.last_send.timestamp(),
            'ipAddr': self.ip_addr,
        }
        old = [
            '_id',
            'problem',
            'code',
            # 'comment', # 'comment' is not in BaseSubmissionDocument
            'tasks',
            'ip_addr',
        ]
        # delete old keys
        for o in old:
            if o in ret:
                del ret[o]

        # 'comment' is specific to Submission, not BaseSubmission
        if 'comment' in ret:
            del ret['comment']

        # insert new keys
        ret.update(**_ret)
        return ret

    def get_result(self) -> List[Dict[str, Any]]:
        '''
        Get results without output
        '''
        tasks = [task.to_mongo() for task in self.tasks]
        for task in tasks:
            for case in task['cases']:
                case.pop('output', None)
                case.pop('output_minio_path', None)
        return [task.to_dict() for task in tasks]

    def get_detailed_result(self) -> List[Dict[str, Any]]:
        '''
        Get all results (including stdout/stderr) of this submission
        '''
        tasks = [task.to_mongo() for task in self.tasks]
        for i, task in enumerate(tasks):
            for j, case in enumerate(task['cases']):
                output = self.get_single_output(i, j)
                case['stdout'] = output['stdout']
                case['stderr'] = output['stderr']
                case.pop('output', None)  # non-serializable field
                case.pop('output_minio_path', None)  # non-serializable field
        return [task.to_dict() for task in tasks]

    def _get_code_raw(self):
        if self.code.grid_id is None and self.code_minio_path is None:
            return None

        if self.code_minio_path is not None:
            minio_client = MinioClient()
            try:
                resp = minio_client.client.get_object(
                    minio_client.bucket,
                    self.code_minio_path,
                )
                return [resp.read()]
            finally:
                if 'resp' in locals():
                    resp.close()
                    resp.release_conn()

        # fallback to read from gridfs
        return [self.code.read()]

    def _get_code_zip(self):
        if (raw := self._get_code_raw()) is None:
            return None
        return ZipFile(io.BytesIO(b"".join(raw)))

    def get_code(self, path: str, binary=False) -> Union[str, bytes]:
        # read file
        try:
            if (z := self._get_code_zip()) is None:
                raise SubmissionCodeNotFound
            with z as zf:
                data = zf.read(path)
        # file not exists in the zip or code haven't been uploaded
        except KeyError:
            return None
        # decode byte if need
        if not binary:
            try:
                data = data.decode('utf-8')
            except UnicodeDecodeError:
                data = 'Unusual file content, decode fail'
        return data

    def get_main_code(self) -> str:
        '''
        Get source code user submitted
        '''
        if self.is_zip_mode:
            return self.get_code_download_url()
        ext = self.main_code_ext
        return self.get_code(f'main{ext}')

    def has_code(self) -> bool:
        return self._get_code_zip() is not None

    @abc.abstractmethod
    def own_permission(self, user) -> Permission:
        raise NotImplementedError

    def permission(self, user, req: Permission):
        """
        check whether user own `req` permission
        """

        return bool(self.own_permission(user) & req)

    def migrate_code_to_minio(self):
        """
        migrate code from gridfs to minio
        """
        # nothing to migrate
        if self.code is None or self.code.grid_id is None:
            self.logger.info(f"no code to migrate. submission={self.id}")
            return

        # upload code to minio
        if self.code_minio_path is None:
            self.logger.info(f"uploading code to minio. submission={self.id}")
            self.update(code_minio_path=self._put_code(self.code), )
            self.reload()
            self.logger.info(
                f"code uploaded to minio. submission={self.id} path={self.code_minio_path}"
            )

        # remove code in gridfs if it is consistent
        if self._check_code_consistency():
            self.logger.info(
                f"data consistency validated, removing code in gridfs. submission={self.id}"
            )
            self._remove_code_in_mongodb()
        else:
            self.logger.warning(
                f"data inconsistent, keeping code in gridfs. submission={self.id}"
            )

    def _remove_code_in_mongodb(self):
        self.code.delete()
        self.save()
        self.reload('code')

    def _check_code_consistency(self):
        """
        check whether the submission is consistent
        """
        if self.code is None or self.code.grid_id is None:
            return False
        gridfs_code = self.code.read()
        if gridfs_code is None:
            # if file is deleted but GridFS proxy is not updated
            return False
        gridfs_checksum = md5(gridfs_code).hexdigest()
        self.logger.info(
            f"calculated grid checksum. submission={self.id} checksum={gridfs_checksum}"
        )

        minio_client = MinioClient()
        try:
            resp = minio_client.client.get_object(
                minio_client.bucket,
                self.code_minio_path,
            )
            minio_code = resp.read()
        finally:
            if 'resp' in locals():
                resp.close()
                resp.release_conn()

        minio_checksum = md5(minio_code).hexdigest()
        self.logger.info(
            f"calculated minio checksum. submission={self.id} checksum={minio_checksum}"
        )
        return minio_checksum == gridfs_checksum


class Submission(MongoBase, BaseSubmission, engine=engine.Submission):
    '''
    Represents a formal submission for homework grading.
    '''

    def __init__(self, submission_id):
        self.submission_id = str(submission_id)

    def __str__(self):
        return f'submission [{self.submission_id}]'

    # --- Implement Abstract Methods ---

    def _calculate_task_score(self, task_index: int, status: int) -> int:
        '''
        Calculate score based on problem's test case definition.
        '''
        if status == 0:  # AC
            try:
                return self.problem.test_case.tasks[task_index].task_score
            except (AttributeError, IndexError):
                self.logger.warning(
                    f"Could not find score for {self} task {task_index}")
                return 0
        return 0

    def finish_judging(self):
        '''
        Update user stats, homework scores, and problem high scores.
        '''
        # update user's submission
        User(self.username).add_submission(self)
        # update homework data
        for homework in self.problem.homeworks:
            try:
                stat = homework.student_status[self.username][str(
                    self.problem_id)]
            except KeyError:
                self.logger.warning(
                    f'{self} not in {homework} [user={self.username}, problem={self.problem_id}]'
                )
                continue
            if self.handwritten:
                continue
            if 'rawScore' not in stat:
                stat['rawScore'] = 0
            stat['submissionIds'].append(self.id)
            # handwritten problem will only keep the last submission
            if self.handwritten:
                stat['submissionIds'] = stat['submissionIds'][-1:]
            # if the homework is overdue, do the penalty
            if self.timestamp > homework.duration.end and not self.handwritten and homework.penalty is not None:
                self.score, stat['rawScore'] = Homework(homework).do_penalty(
                    self, stat)
            else:
                if self.score > stat['rawScore']:
                    stat['rawScore'] = self.score
            # update high score / handwritten problem is judged by teacher
            if self.score >= stat['score'] or self.handwritten:
                stat['score'] = self.score
                stat['problemStatus'] = self.status

            homework.save()
        key = Problem(self.problem).high_score_key(user=self.user)
        RedisCache().delete(key)

    def submit(self, code_file) -> bool:
        '''
        prepare data for submit code to sandbox and then send it

        Args:
            code_file: a zip file contains user's code
        '''
        # unexisted id
        if not self:
            raise engine.DoesNotExist(f'{self}')
        self._validate_execution_mode_constraints()
        self.update(
            status=-1,
            last_send=datetime.now(),
            code_minio_path=self._put_code(code_file),
        )
        self.reload()
        self.logger.debug(f'{self} code updated.')
        # delete old handwritten submission
        if self.handwritten:
            q = {
                'problem': self.problem,
                'user': self.user,
                'language': 3,
            }
            for submission in engine.Submission.objects(**q):
                if submission != self.obj:
                    for homework in self.problem.homeworks:
                        stat = homework.student_status[self.user.username][str(
                            self.problem_id)]
                        stat['score'] = 0
                        stat['problemStatus'] = -1
                        stat['submissionIds'] = []
                        homework.save()
                    submission.delete()
        # we no need to actually send code to sandbox during testing
        if current_app.config['TESTING'] or self.handwritten:
            return True
        return self.send()

    def send(self) -> bool:
        '''
        send code to sandbox
        '''
        if self.handwritten:
            logging.warning(f'try to send a handwritten {self}')
            return False
        # TODO: Ensure problem is ready to submitted
        # if not Problem(self.problem).is_test_case_ready():
        #     raise TestCaseNotFound(self.problem.problem_id)
        # setup post body
        files = {
            'src': io.BytesIO(b"".join(self._get_code_raw())),
        }
        # look for the target sandbox
        tar = self.target_sandbox()
        if tar is None:
            self.logger.error(f'can not target a sandbox for {repr(self)}')
            return False
        # save token for validation
        Submission.assign_token(self.id, tar.token)
        post_data = {
            'token': tar.token,
            'problem_id': self.problem_id,
            'language': self.language,
            'submission_type': 'normal',  # Flag for sandbox
        }
        judge_url = f'{tar.url}/submit/{self.id}'
        # send submission to sandbox for judgement
        self.logger.info(f'send {self} to {tar.name}')
        resp = rq.post(
            judge_url,
            data=post_data,
            files=files,
        )
        self.logger.info(f'recieve {self} resp from sandbox')
        return self.sandbox_resp_handler(resp)

    def own_permission(self, user) -> BaseSubmission.Permission:
        key = f'SUBMISSION_PERMISSION_{self.id}_{user.id}_{self.problem.id}'
        # Check cache
        cache = RedisCache()
        if (v := cache.get(key)) is not None:
            return self.Permission(int(v))

        # Calculate
        cap = self.Permission(0)
        try:
            problem_courses = map(Course, self.problem.courses)
            if any(
                    c.own_permission(user) & Course.Permission.GRADE
                    for c in problem_courses):
                cap = self.Permission.MANAGER
            elif user.username == self.user.username:
                cap = self.Permission.STUDENT
            elif Problem(self.problem).permission(
                    user=user,
                    req=Problem.Permission.VIEW,
            ):
                cap = self.Permission.OTHER
        except Exception as e:
            self.logger.error(f"Error calculating permission for {self}: {e}")
            # Fallback to no permission
            cap = self.Permission(0)

        # students can view outputs of their CE submissions
        CE = 2
        if cap & self.Permission.STUDENT and self.status == CE:
            cap |= self.Permission.VIEW_OUTPUT

        cache.set(key, cap.value, 60)
        return cap

    def _get_droppable_fields(self) -> set:
        # Submission has 'comment'
        return super()._get_droppable_fields() | {'comment'}

    # --- Submission-specific Methods ---

    def get_comment(self) -> bytes:
        '''
        if comment not exist
        '''
        if self.comment.grid_id is None:
            raise FileNotFoundError('it seems that comment haven\'t upload')
        return self.comment.read()

    def add_comment(self, file):
        '''
        comment a submission with PDF

        Args:
            file: a PDF file
        '''
        data = file.read()
        # check magic number
        if data[:5] != b'%PDF-':
            raise ValueError('only accept PDF file.')
        # write to a new file if it did not exist before
        if self.comment.grid_id is None:
            write_func = self.comment.put
        # replace its content otherwise
        else:
            write_func = self.comment.replace
        write_func(data)
        self.logger.debug(f'{self} comment updated.')
        # update submission
        self.save()

    # --- Submission-specific Classmethods ---

    @staticmethod
    def count():
        return len(engine.Submission.objects)

    @classmethod
    def filter(
        cls,
        user,
        offset: int = 0,
        count: int = -1,
        problem: Optional[Union[Problem, int]] = None,
        q_user: Optional[Union[User, str]] = None,
        status: Optional[int] = None,
        language_type: Optional[Union[List[int], int]] = None,
        course: Optional[Union[Course, str]] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
        sort_by: Optional[str] = None,
        with_count: bool = False,
        ip_addr: Optional[str] = None,
    ):
        if before is not None and after is not None:
            if after > before:
                raise ValueError('the query period is empty')
        if offset < 0:
            raise ValueError(f'offset must >= 0!')
        if count < -1:
            raise ValueError(f'count must >=-1!')
        if sort_by is not None and sort_by not in ['runTime', 'memoryUsage']:
            raise ValueError(f'can only sort by runTime or memoryUsage')
        wont_have_results = False
        if isinstance(problem, int):
            problem = Problem(problem).obj
            if problem is None:
                wont_have_results = True
        if isinstance(q_user, str):
            q_user = User(q_user)
            if not q_user:
                wont_have_results = True
            q_user = q_user.obj
        if isinstance(course, str):
            course = Course(course)
            if not course:
                wont_have_results = True
        # problem's query key
        p_k = 'problem'
        if course:
            problems = Problem.get_problem_list(
                user,
                course=course.course_name,
            )
            # use all problems under this course to filter
            if problem is None:
                p_k = 'problem__in'
                problem = problems
            # if problem not in course
            elif problem not in problems:
                wont_have_results = True
        if wont_have_results:
            return ([], 0) if with_count else []
        if isinstance(language_type, int):
            language_type = [language_type]
        # query args
        q = {
            p_k: problem,
            'status': status,
            'language__in': language_type,
            'user': q_user,
            'ip_addr': ip_addr,
            'timestamp__lte': before,
            'timestamp__gte': after,
        }
        q = {k: v for k, v in q.items() if v is not None}
        # sort by upload time
        submissions = engine.Submission.objects(
            **q).order_by(sort_by if sort_by is not None else '-timestamp')
        submission_count = submissions.count()
        # truncate
        if count == -1:
            submissions = submissions[offset:]
        else:
            submissions = submissions[offset:offset + count]
        submissions = list(cls(s) for s in submissions)
        if with_count:
            return submissions, submission_count
        return submissions

    def is_artifact_enabled(self, task_index: int) -> bool:
        try:
            config = self.problem.config
            if not config or not isinstance(config, dict):
                return False
            artifact_collection = config.get('artifactCollection', [])
            return task_index in artifact_collection
        except (AttributeError, KeyError):
            return False

    def build_task_artifact_zip(self, task_index: int) -> io.BytesIO:
        if task_index < 0 or task_index >= len(self.tasks):
            raise FileNotFoundError('task not exist')
        task = self.tasks[task_index]
        if not task.cases:
            raise FileNotFoundError('case not exist')

        minio_client = MinioClient()
        artifact_buf = io.BytesIO()
        wrote_any_file = False

        with ZipFile(artifact_buf, 'w') as artifact_zip:
            for case_index, case in enumerate(task.cases):
                output_path = getattr(case, 'output_minio_path', None)
                if not output_path:
                    continue
                data = minio_client.download_file(output_path)
                try:
                    with ZipFile(io.BytesIO(data)) as case_zip:
                        for name in case_zip.namelist():
                            arcname = (
                                f'task_{task_index:02d}/case_{case_index:02d}/{name}'
                            )
                            artifact_zip.writestr(arcname, case_zip.read(name))
                            wrote_any_file = True
                except BadZipFile as exc:
                    raise FileNotFoundError(
                        f'invalid artifact archive: {exc}') from exc

        if not wrote_any_file:
            raise FileNotFoundError('artifact not available for this task')

        artifact_buf.seek(0)
        return artifact_buf

    def set_compiled_binary(self, binary_data: bytes) -> None:
        try:
            minio_client = MinioClient()
            object_name = f'compiled_binaries/{self.id}'
            minio_client.upload_file_object(
                io.BytesIO(binary_data),
                object_name,
                len(binary_data),
            )
            self.update(compiled_binary_minio_path=object_name)
        except Exception as e:
            self.logger.error(f'Failed to set compiled binary: {e}')
            raise

    def has_compiled_binary(self) -> bool:
        try:
            return bool(self.compiled_binary_minio_path)
        except AttributeError:
            return False

    def get_compiled_binary(self) -> io.BytesIO:
        if not self.compiled_binary_minio_path:
            raise FileNotFoundError('compiled binary not found')
        minio_client = MinioClient()
        data = minio_client.download_file(self.compiled_binary_minio_path)
        return io.BytesIO(data)

    @classmethod
    def add(
        cls,
        problem_id: int,
        username: str,
        lang: int,
        timestamp: Optional[date] = None,
        ip_addr: Optional[str] = None,
    ) -> 'Submission':
        '''
        Insert a new submission into db

        Returns:
            The created submission
        '''
        # check existence
        user = User(username)
        if not user:
            raise engine.DoesNotExist(f'{user} does not exist')
        problem = Problem(problem_id)
        if not problem:
            raise engine.DoesNotExist(f'{problem} dose not exist')
        # TODO: Ensure problem is ready to submitted
        # if not problem.is_test_case_ready():
        #     raise TestCaseNotFound(problem_id)
        if timestamp is None:
            timestamp = datetime.now()
        # create a new submission
        submission = engine.Submission(problem=problem.obj,
                                       user=user.obj,
                                       language=lang,
                                       timestamp=timestamp,
                                       ip_addr=ip_addr)
        submission.save()
        return cls(submission.id)


class TrialSubmission(MongoBase, BaseSubmission,
                      engine=engine.TrialSubmission):
    '''
    Represents a test submission against public or custom cases.
    Does not affect homework scores. Expires after 14 days.
    '''

    def __init__(self, submission_id):
        self.submission_id = str(submission_id)

    def __str__(self):
        return f'trial_submission [{self.submission_id}]'

    # --- Implement Abstract Methods ---

    def _calculate_task_score(self, task_index: int, status: int) -> int:
        '''
        Test submissions do not have a "score" in the traditional sense.
        The pass/fail status of cases is what matters.
        Return 0.
        '''
        return 0

    def finish_judging(self):
        '''
        Update problem-level test submission stats.
        Does NOT update homework or user AC stats.
        '''
        self.logger.info(f"Finished judging {self}")

        # Update problem stats (e.g., submission count)
        problem = Problem(self.problem)
        if problem:
            username = self.user.username
            count_key = f"trial_submission_counts.{username}"
            problem.obj.update(**{f'inc__{count_key}': 1})

        # No User.add_submission()
        # No Homework.student_status update
        pass

    def submit(self,
               code_file,
               use_default_case: bool = True,
               custom_input_file=None) -> bool:
        '''
        Prepare data for a test submission.
        Checks for test mode enablement and quota.
        '''
        if not self:
            raise engine.DoesNotExist(f'{self}')

        problem = Problem(self.problem)
        if not problem.test_mode_enabled:
            raise PermissionError("Test mode is not enabled for this problem.")

        # Check quota
        if problem.trial_submission_quota > 0:
            username = self.user.username
            current_count = problem.trial_submission_counts.get(username, 0)
            if current_count >= problem.trial_submission_quota:
                raise PermissionError("Test submission quota exceeded.")

        custom_input_path = None
        if not use_default_case:
            if custom_input_file is None:
                raise ValueError(
                    "Custom input file must be provided when not using default cases."
                )
            # TODO: Implement _put_custom_input method, similar to _put_code
            # Need to validate custom_input_file (e.g., check zip format, size)
            # custom_input_path = self._put_custom_input(custom_input_file)
            self.logger.warning(
                f"Custom input for {self} is not yet implemented.")

        self.update(
            status=-1,
            last_send=datetime.now(),
            code_minio_path=self._put_code(code_file),
            use_default_case=use_default_case,
            # custom_input_minio_path=custom_input_path,
        )
        self.reload()
        self.logger.debug(f'{self} code updated.')

        if current_app.config['TESTING']:
            return True
        return self.send()

    def send(self) -> bool:
        '''
        Send code, public/custom cases, and AC code to sandbox.
        '''
        problem = Problem(self.problem)
        if not problem.test_mode_enabled:
            self.logger.warning(
                f"Attempted to send {self} but test mode is disabled.")
            return False

        # 1. User's code
        files = {
            'src': io.BytesIO(b"".join(self._get_code_raw())),
        }

        # 2. Test cases (public or custom)
        # TODO: This logic depends heavily on sandbox API.
        # The sandbox needs to receive either the public cases or custom cases.
        # This might involve passing Minio paths or zips.
        if self.use_default_case:
            if not problem.public_cases_zip_minio_path:
                raise TestCaseNotFound(self.problem_id)
            # files['public_cases'] = ... (logic to get public cases zip)
        else:
            if not self.custom_input_minio_path:
                raise TestCaseNotFound(self.problem_id)  # Or a different error
            # files['custom_cases'] = ... (logic to get custom input zip)

        # 3. AC code (for comparison)
        # TODO: Add logic for sending AC code
        # if problem.ac_code_minio_path:
        #    ac_code_raw = ... (logic to get AC code zip)
        #    files['ac_code'] = ac_code_raw

        tar = self.target_sandbox()
        if tar is None:
            self.logger.error(f'can not target a sandbox for {repr(self)}')
            return False

        TrialSubmission.assign_token(self.id, tar.token)

        post_data = {
            'token': tar.token,
            'problem_id': self.problem_id,
            'language': self.language,
            'submission_type': 'test',  # Flag for sandbox
            'use_default_case': self.use_default_case,
            # 'ac_code_language': problem.ac_code_language,
            # 'public_cases_path': problem.public_cases_zip_minio_path,
            # 'custom_cases_path': self.custom_input_minio_path,
            # 'ac_code_path': problem.ac_code_minio_path,
        }

        # This URL might be different, e.g., /submit_test/
        judge_url = f'{tar.url}/submit/{self.id}'

        self.logger.info(f'send {self} to {tar.name}')
        self.logger.warning(
            f"TrialSubmission.send() is a placeholder and needs sandbox API integration."
        )

        self.logger.error("Test submission sandbox endpoint not implemented.")
        return False

        # resp = rq.post(
        #     judge_url,
        #     data=post_data,
        #     files=files,
        # )
        # self.logger.info(f'recieve {self} resp from sandbox')
        # return self.sandbox_resp_handler(resp)

    def own_permission(self, user) -> BaseSubmission.Permission:
        '''
        TrialSubmissions: Teachers/TAs can see all. Students can only see their own.
        '''
        key = f'TRIAL_SUBMISSION_PERMISSION_{self.id}_{user.id}_{self.problem.id}'
        cache = RedisCache()
        if (v := cache.get(key)) is not None:
            return self.Permission(int(v))

        cap = self.Permission(0)
        try:
            # Teachers/TAs (Grade permission) can see all
            problem_courses = map(Course, self.problem.courses)
            if any(
                    c.own_permission(user) & Course.Permission.GRADE
                    for c in problem_courses):
                cap = self.Permission.MANAGER
            # Students can only see their own
            elif user.username == self.user.username:
                cap = self.Permission.STUDENT
        except Exception as e:
            self.logger.error(f"Error calculating permission for {self}: {e}")
            cap = self.Permission(0)

        cache.set(key, cap.value, 60)
        return cap

    def _get_droppable_fields(self) -> set:
        # TrialSubmission has 'custom_input'
        return super()._get_droppable_fields() | {'custom_input'}

    # --- TrialSubmission-specific Classmethods ---

    @staticmethod
    def count():
        return len(engine.TrialSubmission.objects)

    @classmethod
    def filter(
        cls,
        user,
        offset: int = 0,
        count: int = -1,
        problem: Optional[Union[Problem, int]] = None,
        q_user: Optional[Union[User, str]] = None,
        status: Optional[int] = None,
        language_type: Optional[Union[List[int], int]] = None,
        course: Optional[Union[Course, str]] = None,
        before: Optional[datetime] = None,
        after: Optional[datetime] = None,
        sort_by: Optional[str] = None,
        with_count: bool = False,
        ip_addr: Optional[str] = None,
    ):
        # This logic is identical to Submission.filter, but
        # queries engine.TrialSubmission

        # --- (Validation logic, identical to Submission.filter) ---
        if before is not None and after is not None:
            if after > before:
                raise ValueError('the query period is empty')
        if offset < 0:
            raise ValueError(f'offset must >= 0!')
        if count < -1:
            raise ValueError(f'count must >=-1!')
        if sort_by is not None and sort_by not in ['runTime', 'memoryUsage']:
            raise ValueError(f'can only sort by runTime or memoryUsage')
        wont_have_results = False
        if isinstance(problem, int):
            problem = Problem(problem).obj
            if problem is None:
                wont_have_results = True
        if isinstance(q_user, str):
            q_user = User(q_user)
            if not q_user:
                wont_have_results = True
            q_user = q_user.obj
        if isinstance(course, str):
            course = Course(course)
            if not course:
                wont_have_results = True
        # problem's query key
        p_k = 'problem'
        if course:
            problems = Problem.get_problem_list(
                user,
                course=course.course_name,
            )
            if problem is None:
                p_k = 'problem__in'
                problem = problems
            elif problem not in problems:
                wont_have_results = True
        if wont_have_results:
            return ([], 0) if with_count else []
        if isinstance(language_type, int):
            language_type = [language_type]
        # --- (End of validation) ---

        q = {
            p_k: problem,
            'status': status,
            'language__in': language_type,
            'user': q_user,
            'ip_addr': ip_addr,
            'timestamp__lte': before,
            'timestamp__gte': after,
        }
        q = {k: v for k, v in q.items() if v is not None}

        # Query engine.TrialSubmission
        submissions = engine.TrialSubmission.objects(
            **q).order_by(sort_by if sort_by is not None else '-timestamp')

        submission_count = submissions.count()
        # truncate
        if count == -1:
            submissions = submissions[offset:]
        else:
            submissions = submissions[offset:offset + count]

        submissions = list(cls(s) for s in submissions)
        if with_count:
            return submissions, submission_count
        return submissions

    @classmethod
    def add(
        cls,
        problem_id: int,
        username: str,
        lang: int,
        timestamp: Optional[date] = None,
        ip_addr: Optional[str] = None,
    ) -> 'TrialSubmission':
        '''
        Insert a new test submission into db
        '''
        user = User(username)
        if not user:
            raise engine.DoesNotExist(f'{user} does not exist')
        problem = Problem(problem_id)
        if not problem:
            raise engine.DoesNotExist(f'{problem} dose not exist')

        if not problem.test_mode_enabled:
            raise PermissionError("Test mode is not enabled for this problem.")

        if timestamp is None:
            timestamp = datetime.now()

        # create a new trial submission
        submission = engine.TrialSubmission(problem=problem.obj,
                                            user=user.obj,
                                            language=lang,
                                            timestamp=timestamp,
                                            ip_addr=ip_addr)
        submission.save()
        return cls(submission.id)
