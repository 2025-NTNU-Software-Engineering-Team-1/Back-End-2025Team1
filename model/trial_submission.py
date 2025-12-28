import io
from flask import Blueprint, request, current_app, send_file
from datetime import datetime, timezone, timedelta

from .utils import *
from .auth import *
from mongo.submission import TrialSubmission
from mongo.user import User, Role
from mongo.utils import MinioClient
from mongo import engine
from mongo import sandbox
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


@trial_submission_api.route("/check-rejudge-permission/<int:problem_id>",
                            methods=["GET"])
@login_required
def check_rejudge_permission(user, problem_id: int):
    """
    Check if the current user has permission to rejudge trial submissions for a problem.
    Returns True if:
    - User is Admin, OR
    - User has GRADE permission in any course containing this problem
    """
    from mongo.problem import Problem
    from mongo.user import Role

    # Admin can always rejudge
    if user.role == Role.ADMIN:
        return HTTPResponse("", data={"can_rejudge": True})

    # Check if user has GRADE permission in any course containing this problem
    try:
        problem = Problem(problem_id)
        if not problem:
            return HTTPError(f"Problem {problem_id} not found.", 404)

        from mongo.course import Course
        problem_courses = map(Course, problem.courses)
        has_permission = any(
            c.own_permission(user) & Course.Permission.GRADE
            for c in problem_courses)

        return HTTPResponse("", data={"can_rejudge": has_permission})
    except Exception as e:
        current_app.logger.error(f"Error checking rejudge permission: {e}")
        return HTTPError("Permission check failed.", 500)


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
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

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

    # macOS zip 檢測
    is_valid, sanitize_error = zip_sanitize(code_bytes)
    if not is_valid:
        current_app.logger.warning(
            f"Code file rejected by sanitize: {sanitize_error}. trial_id: {trial_id}"
        )
        return HTTPError(sanitize_error, 400)

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

        # macOS zip 檢測
        is_valid, sanitize_error = zip_sanitize(custom_bytes)
        if not is_valid:
            current_app.logger.warning(
                f"Custom testcases rejected by sanitize: {sanitize_error}. trial_id: {trial_id}"
            )
            return HTTPError(sanitize_error, 400)

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
            # 系統層級錯誤 - 回滾：刪除已上傳的 code
            current_app.logger.error(
                f"MinIO upload failed for trial {trial_id} (custom): {str(e)}")
            try:
                minio.client.remove_object(minio.bucket, code_path)
                current_app.logger.info(
                    f"Rolled back code upload for trial {trial_id}")
            except Exception as rollback_err:
                current_app.logger.warning(
                    f"Failed to rollback code upload for trial {trial_id}: {rollback_err}"
                )
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
        # Mark as judging now that files are uploaded
        ts.obj.status = -1
        ts.obj.last_send = datetime.now()
        ts.obj.score = -1
        ts.obj.exec_time = -1
        ts.obj.memory_usage = -1
        ts.obj.tasks = []
        if hasattr(ts.obj, 'output_fields_initialized'):
            ts.obj.output_fields_initialized = False
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
                            "trial_submission_id": str(ts.id),
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


@trial_submission_api.route("/<trial_id>/static-analysis", methods=["GET"])
@login_required
def get_trial_static_analysis(user, trial_id: str):
    """
    Get static analysis report for a Trial Submission.
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    if not ts.permission(user, TrialSubmission.Permission.FEEDBACK):
        return HTTPError("forbidden.", 403)

    report = ts.sa_report or ""
    report_url = None
    if ts.sa_report_path:
        try:
            minio_client = MinioClient()
            report_url = minio_client.client.get_presigned_url(
                "GET",
                minio_client.bucket,
                ts.sa_report_path,
                expires=timedelta(minutes=30),
            )
        except Exception:
            current_app.logger.exception("Failed to presign SA report")

    return HTTPResponse("", data={"report": report, "reportUrl": report_url})


@trial_submission_api.route("/<trial_id>/output/<int:task_no>/<int:case_no>",
                            methods=["GET"])
@login_required
def get_trial_output(user, trial_id: str, task_no: int, case_no: int):
    """
    Get stdout/stderr output for a specific case of a trial submission.
    Used for displaying CE (Compile Error) messages.
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # Permission check
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

    try:
        output = ts.get_single_output(task_no, case_no)
    except FileNotFoundError as e:
        return HTTPError(str(e), 400)
    except AttributeError as e:
        return HTTPError(str(e), 102)

    return HTTPResponse('ok', data=output)


@trial_submission_api.route("/<trial_id>/download/task/<int:task_index>",
                            methods=["GET"])
@login_required
def download_trial_task_artifact(user, trial_id: str, task_index: int):
    """
    Download all case artifacts for a specific task as a combined zip.
    Similar to /submission/<id>/artifact/zip/<task_index> for regular submissions.
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # Permission check
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    if hasattr(ts_owner_username, 'username'):
        ts_owner_username = ts_owner_username.username

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # Check trialResultDownloadable for students
    if is_owner and not is_staff:
        problem = Problem(ts.problem_id)
        if (problem.config or {}).get('trialResultDownloadable') is False:
            return HTTPError("Trial result download is disabled.", 403)

    # Build task artifact zip (combine all cases)
    try:
        if task_index < 0 or task_index >= len(ts.tasks):
            return HTTPError("Task index out of range.", 404)

        task = ts.tasks[task_index]
        if not task.cases:
            return HTTPError("No cases found for this task.", 404)

        from mongo.utils import MinioClient
        from zipfile import ZipFile, BadZipFile
        minio_client = MinioClient()
        artifact_buf = io.BytesIO()
        wrote_any_file = False

        with ZipFile(artifact_buf, 'w') as artifact_zip:
            for case_index, case in enumerate(task.cases):
                output_path = getattr(case, 'output_minio_path', None)
                if not output_path:
                    continue
                try:
                    data = minio_client.download_file(output_path)
                    with ZipFile(io.BytesIO(data)) as case_zip:
                        for name in case_zip.namelist():
                            arcname = f'case_{case_index:02d}/{name}'
                            artifact_zip.writestr(arcname, case_zip.read(name))
                            wrote_any_file = True
                except BadZipFile:
                    current_app.logger.warning(
                        f"Invalid zip for trial {trial_id} task {task_index} case {case_index}"
                    )
                    continue
                except Exception as e:
                    current_app.logger.warning(
                        f"Error reading case artifact: {e}")
                    continue

        if not wrote_any_file:
            return HTTPError("No artifacts available for this task.", 404)

        artifact_buf.seek(0)

    except Exception as e:
        current_app.logger.error(f"Error building task artifact: {e}")
        return HTTPError("Internal Error", 500)

    filename = f"trial-{trial_id}-task{task_index}-artifact.zip"

    return send_file(artifact_buf,
                     mimetype='application/zip',
                     as_attachment=True,
                     download_name=filename)


@trial_submission_api.route("/<trial_id>/download/case", methods=["GET"])
@login_required
def download_trial_case_artifact(user, trial_id: str):
    """
    Download the artifact zip for a specific case.
    Query Params: 
        task_index (int): Task index (required)
        case_index (int): Case index within the task (required)
    """
    try:
        task_index = int(request.args.get('task_index'))
        case_index = int(request.args.get('case_index', 0))
    except (TypeError, ValueError):
        return HTTPError("Invalid task_index or case_index.", 400)

    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # Permission check
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    if hasattr(ts_owner_username, 'username'):
        ts_owner_username = ts_owner_username.username

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # Check trialResultDownloadable for students
    if is_owner and not is_staff:
        problem = Problem(ts.problem_id)
        if (problem.config or {}).get('trialResultDownloadable') is False:
            return HTTPError("Trial result download is disabled.", 403)

    # Get case artifact zip
    try:
        if task_index < 0 or task_index >= len(ts.tasks):
            return HTTPError("Task index out of range.", 404)

        task = ts.tasks[task_index]
        if case_index < 0 or case_index >= len(task.cases):
            return HTTPError("Case index out of range.", 404)

        case_result = task.cases[case_index]
        # _get_output_raw 是 BaseSubmission 的方法，回傳 BytesIO
        output_io = ts._get_output_raw(case_result)
        output_io.seek(0)
    except (IndexError, AttributeError) as e:
        return HTTPError("Output not found (pending or error).", 404)
    except Exception as e:
        current_app.logger.error(f"Error downloading case: {e}")
        return HTTPError("Internal Error", 500)

    filename = f"trial-{trial_id}-task{task_index}-case{case_index}.zip"

    return send_file(output_io,
                     mimetype='application/zip',
                     as_attachment=True,
                     download_name=filename)


@trial_submission_api.route(
    "/<trial_id>/artifact/case/<int:task_no>/<int:case_no>", methods=["GET"])
@login_required
def get_trial_case_artifact_files(user, trial_id: str, task_no: int,
                                  case_no: int):
    """
    Get all files from case artifact zip including stdout, stderr, and other files.
    Returns files with appropriate encoding (text as string, images as base64).
    """
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    # Permission check (Reference: download_trial_case)
    req_user = User(user.username)
    ts_owner_username = getattr(ts.obj, 'user', None)
    if hasattr(ts_owner_username, 'username'):
        ts_owner_username = ts_owner_username.username

    is_owner = (req_user.username == ts_owner_username)
    is_staff = req_user.role in (Role.ADMIN, Role.TEACHER, Role.TA)

    if not (is_owner or is_staff):
        return HTTPError("Forbidden.", 403)

    # Check trialResultVisible for students
    if is_owner and not is_staff:
        problem = Problem(ts.problem_id)
        if (problem.config or {}).get('trialResultVisible') is False:
            return HTTPError("Trial result is not visible.", 403)

    try:
        artifact_files = ts.get_case_artifact_files(task_no, case_no)
    except FileNotFoundError as e:
        return HTTPError(str(e), 400)
    except AttributeError as e:
        return HTTPError(str(e), 102)  # Processing?
    except Exception as e:
        return HTTPError(f'Failed to read artifact files: {str(e)}', 500)

    return HTTPResponse('ok', data=artifact_files)


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

    # Check trialResultDownloadable for students
    if is_owner and not is_staff:
        problem = Problem(ts.problem_id)
        if (problem.config or {}).get('trialResultDownloadable') is False:
            return HTTPError("Trial result download is disabled.", 403)

    # Generator - yields each task's complete artifact zip (similar to download_trial_task_artifact)
    def file_iterator():
        from zipfile import ZipFile, BadZipFile
        minio_client = MinioClient()

        for task_index, task in enumerate(ts.tasks):
            if not task.cases:
                continue

            try:
                # Build a zip for this task containing all cases
                task_buf = io.BytesIO()
                wrote_any_file = False

                with ZipFile(task_buf, 'w') as task_zip:
                    for case_index, case in enumerate(task.cases):
                        output_path = getattr(case, 'output_minio_path', None)
                        if not output_path:
                            continue
                        try:
                            data = minio_client.download_file(output_path)
                            with ZipFile(io.BytesIO(data)) as case_zip:
                                for name in case_zip.namelist():
                                    arcname = f'case_{case_index:02d}/{name}'
                                    task_zip.writestr(arcname,
                                                      case_zip.read(name))
                                    wrote_any_file = True
                        except BadZipFile:
                            continue
                        except Exception:
                            continue

                if wrote_any_file:
                    task_buf.seek(0)
                    yield (f"task_{task_index}.zip", task_buf.read())
                else:
                    yield (f"task_{task_index}_error.txt",
                           b"No artifacts available")

            except Exception:
                yield (f"task_{task_index}_error.txt", b"Output not found")

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


# === Rejudge APIs ===


@trial_submission_api.route("/<trial_id>/rejudge", methods=["GET"])
@login_required
def rejudge_trial(user, trial_id: str):
    """
    Rejudge a single trial submission.
    Uses the same permission check as regular submission rejudge:
    checks if user has GRADE permission in the problem's courses.
    """
    from datetime import datetime
    from mongo.submission import JudgeQueueFullError
    from mongoengine import ValidationError

    # Load trial submission
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)
    except Exception as e:
        current_app.logger.error(f"Error loading trial submission: {e}")
        return HTTPError("Invalid trial submission ID.", 400)

    # Check if submission is currently being judged (rate limit protection)
    # Same logic as normal submission - check status FIRST, then permission
    if ts.status == -2:
        pending_since = getattr(ts, 'last_send', None) or getattr(
            ts, 'timestamp', None)
        if pending_since is None:
            return HTTPError(
                'Trial submission is queued and not yet judged. Please wait.',
                403)
        seconds_since_pending = (datetime.now() -
                                 pending_since).total_seconds()
        if seconds_since_pending < 300:
            remaining_seconds = 300 - seconds_since_pending
            remaining_minutes = (remaining_seconds // 60) + 1
            return HTTPError(
                f'Trial submission is queued and not yet judged. '
                f'Please wait approximately {remaining_minutes} minute(s) before trying again.',
                403)
        current_app.logger.warning(
            f"Allowing rejudge for stale pending trial submission {trial_id} "
            f"({int(seconds_since_pending)}s since pending)")
    if ts.status == -1:
        time_since_send = (datetime.now() - ts.last_send).seconds
        if time_since_send < 300:  # Same 5 minute rate limit as normal submission
            remaining_seconds = 300 - time_since_send
            remaining_minutes = (remaining_seconds // 60) + 1
            return HTTPError(
                f'Rejudge rate limit: Trial submission is currently being judged. '
                f'Please wait approximately {remaining_minutes} minute(s) before trying again.',
                403)

    # Check permission using the same logic as regular submission
    if not ts.permission(user, TrialSubmission.Permission.REJUDGE):
        return HTTPError("forbidden.", 403)

    # Perform rejudge
    try:
        success = ts.rejudge()
    except ValueError as e:
        return HTTPError(str(e), 400)
    except JudgeQueueFullError as e:
        return HTTPResponse(str(e), 202, data={'ok': False})
    except ValidationError as e:
        return HTTPError(str(e), 422, data=e.to_dict())
    except Exception as e:
        current_app.logger.error(
            f"Error rejudging trial submission {trial_id}: {e}")
        return HTTPError(f"Rejudge failed: {e}", 500)

    # Check explicit False (not None or other falsy values)
    if success is False:
        return HTTPError("Some error occurred, please contact the admin", 500)

    current_app.logger.info(f"Rejudged trial submission {trial_id}")
    return HTTPResponse("", data={"ok": True})


@trial_submission_api.route("/<trial_id>", methods=["DELETE"])
@login_required
def delete_trial(user, trial_id: str):
    """
    Delete a single trial submission.
    Uses the same permission check as rejudge:
    checks if user has GRADE permission in the problem's courses.
    
    Protection:
    - Cannot delete if status == -1 (judging in progress) and last_send < 10 minutes
    - Status -2 (never judged) is safe to delete
    - Status >= 0 (already judged) is safe to delete
    """
    # Load trial submission
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)
    except Exception as e:
        current_app.logger.error(f"Error loading trial submission: {e}")
        return HTTPError("Invalid trial submission ID.", 400)

    # Check permission using the same logic as rejudge
    if not ts.permission(user, TrialSubmission.Permission.REJUDGE):
        return HTTPError("forbidden.", 403)

    # Protection: Cannot delete if currently being judged
    # Status -1 means "judging in progress" - sandbox is processing
    if ts.status == -1:
        last_send = getattr(ts, 'last_send', None)
        if last_send:
            seconds_since_send = (datetime.now() - last_send).total_seconds()
            # If sent within last 10 minutes, it might still be processing
            if seconds_since_send < 600:  # 10 minutes
                minutes_remaining = int((600 - seconds_since_send) / 60) + 1
                return HTTPError(
                    f"Cannot delete: submission is currently being judged. "
                    f"Please wait {minutes_remaining} minutes or until judging completes.",
                    409  # Conflict
                )
            else:
                # Judging for more than 10 minutes - likely stuck, allow deletion
                current_app.logger.warning(
                    f"Allowing deletion of stuck trial submission {trial_id} "
                    f"(status=-1 for {int(seconds_since_send)}s)")
        else:
            # No last_send but status is -1 - inconsistent state, allow deletion
            current_app.logger.warning(
                f"Deleting trial submission {trial_id} with status=-1 but no last_send"
            )

    # Perform deletion
    try:
        problem_id = ts.problem_id
        submission_id = str(ts.id)
        status = ts.status

        # Delete code from MinIO if exists
        code_path = getattr(ts, 'code_minio_path', None)
        if code_path:
            try:
                minio_client = MinioClient()
                minio_client.client.remove_object(minio_client.bucket,
                                                  code_path)
                current_app.logger.info(
                    f"Deleted code from MinIO: {code_path}")
            except Exception as e:
                current_app.logger.warning(
                    f"Failed to delete code from MinIO: {e}")

        # Delete custom input from MinIO if exists
        custom_input_path = getattr(ts, 'custom_input_minio_path', None)
        if custom_input_path:
            try:
                minio_client = MinioClient()
                minio_client.client.remove_object(minio_client.bucket,
                                                  custom_input_path)
                current_app.logger.info(
                    f"Deleted custom input from MinIO: {custom_input_path}")
            except Exception as e:
                current_app.logger.warning(
                    f"Failed to delete custom input from MinIO: {e}")

        # Delete the document from MongoDB
        ts.obj.delete()

        current_app.logger.info(
            f"Deleted trial submission {submission_id} for problem {problem_id} "
            f"(status was {status})")
        return HTTPResponse("Trial submission deleted.", data={"ok": True})
    except Exception as e:
        current_app.logger.error(
            f"Error deleting trial submission {trial_id}: {e}")
        return HTTPError(f"Delete failed: {e}", 500)


@trial_submission_api.route("/delete-all/<int:problem_id>", methods=["DELETE"])
@login_required
def delete_all_trial_submissions(user, problem_id: int):
    """
    Delete all trial submissions for a problem.
    Admin/Teacher/TA with course permission can use this.
    """
    from mongo.problem import Problem

    # Check permission using same logic as rejudge-all
    try:
        problem = Problem(problem_id)
        if not problem:
            return HTTPError(f"Problem {problem_id} not found.", 404)

        # Check if user has GRADE permission in any course containing this problem
        from mongo.course import Course
        problem_courses = map(Course, problem.courses)
        has_permission = any(
            c.own_permission(user) & Course.Permission.GRADE
            for c in problem_courses)

        if not has_permission:
            return HTTPError("forbidden.", 403)
    except Exception as e:
        current_app.logger.error(
            f"Error checking permission for delete-all trials: {e}")
        return HTTPError("Permission check failed.", 500)

    # Get all trial submissions for this problem
    try:
        submissions = engine.TrialSubmission.objects(problem=problem_id)

        deleted_count = 0
        skipped_count = 0

        from mongo.utils import MinioClient

        for sub_doc in submissions:
            try:
                # Same protection as single delete:
                # Skip if currently judging (status -1) and sent recently (< 10 mins)
                if sub_doc.status == -1:
                    last_send = sub_doc.last_send
                    if last_send and (datetime.now() -
                                      last_send).total_seconds() < 600:
                        skipped_count += 1
                        continue

                ts_id = str(sub_doc.id)

                # Delete code from MinIO if exists
                code_path = getattr(sub_doc, 'code_minio_path', None)
                if code_path:
                    try:
                        minio_client = MinioClient()
                        minio_client.client.remove_object(
                            minio_client.bucket, code_path)
                    except Exception:
                        pass

                # Delete custom input from MinIO if exists
                custom_input_path = getattr(sub_doc, 'custom_input_minio_path',
                                            None)
                if custom_input_path:
                    try:
                        minio_client = MinioClient()
                        minio_client.client.remove_object(
                            minio_client.bucket, custom_input_path)
                    except Exception:
                        pass

                # Delete document
                sub_doc.delete()
                deleted_count += 1

            except Exception as e:
                current_app.logger.error(
                    f"Error deleting trial submission {sub_doc.id}: {e}")
                skipped_count += 1

        return HTTPResponse(
            f"Delete all completed: {deleted_count} deleted, {skipped_count} skipped.",
            data={
                "deleted": deleted_count,
                "skipped": skipped_count
            })
    except Exception as e:
        current_app.logger.error(
            f"Error deleting all trials for problem {problem_id}: {e}")
        return HTTPError(f"Delete all failed: {e}", 500)


@trial_submission_api.route("/rejudge-all/<int:problem_id>", methods=["POST"])
@login_required
def rejudge_all_trials(user, problem_id: int):
    """
    Rejudge all trial submissions for a problem.
    Uses the same permission check as regular submission rejudge:
    checks if user has GRADE permission in the problem's courses.
    """
    from mongo.problem import Problem

    # Check if user has permission to rejudge submissions for this problem
    # We check by creating a dummy submission check - if user has GRADE permission
    # in any course that contains this problem, they can rejudge
    try:
        problem = Problem(problem_id)
        if not problem:
            return HTTPError(f"Problem {problem_id} not found.", 404)

        # Check if user has GRADE permission in any course containing this problem
        from mongo.course import Course
        problem_courses = map(Course, problem.courses)
        has_permission = any(
            c.own_permission(user) & Course.Permission.GRADE
            for c in problem_courses)

        if not has_permission:
            return HTTPError("forbidden.", 403)
    except Exception as e:
        current_app.logger.error(
            f"Error checking permission for rejudge-all: {e}")
        return HTTPError("Permission check failed.", 500)

    # Get all trial submissions for this problem
    try:
        submissions = engine.TrialSubmission.objects(problem=problem_id)
        if not submissions:
            return HTTPError("No trial submissions found for this problem.",
                             404)

        success_count = 0
        fail_count = 0
        skipped_count = 0

        for sub_doc in submissions:
            try:
                ts = TrialSubmission(sub_doc.id)
                # Skip if pending or recently sent (same logic as single rejudge)
                if ts.status == -2:
                    from datetime import datetime
                    pending_since = getattr(ts, 'last_send', None) or getattr(
                        ts, 'timestamp', None)
                    if pending_since is None:
                        skipped_count += 1
                        continue
                    seconds_since_pending = (datetime.now() -
                                             pending_since).total_seconds()
                    if seconds_since_pending < 300:
                        skipped_count += 1
                        continue
                    current_app.logger.warning(
                        f"Allowing rejudge for stale pending trial submission {ts.id} "
                        f"({int(seconds_since_pending)}s since pending)")
                if ts.status == -1 and hasattr(ts, 'last_send'):
                    from datetime import datetime
                    if (datetime.now() - ts.last_send).seconds < 300:
                        skipped_count += 1
                        continue

                result = ts.rejudge()
                if result:
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                current_app.logger.warning(
                    f"Failed to rejudge {sub_doc.id}: {e}")
                fail_count += 1

        current_app.logger.info(
            f"Rejudge all for problem {problem_id}: success={success_count}, fail={fail_count}, skipped={skipped_count}"
        )
        return HTTPResponse(
            f"Rejudge completed: {success_count} success, {fail_count} failed, {skipped_count} skipped.",
            data={
                "success": success_count,
                "failed": fail_count,
                "skipped": skipped_count
            })
    except Exception as e:
        current_app.logger.error(
            f"Error rejudging all trials for problem {problem_id}: {e}")
        return HTTPError(f"Rejudge all failed: {e}", 500)


@trial_submission_api.put('/<trial_id>/artifact/upload/case')
@Request.args('task', 'case', 'token')
def upload_trial_submission_case_artifact(trial_id: str, task, case,
                                          token: str):
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    try:
        task_no = int(task)
        case_no = int(case)
    except (TypeError, ValueError):
        return HTTPError('invalid task/case', 400)

    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)

    data = request.get_data()
    if not data:
        return HTTPError('no data', 400)

    try:
        ts.set_case_artifact(task_no, case_no, data)
    except FileNotFoundError as e:
        return HTTPError(str(e), 404)
    except Exception as e:
        return HTTPError(f'failed to upload artifact: {e}', 500)
    return HTTPResponse('artifact uploaded', data={'ok': True})


@trial_submission_api.put('/<trial_id>/artifact/upload/binary')
@Request.args('token')
def upload_trial_submission_compiled_binary(trial_id: str, token: str):
    try:
        ts = TrialSubmission(trial_id)
    except engine.DoesNotExist:
        return HTTPError("Trial submission not found.", 404)

    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)

    data = request.get_data()
    if not data:
        return HTTPError('no data', 400)

    try:
        ts.set_compiled_binary(data)
    except Exception as e:
        return HTTPError(f'failed to upload compiled binary: {e}', 500)
    return HTTPResponse('binary uploaded', data={'ok': True})
