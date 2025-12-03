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
    pid = utils.problem.create_problem(course=course)
    prob = Problem(pid)
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
    # inject tasks structure to match resource naming
    tasks = prob.test_case.tasks or []
    if not tasks:
        prob.test_case.tasks = [
            engine.Task(
                task_score=100,
                case_count=2,
                memory_limit=134218,
                time_limit=1000,
            )
        ]
    prob.config = prob.config or {}
    prob.config.update({
        "resourceData": True,
        "assetPaths": {
            "resource_data": path
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
                "asset_type": "resource_data"
            },
        )
        assert rv.status_code == 200
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
        assert meta["config"]["resourceData"]
        assert meta["assetPaths"]["resource_data"] == path
