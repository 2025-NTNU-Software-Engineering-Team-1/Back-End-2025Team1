import io
import zipfile
from pathlib import Path

import pytest
from mongo import Problem
from tests import utils
from tests.base_tester import BaseTester, random_string


def load_testcase_zip() -> io.BytesIO:
    base = Path(__file__).resolve(
    ).parent / "problem_test_case" / "default" / "test_case.zip"
    with open(base, "rb") as handle:
        return io.BytesIO(handle.read())


@pytest.mark.usefixtures("setup_minio")
class TestProblemImportExport(BaseTester):

    def _create_problem_with_testcase(self, course,
                                      owner_username: str) -> Problem:
        test_case_info = utils.problem.create_test_case_info(
            language=1,
            task_len=1,
            case_count_range=(1, 1),
        )
        problem = utils.problem.create_problem(
            course=course,
            owner=owner_username,
            name=f"export-{random_string(6)}",
            status=0,
            test_case_info=test_case_info,
        )
        test_zip = load_testcase_zip()
        problem.update_test_case(test_zip)
        problem.reload()
        return problem

    def test_export_import_roundtrip(self, forge_client):
        course = utils.course.create_course(
            name=f"course-{random_string(6)}",
            teacher="teacher",
        )
        problem = self._create_problem_with_testcase(course, "admin")

        client_admin = forge_client("admin")
        rv = client_admin.get(f"/problem/{problem.problem_id}/export")
        assert rv.status_code == 200

        zf = zipfile.ZipFile(io.BytesIO(rv.data))
        assert "manifest.json" in zf.namelist()
        assert "meta.json" in zf.namelist()
        assert "testcase.zip" in zf.namelist()

        meta = zf.read("meta.json")
        assert b'"problemName"' in meta
        assert b'"testCase"' in meta

        import_payload = {
            "file": (io.BytesIO(rv.data), "problem.noj.zip"),
            "course": course.course_name,
        }
        rv = client_admin.post(
            "/problem/import",
            data=import_payload,
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200, rv.get_json()
        data = rv.get_json()["data"]
        new_problem_id = data["problemId"]
        imported = Problem(new_problem_id)
        assert imported.problem_name == problem.problem_name
        assert imported.is_test_case_ready()

    def test_export_import_batch(self, forge_client):
        course = utils.course.create_course(
            name=f"course-{random_string(6)}",
            teacher="teacher",
        )
        problem_a = self._create_problem_with_testcase(course, "admin")
        problem_b = self._create_problem_with_testcase(course, "admin")

        client_admin = forge_client("admin")
        rv = client_admin.post(
            "/problem/export-batch",
            json={"problemIds": [problem_a.problem_id, problem_b.problem_id]},
        )
        assert rv.status_code == 200

        import_payload = {
            "file": (io.BytesIO(rv.data), "problems_export.noj.zip"),
            "course": course.course_name,
        }
        rv = client_admin.post(
            "/problem/import-batch",
            data=import_payload,
            content_type="multipart/form-data",
        )
        assert rv.status_code == 200, rv.get_json()
        data = rv.get_json()["data"]
        imported = data.get("imported") or []
        assert len(imported) == 2
        for item in imported:
            new_id = item["newId"]
            assert Problem(new_id)
