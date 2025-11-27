import pytest

from tests import utils
from mongo.utils import MinioClient
from mongo import Submission


def setup_function(_):
    utils.drop_db()


def teardown_function(_):
    utils.drop_db()


def _simple_problem():
    return utils.problem.create_problem(
        test_case_info=utils.problem.create_test_case_info(
            language=0,
            task_len=1,
            case_count_range=(1, 1),
        ))


def _make_submission(user, problem):
    return utils.submission.create_submission(
        user=user,
        problem=problem,
        lang=0,
        code="int main() { return 0; }",
    )


def _dummy_tasks(problem):
    tasks = []
    for task in problem.test_case.tasks:
        cases = []
        for _ in range(task.case_count):
            cases.append({
                "exitCode": 0,
                "status": "AC",
                "stdout": "",
                "stderr": "",
                "execTime": task.time_limit,
                "memoryUsage": task.memory_limit,
            })
        tasks.append(cases)
    return tasks


def test_sa_skip_uploads_report(app):
    with app.app_context():
        user = utils.user.create_user()
        problem = _simple_problem()
        submission = _make_submission(user, problem)

        submission.process_result(
            _dummy_tasks(problem),
            static_analysis={
                "status": "skip",
                "message": "libclang missing",
                "report": "SA report body",
            },
        )
        submission.reload()

        assert submission.sa_status is None
        assert submission.sa_message == "libclang missing"
        assert submission.sa_report == "SA report body"
        assert submission.sa_report_path

        # ensure report is uploaded
        minio_client = MinioClient()
        obj = minio_client.client.stat_object(minio_client.bucket,
                                              submission.sa_report_path)
        assert obj is not None


def test_sa_report_path_preserved(app):
    with app.app_context():
        user = utils.user.create_user()
        problem = _simple_problem()
        submission = _make_submission(user, problem)

        submission.process_result(
            _dummy_tasks(problem),
            static_analysis={
                "status": "pass",
                "message": "ok",
                "report": "hello",
                "reportPath": "custom/report.txt",
            },
        )
        submission.reload()

        assert submission.sa_status == 0
        assert submission.sa_report_path == "custom/report.txt"
