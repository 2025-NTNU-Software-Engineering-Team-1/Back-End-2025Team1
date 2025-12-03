import hashlib
from zipfile import ZipFile
import io

from mongo import Submission
from mongo.problem import Problem
from mongo.utils import MinioClient
from tests import utils
from tests.utils import problem


def test_resource_data_meta_and_checksum(app):
    with app.app_context():
        owner = utils.user.create_user(role=1)
        course = utils.course.create_course(teacher=owner)
        problem = utils.problem.create_problem(course=course)
        pid = problem.id
        prob = Problem(pid)
        # prepare resource asset in minio
        mc = MinioClient()
        data = b"res"
        path = f"problem/{pid}/resource_data/resource_data.zip"
        mc.client.put_object(
            mc.bucket,
            path,
            io.BytesIO(data),
            len(data),
            part_size=5 * 1024 * 1024,
        )
        prob.config = {
            "resourceData": True,
            "assetPaths": {
                "resource_data": path,
            },
        }
        prob.save()
        token = Submission.config().sandbox_instances[0].token
        client = app.test_client()
        rv = client.get(
            f"/problem/{pid}/meta",
            query_string={"token": token},
        )
        assert rv.status_code == 200
        meta = rv.get_json()["data"]
        assert meta["resourceData"]
        assert meta["assetPaths"]["resource_data"] == path

        rv2 = client.get(
            f"/problem/{pid}/asset-checksum",
            query_string={
                "token": token,
                "assetType": "resource_data",
            },
        )
        assert rv2.status_code == 200
        checksum = rv2.get_json()["data"]["checksum"]
        assert checksum == hashlib.md5(data).hexdigest()
