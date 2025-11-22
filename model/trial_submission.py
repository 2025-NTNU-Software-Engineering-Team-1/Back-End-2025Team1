import io
from flask import Blueprint, request, current_app
from datetime import datetime

from .utils import *
from .auth import *
from mongo.submission import TrialSubmission
from mongo.user import User
from mongo.utils import MinioClient
from werkzeug.datastructures import FileStorage
from zipfile import is_zipfile

__all__ = ["trial_submission_api"]
trial_submission_api = Blueprint("trial_submission_api", __name__)


@trial_submission_api.route("/test", methods=["GET"])
def test_endpoint():
    """
    Simple test endpoint to verify the trial_submission API is working
    """
    return HTTPResponse("Trial submission API is working!",
                        data={"status": "ok"})


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
        pass

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
    now_tag = datetime.utcnow().strftime('%Y%m%d%H%M%S')
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

    # (Optional) enqueue judge (non-blocking) — placeholder
    # try:
    #     ts.send()
    # except Exception:
    #     pass  # Ignore queue failures for now

    current_app.logger.info(
        f"Successfully uploaded files for trial_id: {trial_id}")
    return HTTPResponse("Files received.",
                        data={
                            "Trial_Submission_Id": str(ts.id),
                            "Code_Path": code_path,
                            "Custom_Testcases_Path": custom_path,
                        })
