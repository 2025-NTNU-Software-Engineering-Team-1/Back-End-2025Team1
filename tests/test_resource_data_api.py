import io
import hashlib
from zipfile import ZipFile

from mongo.problem import Problem
from mongo import engine
from mongo.submission import Submission
from mongo.utils import MinioClient
from tests import utils
import mongo.config


def _create_problem_with_resource(app):
    owner = utils.user.create_user(role=1)
    course = utils.course.create_course(teacher=owner)
    problem_obj = utils.problem.create_problem(course=course)
    prob = Problem(problem_obj.id)
    pid = prob.problem_id
    mc = MinioClient()
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("0000_config.txt", "cfg0")
        zf.writestr("0001_config.txt", "cfg1")
    buf.seek(0)
    path = f"problem/{pid}/resource_data/resource_data.zip"
    mc.client.put_object(
        mc.bucket,
        path,
        io.BytesIO(buf.getvalue()),
        len(buf.getvalue()),
        part_size=5 * 1024 * 1024,
    )
    prob.config = prob.config or {}
    prob.config.update({
        "resourceData": True,
        "assetPaths": {
            "resource_data": path
        },
    })
    prob.save()
    return pid, path, owner


def _create_problem_with_teacher_resource(app):
    owner = utils.user.create_user(role=1)
    course = utils.course.create_course(teacher=owner)
    problem_obj = utils.problem.create_problem(course=course)
    prob = Problem(problem_obj.id)
    pid = prob.problem_id
    mc = MinioClient()
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("0000_teacher.txt", "t0")
        zf.writestr("0001_teacher.txt", "t1")
    buf.seek(0)
    path = f"problem/{pid}/resource_data_teacher/resource_data_teacher.zip"
    mc.client.put_object(
        mc.bucket,
        path,
        io.BytesIO(buf.getvalue()),
        len(buf.getvalue()),
        part_size=5 * 1024 * 1024,
    )
    prob.config = prob.config or {}
    prob.config.update({
        "resourceDataTeacher": True,
        "assetPaths": {
            "resource_data_teacher": path
        },
    })
    prob.save()
    return pid, path, owner


def test_resource_data_checksum_and_meta(app):
    with app.app_context():
        pid, path, owner = _create_problem_with_resource(app)
        token = Submission.config().sandbox_instances[0].token
        client = app.test_client()
        rv = client.get(
            f"/problem/{pid}/asset-checksum",
            query_string={
                "token": token,
                "assetType": "resource_data"
            },
        )
        assert rv.status_code == 200, rv.data
        checksum = rv.get_json()["data"]["checksum"]
        mc = MinioClient()
        data = mc.download_file(path)
        assert checksum == hashlib.md5(data).hexdigest()

        rv2 = client.get(
            f"/problem/{pid}/meta",
            query_string={"token": token},
        )
        assert rv2.status_code == 200
        meta = rv2.get_json()["data"]
        assert meta["resourceData"]
        assert meta["assetPaths"]["resource_data"] == path


def test_resource_data_teacher_checksum_and_meta(app):
    with app.app_context():
        pid, path, owner = _create_problem_with_teacher_resource(app)
        token = Submission.config().sandbox_instances[0].token
        client = app.test_client()
        rv = client.get(
            f"/problem/{pid}/asset-checksum",
            query_string={
                "token": token,
                "assetType": "resource_data_teacher"
            },
        )
        assert rv.status_code == 200, rv.data
        checksum = rv.get_json()["data"]["checksum"]
        mc = MinioClient()
        data = mc.download_file(path)
        assert checksum == hashlib.md5(data).hexdigest()

        rv2 = client.get(
            f"/problem/{pid}/meta",
            query_string={"token": token},
        )
        assert rv2.status_code == 200
        meta = rv2.get_json()["data"]
        assert meta["resourceDataTeacher"]
        assert meta["assetPaths"]["resource_data_teacher"] == path
