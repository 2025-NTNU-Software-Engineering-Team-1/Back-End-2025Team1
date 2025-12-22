import io
from flask import Blueprint, request, current_app, send_file
from datetime import datetime, timezone

from .utils import *
from .auth import *
from mongo.submission import TrialSubmission
from mongo.user import User, Role
from mongo.utils import MinioClient
from mongo import engine
from werkzeug.datastructures import FileStorage
import zipfile

__all__ = ["trial_submission_api"]
trial_submission_api = Blueprint("trial_submission_api", __name__)


def is_zipfile(file):
    return zipfile.is_zipfile(file)


@trial_submission_api.route("/test", methods=["GET"])
def test_endpoint():
    """
    Simple test endpoint to verify the trial_submission API is working
    """
    return HTTPResponse("Trial submission API is working!",
                        data={"status": "ok"})


@trial_submission_api.route("/download-testcases", methods=["GET"])
def download_custom_testcases():
    """
    Download custom test cases ZIP from MinIO.
    This is called by the sandbox to fetch user-uploaded custom test cases.
    
    Query params:
        token: Sandbox token for authentication
        path: MinIO path to the custom testcases ZIP
    """
    from mongo.sandbox import find_by_token

    # Verify sandbox token
    token = request.args.get("token", "")
    if not token or find_by_token(token) is None:
        current_app.logger.warning("Invalid token for download-testcases")
        return HTTPError("Invalid token", 401)

    # Get MinIO path
    minio_path = request.args.get("path")
    if not minio_path:
        return HTTPError("Missing path parameter", 400)

    # Download from MinIO
    try:
        minio = MinioClient()
        resp = minio.client.get_object(minio.bucket, minio_path)
        content = resp.read()
        resp.close()
        resp.release_conn()

        return send_file(io.BytesIO(content),
                         mimetype='application/zip',
                         as_attachment=True,
                         download_name='custom_testcases.zip')
    except Exception as e:
        current_app.logger.error(
            f"Failed to download testcases from {minio_path}: {e}")
        return HTTPError(f"Failed to download testcases: {e}", 500)


@trial_submission_api.put("/<trial_id>/files")
@login_required
def upload_trial_files(user, trial_id: str):
    """
    Upload code.zip and optional custom_testcases.zip for a Trial Submission.
    Frontend must first create Trial_Submission_Id via /problem/<id>/trial/request.
    """
    current_app.logger.info(f"Uploading trial files for trial_id: {trial_id}")

    # Validate multipart
    if not request.files:
        current_app.logger.warning(
            f"No files provided in request for trial_id: {trial_id}")
        return HTTPError("No files provided.", 400)

    code_file: FileStorage = request.files.get('code')
    custom_file: FileStorage = request.files.get('custom_testcases')

    if code_file is None:
        current_app.logger.warning(
            f"Missing 'code' file in request for trial_id: {trial_id}")
        return HTTPError("Missing code file.", 400)

    # Load submission
    try:
        ts = TrialSubmission(trial_id)
    except Exception as e:
        # Handle both invalid id format and non-existent ids uniformly
        current_app.logger.warning(
            f"Trial submission not found for trial_id: {trial_id}. Error: {str(e)}"
        )
        return HTTPError("Trial submission not found.", 404)

    # Permission: owner or teacher/admin
    req_user = User(user.username)
    # ts.user is a ReferenceField(User) on engine document; compare username
    ts_owner_username = getattr(ts.obj, 'user', None)
    try:
        if ts_owner_username and hasattr(ts_owner_username, 'username'):
            ts_owner_username = ts_owner_username.username
    except Exception as e:
        current_app.logger.error(
            f"Error retrieving owner for trial_id {trial_id}: {str(e)}")

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role <= 1  # 0 admin, 1 teacher

    if not (is_owner or is_staff):
        current_app.logger.warning(
            f"Permission denied. User {req_user.username} tried to upload to trial {trial_id}"
        )
        return HTTPError("Forbidden.", 403)

    # Validate code zip (check compressed and uncompressed sizes)
    code_bytes = code_file.read()
    if not is_zipfile(io.BytesIO(code_bytes)):
        current_app.logger.warning(
            f"Invalid zip format for code file. trial_id: {trial_id}")
        return HTTPError("Code file must be a valid zip.", 400)

    # compressed size limit
    if len(code_bytes) > 10 * 1024 * 1024:
        current_app.logger.warning(
            f"Code file compressed size limit exceeded ({len(code_bytes)} bytes). trial_id: {trial_id}"
        )
        return HTTPError("Code file too large (>10MB).", 400)

    # uncompressed size limit
    try:
        import zipfile as _zip
        with _zip.ZipFile(io.BytesIO(code_bytes)) as _zf:
            uncompressed_total = sum(i.file_size for i in _zf.infolist())
        if uncompressed_total > 10 * 1024 * 1024:
            current_app.logger.warning(
                f"Code file uncompressed size limit exceeded ({uncompressed_total} bytes). trial_id: {trial_id}"
            )
            return HTTPError("Code file too large (>10MB).", 400)
    except Exception as e:
        # Zip 解析失敗屬於 Exception，雖然結果是回傳 400，但紀錄 Exception 有助於分析是否為攻擊或特殊格式
        current_app.logger.error(
            f"Exception while reading code zip structure for trial {trial_id}: {str(e)}"
        )
        return HTTPError("Code file must be a valid zip.", 400)

    # Optional custom testcases
    custom_bytes = None
    if custom_file:
        custom_bytes = custom_file.read()
        if not is_zipfile(io.BytesIO(custom_bytes)):
            current_app.logger.warning(
                f"Invalid zip format for custom testcases. trial_id: {trial_id}"
            )
            return HTTPError("Custom testcases must be a valid zip.", 400)

        # compressed limit
        if len(custom_bytes) > 5 * 1024 * 1024:
            current_app.logger.warning(
                f"Custom testcases compressed size limit exceeded. trial_id: {trial_id}"
            )
            return HTTPError("Custom testcases file too large (>5MB).", 400)

        # uncompressed limit
        try:
            import zipfile as _zip
            with _zip.ZipFile(io.BytesIO(custom_bytes)) as _zf:
                uncompressed_total = sum(i.file_size for i in _zf.infolist())
            if uncompressed_total > 5 * 1024 * 1024:
                current_app.logger.warning(
                    f"Custom testcases uncompressed size limit exceeded. trial_id: {trial_id}"
                )
                return HTTPError("Custom testcases file too large (>5MB).",
                                 400)
        except Exception as e:
            current_app.logger.error(
                f"Exception while reading custom zip structure for trial {trial_id}: {str(e)}"
            )
            return HTTPError("Custom testcases must be a valid zip.", 400)

    # Store in MinIO
    minio = MinioClient()
    now_tag = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    code_path = f"trial/{trial_id}/code-{now_tag}.zip"
    try:
        minio.client.put_object(minio.bucket,
                                code_path,
                                io.BytesIO(code_bytes),
                                length=len(code_bytes))
    except Exception as e:
        # System-level error (e.g. MinIO down)
        current_app.logger.error(
            f"MinIO upload failed for trial {trial_id} (code): {str(e)}")
        return HTTPError(f"Failed to upload code: {e}", 500)

    custom_path = None
    if custom_bytes:
        custom_path = f"trial/{trial_id}/custom-{now_tag}.zip"
        try:
            minio.client.put_object(minio.bucket,
                                    custom_path,
                                    io.BytesIO(custom_bytes),
                                    length=len(custom_bytes))
        except Exception as e:
            # 系統層級錯誤
            current_app.logger.error(
                f"MinIO upload failed for trial {trial_id} (custom): {str(e)}")
            return HTTPError(f"Failed to upload custom testcases: {e}", 500)

    # Update submission document
    try:
        if hasattr(ts.obj, 'code_minio_path'):
            ts.obj.code_minio_path = code_path
        if custom_path and hasattr(ts.obj, 'custom_input_minio_path'):
            ts.obj.custom_input_minio_path = custom_path
            # If custom provided, ensure flag false
            if hasattr(ts.obj, 'use_default_case'):
                ts.obj.use_default_case = False
        ts.obj.save()
    except Exception as e:
        # 資料庫寫入失敗，嚴重錯誤
        current_app.logger.error(
            f"Database save failed for trial submission {trial_id}: {str(e)}")
        return HTTPError(f"Failed to update trial submission: {e}", 500)

    # Enqueue judge (non-blocking)
    try:
        ts.send()
    except Exception as e:
        current_app.logger.warning(
            f"Failed to send trial submission to sandbox: {e}")
        # Continue anyway - files are uploaded

    current_app.logger.info(
        f"Successfully uploaded files for trial_id: {trial_id}")
    return HTTPResponse("Files received.",
                        data={
                            "Trial_Submission_Id": str(ts.id),
                            "Code_Path": code_path,
                            "Custom_Testcases_Path": custom_path,
                        })


@trial_submission_api.route("/<trial_id>", methods=["GET"])
@login_required
def get_trial_record(user, trial_id: str):
    """
    Get detailed record of a Trial Submission including stdout/stderr.
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # 權限檢查 (參考 upload_trial_files 的邏輯)
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    try:
        if ts_owner_username and hasattr(ts_owner_username, 'username'):
            ts_owner_username = ts_owner_username.username
    except Exception:
        pass

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # 回傳格式化後的資料
    try:
        data = ts.get_trial_api_info()
        return HTTPResponse("Success", data=data)
    except Exception as e:
        current_app.logger.error(f"Error retrieval trial info: {e}")
        return HTTPError("Internal Server Error", 500)


@trial_submission_api.route("/<trial_id>/download/case", methods=["GET"])
@login_required
def download_trial_case(user, trial_id: str):
    """
    Download the output zip for a specific task/case.
    Query Param: task_index (int)
    """
    try:
        task_index = int(request.args.get('task_index'))
    except (TypeError, ValueError):
        return HTTPError("Invalid task_index.", 400)

    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # 權限檢查
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    if hasattr(ts_owner_username, 'username'):
        ts_owner_username = ts_owner_username.username

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # 取得 Raw Output (Zip file bytes)
    # 假設每個 Task 只有一個 Case (idx=0)
    try:
        if task_index < 0 or task_index >= len(ts.tasks):
            return HTTPError("Task index out of range.", 404)

        case_result = ts.tasks[task_index].cases[0]
        # _get_output_raw 是 BaseSubmission 的方法，回傳 BytesIO
        output_io = ts._get_output_raw(case_result)
        output_io.seek(0)
    except (IndexError, AttributeError):
        return HTTPError("Output not found (pending or error).", 404)
    except Exception as e:
        current_app.logger.error(f"Error downloading case: {e}")
        return HTTPError("Internal Error", 500)

    filename = f"trial-{trial_id}-task{task_index}.zip"

    return send_file(output_io,
                     mimetype='application/zip',
                     as_attachment=True,
                     download_name=filename)


@trial_submission_api.route("/<trial_id>/download", methods=["GET"])
@login_required
def download_trial_all(user, trial_id: str):
    """
    Download all case outputs zipped together.
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # 權限檢查
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    if hasattr(ts_owner_username, 'username'):
        ts_owner_username = ts_owner_username.username

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # Generator
    def file_iterator():
        for i, task in enumerate(ts.tasks):
            if not task.cases:
                continue

            try:
                case_result = task.cases[0]
                # 讀取 Raw Zip Bytes
                case_io = ts._get_output_raw(case_result)
                case_data = case_io.read()

                # Yield (檔名, 資料)
                yield (f"task_{i}.zip", case_data)

            except Exception:
                # 錯誤時寫入一個錯誤訊息檔
                yield (f"task_{i}_error.txt", b"Output not found")

    return stream_zip_response(file_iterator, f"trial-{trial_id}.zip")


# === Sandbox Result Callback API ===


@trial_submission_api.route("/<trial_id>/result", methods=["PUT"])
@Request.json('tasks: list', 'token: str')
def on_trial_result(trial_id: str, tasks: list, token: str):
    """
    Receive judging results from sandbox for a Trial Submission.
    This is called by the sandbox after it finishes judging.
    
    Expected JSON body:
    {
        "tasks": [...],  # List of task results
        "token": "...",  # Sandbox token for verification
        "staticAnalysis": {...}  # Optional SA results
    }
    """
    # Validate trial submission exists
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        current_app.logger.warning(
            f"Trial result callback for non-existent trial_id: {trial_id}")
        return HTTPError("Trial submission not found.", 404)
    except Exception as e:
        current_app.logger.error(
            f"Error loading trial submission {trial_id}: {e}")
        return HTTPError("Invalid trial submission ID.", 400)

    # Verify sandbox token
    if not TrialSubmission.verify_token(trial_id, token):
        current_app.logger.warning(
            f"Invalid token for trial result callback: {trial_id}")
        return HTTPError("Invalid or expired token.", 403)

    # Process the results
    try:
        static_analysis = request.json.get('staticAnalysis')

        # TrialSubmission inherits process_result from BaseSubmission
        ts.process_result(
            tasks,
            static_analysis=static_analysis,
            # Trial submissions don't use checker/scoring in the same way
            checker=None,
            scoring=None,
            status_override=None,
        )

        current_app.logger.info(
            f"Successfully processed trial result for {trial_id}")
        return HTTPResponse(f"Trial submission {trial_id} result received.")

    except (ValueError, KeyError) as e:
        current_app.logger.error(
            f"Invalid data in trial result for {trial_id}: {e}")
        return HTTPError(f"Invalid result data: {e}", 400)
    except Exception as e:
        current_app.logger.error(
            f"Error processing trial result for {trial_id}: {e}")
        return HTTPError(f"Failed to process result: {e}", 500)
