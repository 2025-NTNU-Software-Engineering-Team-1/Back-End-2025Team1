import io
from zipfile import ZipFile
from mongo import Submission, engine
from mongo.utils import MinioClient
from tests.test_submission import _create_submission_with_artifact


def test_download_task_artifact_zip_aggregation(app, forge_client):
    with app.app_context():
        submission, _, _, owner = _create_submission_with_artifact(app, [0])
        client = forge_client(owner.username)

        # 模擬兩個 case 各自有 zip
        mc = MinioClient()
        case_objs = []
        for case_index in (0, 1):
            buf = io.BytesIO()
            with ZipFile(buf, "w") as zf:
                zf.writestr(f"stdout_{case_index}",
                            f"out{case_index}".encode())
            buf.seek(0)
            object_name = submission._generate_output_minio_path(0, case_index)
            mc.client.put_object(
                mc.bucket,
                object_name,
                io.BytesIO(buf.getvalue()),
                len(buf.getvalue()),
                part_size=5 * 1024 * 1024,
                content_type='application/zip',
            )
            case_objs.append(
                engine.CaseResult(
                    status=0,
                    exec_time=10,
                    memory_usage=128,
                    output_minio_path=object_name,
                ))
        submission.tasks = [
            engine.TaskResult(
                status=0,
                exec_time=10,
                memory_usage=128,
                score=100,
                cases=case_objs,
            )
        ]
        submission.save()

        rv = client.get(f'/submission/{submission.id}/artifact/zip/0')
        assert rv.status_code == 200, rv.get_json()
        with ZipFile(io.BytesIO(rv.data)) as zf:
            names = sorted(zf.namelist())
        assert 'task_00/case_00/stdout_0' in names
        assert 'task_00/case_01/stdout_1' in names


def test_artifact_enabled_string_and_index(app):
    with app.app_context():
        submission, _, _, _ = _create_submission_with_artifact(app, [0])
        # string mode
        submission.problem.config['artifactCollection'] = ['zip']
        assert submission.is_artifact_enabled(0)
        # index mode
        submission.problem.config['artifactCollection'] = [0]
        assert submission.is_artifact_enabled(0)


def test_upload_case_artifact_invalid_token(app):
    with app.app_context():
        submission, _, _, _ = _create_submission_with_artifact(app, [0])
        client = app.test_client()
        rv = client.put(
            f'/submission/{submission.id}/artifact/upload/case',
            query_string={
                'task': 0,
                'case': 0,
                'token': 'bad'
            },
            data=b'123',
            content_type='application/zip',
        )
        assert rv.status_code == 401


def test_artifact_collector_limits(monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[2]
    sandbox_path = repo_root / "Sandbox"
    sys.path.insert(0, str(sandbox_path))
    from dispatcher.artifact_collector import ArtifactCollector, _CASE_FILE_LIMIT

    class DummyResp:

        def __init__(self, ok=True, status_code=200, text="ok"):
            self.ok = ok
            self.status_code = status_code
            self.text = text

    calls = []

    def fake_put(url, params=None, data=None, timeout=None, headers=None):
        calls.append({"url": url, "len": len(data or b"")})
        return DummyResp()

    monkeypatch.setattr('dispatcher.artifact_collector.requests.put', fake_put)

    workdir = tmp_path / "submissions" / "s1" / "src"
    workdir.mkdir(parents=True, exist_ok=True)
    big = workdir / "big.bin"
    big.write_bytes(b"0" * (_CASE_FILE_LIMIT + 1))
    small = workdir / "ok.txt"
    small.write_text("ok")

    collector = ArtifactCollector()
    collector.snapshot_before_case("s1", 0, 0, workdir)
    # modify small so it is detected as changed
    small.write_text("changed")
    collector.record_case_artifact("s1", 0, 0, workdir, stdout="", stderr="")
    collector.upload_all("s1")

    # 大檔被跳過，只應上傳一次 case zip
    assert len(calls) == 1
    assert 'artifact/upload/case' in calls[0]['url']
