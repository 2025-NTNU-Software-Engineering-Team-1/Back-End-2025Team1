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


def test_sa_skip_uploads_report(app, setup_minio):
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


def test_sa_report_path_preserved(app, setup_minio):
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


def test_sa_disabled_clears_legacy_status(app, setup_minio):
    """When SA is disabled (static_analysis=None), old SA values should be cleared."""
    with app.app_context():
        user = utils.user.create_user()
        problem = _simple_problem()
        submission = _make_submission(user, problem)

        # First, process with SA fail to set legacy values
        submission.process_result(
            _dummy_tasks(problem),
            static_analysis={
                "status": "fail",
                "message": "Found violations",
                "report": "Some violations report",
            },
        )
        submission.reload()

        # Verify SA fail was recorded
        assert submission.sa_status == 1
        assert submission.sa_message == "Found violations"

        # Now process again with SA disabled (None) - simulating config removal
        submission.process_result(
            _dummy_tasks(problem),
            static_analysis=None,
        )
        submission.reload()

        # SA fields should be cleared
        assert submission.sa_status is None
        assert submission.sa_message is None
        assert submission.sa_report is None


def test_checker_payload_summary_and_artifact_upload(app, setup_minio):
    with app.app_context():
        user = utils.user.create_user()
        problem = _simple_problem()
        submission = _make_submission(user, problem)

        tasks = _dummy_tasks(problem)
        checker_payload = {
            "type":
            "custom",
            "messages": [
                {
                    "case": "0000",
                    "status": "WA",
                    "message": "diff at line 3"
                },
                {
                    "case": "0001",
                    "status": "AC",
                    "message": ""
                },
            ],
            "artifacts": {
                "checkResult": "checker body text"
            },
        }

        submission.process_result(
            tasks,
            static_analysis=None,
            checker=checker_payload,
        )
        submission.reload()

        assert "diff at line 3" in (submission.checker_summary or "")
        assert submission.checker_artifacts_path

        # ensure uploaded
        minio_client = MinioClient()
        obj = minio_client.client.stat_object(
            minio_client.bucket, submission.checker_artifacts_path)
        assert obj is not None
