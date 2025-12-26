import io
from typing import Optional
import requests as rq
import secrets
import json
from flask import (
    Blueprint,
    send_file,
    request,
    current_app,
)
from datetime import datetime, timedelta
from mongo import *
from mongo import engine
from mongo import sandbox
from mongo.utils import (
    RedisCache,
    drop_none,
    MinioClient,
)
from .utils import *
from .utils.submission_utils import clear_submission_list_cache_for_submission
from .auth import *

__all__ = ['submission_api']
submission_api = Blueprint('submission_api', __name__)


@submission_api.route('/', methods=['POST'])
@login_required(pat_scope=['write:submissions'])
@Request.json('language_type: int', 'problem_id: int')
def create_submission(user, language_type, problem_id):
    # the user reach the rate limit for submitting
    now = datetime.now()
    delta = timedelta.total_seconds(now - user.last_submit)
    if delta <= Submission.config().rate_limit:
        wait_for = Submission.config().rate_limit - delta
        return HTTPError(
            'Submit too fast!\n'
            f'Please wait for {wait_for:.2f} seconds to submit.',
            429,
            data={
                'waitFor': wait_for,
            },
        )  # Too many request
    # check for fields
    if problem_id is None:
        return HTTPError(
            'problemId is required!',
            400,
        )
    # search for problem
    problem = Problem(problem_id)
    if not problem:
        return HTTPError('Unexisted problem id.', 404)
    # problem permissoion
    if not problem.permission(user, Problem.Permission.VIEW):
        return HTTPError('problem permission denied!', 403)
    # check deadline
    for homework in problem.obj.homeworks:
        if now < homework.duration.start:
            return HTTPError('this homework hasn\'t start.', 403)
    # ip validation
    if not problem.is_valid_ip(get_ip()):
        return HTTPError('Invalid IP address.', 403)
    # handwritten problem doesn't need language type
    if language_type is None:
        if problem.problem_type != 2:
            return HTTPError(
                'post data missing!',
                400,
                data={
                    'languageType': language_type,
                    'problemId': problem_id
                },
            )
        language_type = 3
    # not allowed language
    if not problem.allowed(language_type):
        return HTTPError(
            'not allowed language',
            403,
            data={
                'allowed': problem.obj.allowed_language,
                'got': language_type
            },
        )
    # check if the user has used all his quota
    if problem.obj.quota != -1:
        no_grade_permission = not any(
            c.permission(user=user, req=Course.Permission.GRADE)
            for c in map(Course, problem.courses))

        run_out_of_quota = problem.submit_count(user) >= problem.quota
        if no_grade_permission and run_out_of_quota:
            return HTTPError('you have used all your quotas', 403)
    user.problem_submission[str(problem_id)] = problem.submit_count(user) + 1
    user.save()
    # insert submission to DB
    ip_addr = request.headers.get('cf-connecting-ip', request.remote_addr)
    try:
        submission = Submission.add(problem_id=problem_id,
                                    username=user.username,
                                    lang=language_type,
                                    timestamp=now,
                                    ip_addr=ip_addr)
    except ValidationError:
        return HTTPError('invalid data!', 400)
    except engine.DoesNotExist as e:
        return HTTPError(str(e), 404)
    except TestCaseNotFound as e:
        return HTTPError(str(e), 403)
    # update user
    user.update(
        last_submit=now,
        push__submissions=submission.obj,
    )
    # update problem
    submission.problem.update(inc__submitter=1)
    return HTTPResponse(
        'submission recieved.\n'
        'please send source code with given submission id later.',
        data={
            'submissionId': submission.id,
        },
    )


@submission_api.route('/', methods=['GET'])
@login_required(pat_scope=['read:submissions'])
@Request.args('offset', 'count', 'problem_id', 'username', 'status',
              'language_type', 'course')
def get_submission_list(
    user,
    offset,
    count,
    problem_id,
    username,
    status,
    course,
    language_type,
):
    '''
    get the list of submission data
    '''

    def parse_int(val: Optional[int], name: str):
        if val is None:
            return None
        try:
            return int(val)
        except ValueError:
            raise ValueError(f'can not convert {name} to integer')

    def parse_str(val: Optional[str], name: str):
        if val is None:
            return None
        try:
            return str(val)
        except ValueError:
            raise ValueError(f'can not convert {name} to string')

    def parse_status(val: Optional[str]) -> Optional[int]:
        '''
        Parse status parameter, accepts both status code string and status name
        '''
        if val is None:
            return None
        # Status code mapping
        status_map = {
            'AC': 0,
            'WA': 1,
            'CE': 2,
            'TLE': 3,
            'MLE': 4,
            'RE': 5,
            'JE': 6,
            'OLE': 7,
        }
        # If it's a status name string (AC, WA, etc.)
        if val.upper() in status_map:
            return status_map[val.upper()]
        # If it's a numeric string
        try:
            status_code = int(val)
            if 0 <= status_code <= 7:
                return status_code
            return None
        except ValueError:
            return None

    cache_key = (
        'SUBMISSION_LIST_API',
        user,
        problem_id,
        username,
        status,
        language_type,
        course,
        offset,
        count,
    )

    cache_key = '_'.join(map(str, cache_key))
    cache = RedisCache()
    # check cache
    if cache.exists(cache_key):
        submissions = json.loads(cache.get(cache_key))
        submission_count = submissions['submission_count']
        submissions = submissions['submissions']
    else:
        # convert args
        offset = parse_int(offset, 'offset')
        count = parse_int(count, 'count')
        problem_id = parse_int(problem_id, 'problemId')
        status = parse_status(status)

        if language_type is not None:
            try:
                language_type = list(map(int, language_type.split(',')))
            except ValueError as e:
                return HTTPError(
                    'cannot parse integers from languageType',
                    400,
                )
        # students can only get their own submissions
        if user.role == User.engine.Role.STUDENT:
            username = user.username
        try:
            params = drop_none({
                'user': user,
                'offset': offset,
                'count': count,
                'problem': problem_id,
                'q_user': username,
                'status': status,
                'language_type': language_type,
                'course': course,
            })
            submissions, submission_count = Submission.filter(
                **params,
                with_count=True,
            )
            submissions = [s.to_dict() for s in submissions]
            cache.set(
                cache_key,
                json.dumps({
                    'submissions': submissions,
                    'submission_count': submission_count,
                }), 15)
        except ValueError as e:
            return HTTPError(str(e), 400)
    ret = {
        'submissions': submissions,
        'submissionCount': submission_count,
    }
    return HTTPResponse(
        'here you are, bro',
        data=ret,
    )


@submission_api.route('/<submission>', methods=['GET'])
@login_required(pat_scope=['read:submissions'])
@Request.doc('submission', Submission)
def get_submission(user, submission: Submission):

    user_feedback_perm = submission.permission(user,
                                               Submission.Permission.FEEDBACK)
    # check permission
    if submission.handwritten and not user_feedback_perm:
        return HTTPError('forbidden.', 403)
    # ip validation
    problem = Problem(submission.problem_id)
    if not problem.is_valid_ip(get_ip()):
        return HTTPError('Invalid IP address.', 403)
    if not all(submission.timestamp in hw.duration
               for hw in problem.running_homeworks() if hw.ip_filters):
        return HTTPError('You cannot view this submission during quiz.', 403)
    # serialize submission
    has_code = not submission.handwritten and user_feedback_perm
    has_output = submission.problem.can_view_stdout
    ret = submission.to_dict()

    if has_code:
        if submission.is_zip_mode:
            ret['code'] = None
            ret['codeDownloadUrl'] = submission.get_code_download_url()
        else:
            try:
                code = submission.get_main_code()
                ret['code'] = code if code is not None else ''
            except (UnicodeDecodeError, SubmissionCodeNotFound):
                ret['code'] = ''
    if has_output:
        try:
            ret['tasks'] = submission.get_detailed_result()
        except Exception as e:
            current_app.logger.error(
                f"failed to load submission outputs [{submission.id}]: {e}")
            ret['tasks'] = []
            ret['tasksError'] = 'Failed to load outputs'
    else:
        ret['tasks'] = submission.get_result()
    return HTTPResponse(data=ret)


@submission_api.get('/<submission>/output/<int:task_no>/<int:case_no>')
@login_required(pat_scope=['read:submissions'])
@Request.doc('submission', Submission)
def get_submission_output(
    user,
    submission: Submission,
    task_no: int,
    case_no: int,
):
    if not submission.permission(user, Submission.Permission.VIEW_OUTPUT):
        return HTTPError('permission denied', 403)
    try:
        output = submission.get_single_output(task_no, case_no)
    except FileNotFoundError as e:
        return HTTPError(str(e), 400)
    except AttributeError as e:
        return HTTPError(str(e), 102)
    return HTTPResponse('ok', data=output)


@submission_api.get('/<submission>/artifact/case/<int:task_no>/<int:case_no>')
@login_required(pat_scope=['read:submissions'])
@Request.doc('submission', Submission)
def get_case_artifact_files(
    user,
    submission: Submission,
    task_no: int,
    case_no: int,
):
    '''
    Get all files from case artifact zip including stdout, stderr, and other files.
    Returns files with appropriate encoding (text as string, images as base64).
    Only available when artifact collection is enabled for this task.
    '''
    if not submission.permission(user, Submission.Permission.VIEW_OUTPUT):
        return HTTPError('permission denied', 403)
    if not submission.is_artifact_enabled(task_no):
        return HTTPError('artifact collection not enabled for this task', 404)
    try:
        artifact_files = submission.get_case_artifact_files(task_no, case_no)
    except FileNotFoundError as e:
        return HTTPError(str(e), 400)
    except AttributeError as e:
        return HTTPError(str(e), 102)
    except Exception as e:
        return HTTPError(f'Failed to read artifact files: {str(e)}', 500)
    return HTTPResponse('ok', data=artifact_files)


@submission_api.get('/<submission>/artifact/zip/<int:task_index>')
@login_required
@Request.doc('submission', Submission)
def download_submission_task_artifact(
    user,
    submission: Submission,
    task_index: int,
):
    if not submission.permission(user, Submission.Permission.VIEW_OUTPUT):
        return HTTPError('permission denied', 403)
    if task_index < 0 or task_index >= len(submission.tasks):
        return HTTPError('task not exist', 404)
    if not submission.is_artifact_enabled(task_index):
        return HTTPError('artifact not available for this task', 404)
    try:
        artifact = submission.build_task_artifact_zip(task_index)
    except FileNotFoundError as e:
        return HTTPError(str(e), 404)
    return send_file(
        artifact,
        mimetype='application/zip',
        as_attachment=True,
        download_name=
        f'submission-{submission.id}-task-{task_index:02d}-artifact.zip',
    )


@submission_api.get('/<submission>/artifact/compiledBinary')
@login_required
@Request.doc('submission', Submission)
def download_submission_compiled_binary(user, submission: Submission):
    problem = Problem(submission.problem_id)
    has_permission = (submission.permission(user,
                                            Submission.Permission.VIEW_OUTPUT)
                      or user.username == submission.username)
    if not has_permission:
        return HTTPError('permission denied', 403)
    artifact_collection = (problem.config or {}).get('artifactCollection', [])
    if not ((problem.config or {}).get('compilation') or
            ('compiledBinary' in artifact_collection)):
        return HTTPError('compiled binary not available', 404)
    if not submission.has_compiled_binary():
        return HTTPError('compiled binary not found', 404)
    try:
        binary_stream = submission.get_compiled_binary()
    except FileNotFoundError as e:
        return HTTPError(str(e), 404)
    binary_stream.seek(0)
    return send_file(
        binary_stream,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=f'submission-{submission.id}-compiled.bin',
    )


@submission_api.put('/<submission>/artifact/upload/case')
@Request.args('task', 'case', 'token')
@Request.doc('submission', Submission)
def upload_submission_case_artifact(submission: Submission, task, case,
                                    token: str):
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
        submission.set_case_artifact(task_no, case_no, data)
    except FileNotFoundError as e:
        return HTTPError(str(e), 404)
    except Exception as e:
        return HTTPError(f'failed to upload artifact: {e}', 500)
    return HTTPResponse('artifact uploaded', data={'ok': True})


@submission_api.put('/<submission>/artifact/upload/binary')
@Request.args('token')
@Request.doc('submission', Submission)
def upload_submission_compiled_binary(submission: Submission, token: str):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    data = request.get_data()
    if not data:
        return HTTPError('no data', 400)
    try:
        submission.set_compiled_binary(data)
    except Exception as e:
        return HTTPError(f'failed to upload compiled binary: {e}', 500)
    return HTTPResponse('binary uploaded', data={'ok': True})


@submission_api.route('/<submission>/pdf/<item>', methods=['GET'])
@login_required(pat_scope=['read:submissions'])
@Request.doc('submission', Submission)
def get_submission_pdf(user, submission: Submission, item):
    # check the permission
    if not submission.permission(user, Submission.Permission.FEEDBACK):
        return HTTPError('forbidden.', 403)
    # non-handwritten submissions have no pdf file
    if not submission.handwritten:
        return HTTPError('it is not a handwritten submission.', 400)
    if item not in ['comment', 'upload']:
        return HTTPError('/<submission_id>/pdf/<"upload" or "comment">', 400)
    try:
        if item == 'comment':
            data = submission.get_comment()
        else:
            data = submission.get_code('main.pdf', binary=True)
    except FileNotFoundError as e:
        return HTTPError('File not found.', 404)
    return send_file(
        io.BytesIO(data),
        mimetype='application/pdf',
        as_attachment=True,
        max_age=0,
        download_name=f'{item}-{submission.id[-6:] or "missing-id"}.pdf',
    )


@submission_api.route('/<submission>/static-analysis', methods=['GET'])
@login_required
@Request.doc('submission', Submission)
def get_static_analysis(user, submission: Submission):
    if not submission.permission(user, Submission.Permission.FEEDBACK):
        return HTTPError('forbidden.', 403)
    report = submission.sa_report or ""
    report_url = None
    if submission.sa_report_path:
        try:
            minio_client = MinioClient()
            report_url = minio_client.client.get_presigned_url(
                'GET',
                minio_client.bucket,
                submission.sa_report_path,
                expires=timedelta(minutes=30),
            )
        except Exception:
            current_app.logger.exception("Failed to presign SA report")
    return HTTPResponse('', data={"report": report, "reportUrl": report_url})


@submission_api.route('/<submission>/late-seconds', methods=['GET'])
@Request.args('token: str')
@Request.doc('submission', Submission)
def get_late_seconds(submission: Submission, token: str):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    late_seconds = submission.calculate_late_seconds()
    return HTTPResponse('', data={"lateSeconds": late_seconds})


@submission_api.route('/<submission>/complete', methods=['PUT'])
@Request.json('tasks: list', 'token: str')
@Request.doc('submission', Submission)
def on_submission_complete(submission: Submission, tasks, token):
    if not Submission.verify_token(submission.id, token):
        return HTTPError('i don\'t know you', 403)
    try:
        static_analysis = request.json.get('staticAnalysis')
        checker_payload = request.json.get('checker')
        scoring_payload = request.json.get('scoring')
        status_override = request.json.get('statusOverride')
        submission.process_result(tasks,
                                  static_analysis=static_analysis,
                                  checker=checker_payload,
                                  scoring=scoring_payload,
                                  status_override=status_override)
    except (ValidationError, KeyError) as e:
        return HTTPError(
            'invalid data!\n'
            f'{type(e).__name__}: {e}',
            400,
        )

    # Clear submission list cache for this submission only
    clear_submission_list_cache_for_submission(str(submission.id))

    return HTTPResponse(f'{submission} result recieved.')


@submission_api.route('/<submission>', methods=['PUT'])
@login_required(pat_scope=['write:submissions'])
@Request.doc('submission', Submission)
@Request.files('code')
def update_submission(user, submission: Submission, code):
    # validate this reques
    if submission.status >= 0:
        return HTTPError(
            f'{submission} has finished judgement.',
            403,
        )
    # if user not equal, reject
    if not secrets.compare_digest(submission.user.username, user.username):
        return HTTPError('user not equal!', 403)
    # if source code not found
    if code is None:
        return HTTPError(
            f'can not find the source file',
            400,
        )
    # or empty file
    if len(code.read()) == 0:
        return HTTPError('empty file', 400)
    code.seek(0)
    # has been uploaded
    if submission.has_code():
        return HTTPError(
            f'{submission} has been uploaded source file!',
            403,
        )
    try:
        success = submission.submit(code)
    except FileExistsError:
        exit(10086)
    except ValueError as e:
        return HTTPError(str(e), 400)
    except JudgeQueueFullError as e:
        return HTTPResponse(str(e), 202)
    except ValidationError as e:
        return HTTPError(str(e), 400, data=e.to_dict())
    except TestCaseNotFound as e:
        return HTTPError(str(e), 403)
    if success:
        return HTTPResponse(
            f'{submission} {"is finished." if submission.handwritten else "send to judgement."}',
            data={'ok': True},
            status_code=200)
    else:
        return HTTPError('Some error occurred, please contact the admin', 500)


@submission_api.route('/<submission>/grade', methods=['PUT'])
@login_required(pat_scope=['grade:submissions'])
@Request.json('score: int')
@Request.doc('submission', Submission)
def grade_submission(user: User, submission: Submission, score: int):
    if not submission.permission(user, Submission.Permission.GRADE):
        return HTTPError('forbidden.', 403)

    if score < 0 or score > 100:
        return HTTPError('score must be between 0 to 100.', 400)

    # AC if the score is 100, WA otherwise
    submission.update(score=score, status=(0 if score == 100 else 1))
    submission.finish_judging()
    return HTTPResponse(f'{submission} score recieved.')


@submission_api.route('/<submission>/manual-grade', methods=['PUT'])
@login_required
@Request.json('score: int', 'reason')
@Request.doc('submission', Submission)
def manual_grade_submission(user: User,
                            submission: Submission,
                            score: int,
                            reason: str = None):
    """
    Manually modify submission total score via frontend UI.
    This is separate from the PAT-based /grade endpoint.
    Records modification history for audit trail.
    """
    try:
        if not submission.permission(user, Submission.Permission.GRADE):
            return HTTPError('forbidden.', 403)

        if score is None:
            return HTTPError('score is required.', 400)

        if score < 0 or score > 100:
            return HTTPError('score must be between 0 and 100.', 400)

        # Record the modification
        before_score = submission.score
        modification_record = engine.ScoreModificationRecord(
            modifier=user.username,
            timestamp=datetime.now(),
            before_score=before_score,
            after_score=score,
            task_index=None,  # None means total score
            reason=reason,
        )

        # Update submission score only (do NOT change status)
        # Status should remain unchanged when manually modifying score
        # Append modification record to the list
        if not hasattr(submission, 'score_modifications'
                       ) or submission.score_modifications is None:
            submission.score_modifications = []
        submission.score_modifications.append(modification_record)

        submission.update(
            score=score,
            score_modifications=submission.score_modifications,
        )
        submission.reload()

        # Sync homework grades
        try:
            submission.finish_judging()
        except Exception as e:
            current_app.logger.error(
                f'Failed to sync homework grades for {submission}: {e}',
                exc_info=True)
            # Continue even if homework sync fails

        # Clear submission list cache
        try:
            clear_submission_list_cache_for_submission(str(submission.id))
        except Exception as e:
            current_app.logger.warning(
                f'Failed to clear cache for submission {submission.id}: {e}')

        return HTTPResponse(
            f'{submission} score manually updated from {before_score} to {score}.',
            data={
                'ok': True,
                'beforeScore': before_score,
                'afterScore': score,
            })
    except Exception as e:
        # HTTPError is not an Exception, it's a return value, so we don't catch it
        # Only catch actual exceptions
        current_app.logger.error(
            f'Error in manual_grade_submission for submission {submission.id}: {e}',
            exc_info=True)
        return HTTPError(f'Failed to update score: {str(e)}', 500)


@submission_api.route('/<submission>/manual-grade/task/<int:task_index>',
                      methods=['PUT'])
@login_required
@Request.json('score: int', 'reason')
@Request.doc('submission', Submission)
def manual_grade_task(user: User,
                      submission: Submission,
                      task_index: int,
                      score: int,
                      reason: str = None):
    """
    Manually modify a specific task's score via frontend UI.
    Recalculates total score as sum of all task scores.
    Records modification history for audit trail.
    """
    try:
        if not submission.permission(user, Submission.Permission.GRADE):
            return HTTPError('forbidden.', 403)

        if score is None:
            return HTTPError('score is required.', 400)

        # Validate task_index
        if task_index < 0 or task_index >= len(submission.tasks):
            return HTTPError(
                f'Invalid task index. Submission has {len(submission.tasks)} tasks.',
                400)

        # Get the task
        task = submission.tasks[task_index]
        before_score = task.score

        # Validate score range based on problem's task configuration
        if score < 0:
            return HTTPError('score must be non-negative.', 400)

        # Get max score for this task from problem config
        problem = submission.problem
        max_task_score = None
        try:
            if problem and problem.test_case and problem.test_case.tasks:
                if task_index < len(problem.test_case.tasks):
                    max_task_score = problem.test_case.tasks[
                        task_index].task_score
        except (AttributeError, IndexError) as e:
            current_app.logger.warning(
                f'Could not get max task score for task {task_index}: {e}')

        if max_task_score is not None and score > max_task_score:
            return HTTPError(
                f'score must be between 0 and {max_task_score} for this task.',
                400)

        # Record the modification
        modification_record = engine.ScoreModificationRecord(
            modifier=user.username,
            timestamp=datetime.now(),
            before_score=before_score,
            after_score=score,
            task_index=task_index,
            reason=reason,
        )

        # Calculate new total score (sum of all task scores, with updated score for target task)
        new_total_score = 0
        for i, t in enumerate(submission.tasks):
            if i == task_index:
                new_total_score += score
            else:
                new_total_score += t.score

        # Use MongoDB's raw update through collection to update just the score field
        # This avoids ValidationError when recreating EmbeddedDocument with CaseResult
        engine.Submission._get_collection().update_one(
            {'_id': submission.obj.id}, {
                '$set': {
                    f'tasks.{task_index}.score': score,
                    'score': new_total_score,
                },
                '$push': {
                    'scoreModifications': modification_record.to_mongo(),
                }
            })
        submission.reload()

        # Sync homework grades
        try:
            submission.finish_judging()
        except Exception as e:
            current_app.logger.error(
                f'Failed to sync homework grades for {submission}: {e}',
                exc_info=True)
            # Continue even if homework sync fails

        # Clear submission list cache
        try:
            clear_submission_list_cache_for_submission(str(submission.id))
        except Exception as e:
            current_app.logger.warning(
                f'Failed to clear cache for submission {submission.id}: {e}')

        return HTTPResponse(
            f'{submission} task {task_index} score manually updated from {before_score} to {score}.',
            data={
                'ok': True,
                'taskIndex': task_index,
                'beforeScore': before_score,
                'afterScore': score,
                'newTotalScore': new_total_score,
            })
    except Exception as e:
        # HTTPError is not an Exception, it's a return value, so we don't catch it
        # Only catch actual exceptions
        current_app.logger.error(
            f'Error in manual_grade_task for submission {submission.id}, task {task_index}: {e}',
            exc_info=True)
        return HTTPError(f'Failed to update task score: {str(e)}', 500)


@submission_api.route('/<submission>/score-history', methods=['GET'])
@login_required
@Request.doc('submission', Submission)
def get_score_history(user: User, submission: Submission):
    """
    Get the score modification history for a submission.
    Only users with GRADE permission can view this.
    """
    if not submission.permission(user, Submission.Permission.GRADE):
        return HTTPError('forbidden.', 403)

    # Get the score_modifications list
    modifications = getattr(submission, 'score_modifications', []) or []

    history = []
    for mod in modifications:
        history.append({
            'modifier':
            mod.modifier,
            # Return timestamp in seconds (formatTime will multiply by 1000)
            'timestamp':
            mod.timestamp.timestamp() if mod.timestamp else None,
            'beforeScore':
            mod.before_score,
            'afterScore':
            mod.after_score,
            'taskIndex':
            mod.task_index,  # None means total score
            'reason':
            mod.reason,
        })

    return HTTPResponse('Score modification history retrieved.',
                        data={
                            'ok': True,
                            'history': history,
                            'count': len(history),
                        })


@submission_api.route('/<submission>/comment', methods=['PUT'])
@login_required
@Request.files('comment')
@Request.doc('submission', Submission)
def comment_submission(user, submission: Submission, comment):
    if not submission.permission(user, Submission.Permission.COMMENT):
        return HTTPError('forbidden.', 403)

    if comment is None:
        return HTTPError(
            f'can not find the comment',
            400,
        )
    try:
        submission.add_comment(comment)
    except ValueError as e:
        return HTTPError(str(e), 400)
    return HTTPResponse(f'{submission} comment recieved.')


@submission_api.route('/<submission>/rejudge', methods=['GET'])
@login_required
@Request.doc('submission', Submission)
def rejudge(user, submission: Submission):
    # Check if submission is currently being judged (rate limit protection)
    if submission.status == -2:
        return HTTPError(
            'Submission is queued and not yet judged. Please wait.', 403)
    if submission.status == -1:
        time_since_send = (datetime.now() - submission.last_send).seconds
        if time_since_send < 300:
            remaining_seconds = 300 - time_since_send
            remaining_minutes = (remaining_seconds // 60) + 1
            return HTTPError(
                f'Rejudge rate limit: Submission is currently being judged. '
                f'Please wait approximately {remaining_minutes} minute(s) before trying again.',
                403)
    if not submission.permission(user, Submission.Permission.REJUDGE):
        return HTTPError('forbidden.', 403)
    try:
        success = submission.rejudge()
    except ValueError as e:
        return HTTPError(str(e), 400)
    except JudgeQueueFullError as e:
        return HTTPResponse(str(e), 202, data={'ok': False})
    except ValidationError as e:
        return HTTPError(str(e), 422, data=e.to_dict())

    # Check explicit False (not None or other falsy values)
    if success is False:
        return HTTPError('Some error occurred, please contact the admin', 500)

    # Clear submission list cache for this submission only
    clear_submission_list_cache_for_submission(str(submission.id))

    return HTTPResponse('', data={'ok': True})


@submission_api.route('/<submission>', methods=['DELETE'])
@login_required
@Request.doc('submission', Submission)
def delete_submission(user, submission: Submission):
    """
    Delete a submission. Only admin can delete submissions.
    Protection: Cannot delete if currently being judged.
    """
    # Only admin can delete submissions
    if not User(user.username).role == 0:  # Role.ADMIN
        return HTTPError('Only admin can delete submissions.', 403)

    # Protection: Cannot delete if currently being judged
    if submission.status == -1:
        last_send = getattr(submission, 'last_send', None)
        if last_send:
            seconds_since_send = (datetime.now() - last_send).total_seconds()
            if seconds_since_send < 600:  # 10 minutes
                minutes_remaining = int((600 - seconds_since_send) / 60) + 1
                return HTTPError(
                    f"Cannot delete: submission is currently being judged. "
                    f"Please wait {minutes_remaining} minutes or until judging completes.",
                    409  # Conflict
                )

    try:
        # Delete code from MinIO if exists
        if submission.code_minio_path:
            try:
                minio_client = MinioClient()
                minio_client.client.remove_object(minio_client.bucket,
                                                  submission.code_minio_path)
            except Exception as e:
                current_app.logger.warning(
                    f"Failed to delete code from MinIO: {e}")

        # Delete the submission document
        submission.delete()
        return HTTPResponse('Submission deleted successfully.',
                            data={'ok': True})
    except Exception as e:
        current_app.logger.error(f"Error deleting submission: {e}")
        return HTTPError(f'Failed to delete submission: {str(e)}', 500)


@submission_api.route('/rejudge-all', methods=['POST'])
@login_required
@Request.json('problem_id: int')
def rejudge_all_submissions(user, problem_id: int):
    """
    Rejudge all submissions for a specific problem.
    Only admin/teacher/TA with course permissions can use this.
    """
    # Check permission
    req_user = User(user.username)
    if req_user.role not in (0, 1, 2):  # Admin, Teacher, TA
        return HTTPError('Forbidden.', 403)

    try:
        problem = Problem(problem_id)
    except engine.DoesNotExist:
        return HTTPError('Problem not found.', 404)

    # For non-admin, check course permission
    if req_user.role != 0:
        has_permission = False
        for course in problem.courses:
            if Course(course.course_name).permission(req_user,
                                                     Course.Permission.GRADE):
                has_permission = True
                break
        if not has_permission:
            return HTTPError(
                'You do not have permission to rejudge for this problem.', 403)

    # Get all submissions for this problem
    submissions = Submission.filter(problem=problem_id)

    success_count = 0
    failed_count = 0
    skipped_count = 0

    for sub in submissions:
        try:
            # Skip if never judged or recently sent
            if sub.status == -2:
                skipped_count += 1
                continue
            if sub.status == -1:
                last_send = getattr(sub, 'last_send', None)
                if last_send and (datetime.now() -
                                  last_send).total_seconds() < 60:
                    skipped_count += 1
                    continue

            sub.rejudge()
            success_count += 1
        except Exception as e:
            current_app.logger.warning(
                f"Failed to rejudge submission {sub.id}: {e}")
            failed_count += 1

    return HTTPResponse(
        f'Rejudge completed. Success: {success_count}, Failed: {failed_count}, Skipped: {skipped_count}',
        data={
            'ok': True,
            'success': success_count,
            'failed': failed_count,
            'skipped': skipped_count
        })


@submission_api.route('/config', methods=['GET', 'PUT'])
@login_required
@identity_verify(0)
def config(user):
    config = Submission.config()

    def get_config():
        ret = config.to_mongo()
        del ret['_cls']
        del ret['_id']
        return HTTPResponse('success.', data=ret)

    @Request.json('rate_limit: int', 'sandbox_instances: list')
    def modify_config(rate_limit, sandbox_instances):
        # try to convert json object to Sandbox instance
        try:
            sandbox_instances = [
                *map(
                    lambda s: engine.Sandbox(**s),
                    sandbox_instances,
                )
            ]
        except engine.ValidationError as e:
            return HTTPError(
                'wrong Sandbox schema',
                400,
                data=e.to_dict(),
            )
        # skip if during testing
        if not current_app.config['TESTING']:
            resps = []
            # check sandbox status
            for sb in sandbox_instances:
                resp = rq.get(f'{sb.url}/status')
                if not resp.ok:
                    resps.append((sb.name, resp))
            # some exception occurred
            if len(resps) != 0:
                return HTTPError(
                    'some error occurred when check sandbox status',
                    400,
                    data=[{
                        'name': name,
                        'statusCode': resp.status_code,
                        'response': resp.text,
                    } for name, resp in resps],
                )
        try:
            config.update(
                rate_limit=rate_limit,
                sandbox_instances=sandbox_instances,
            )
        except ValidationError as e:
            return HTTPError(str(e), 400)

        return HTTPResponse('success.')

    methods = {'GET': get_config, 'PUT': modify_config}
    return methods[request.method]()


@submission_api.post('/<submission>/migrate-code')
@login_required
@identity_verify(0)
@Request.doc('submission', Submission)
def migrate_code(user: User, submission: Submission):
    if not submission.permission(
            user,
            Submission.Permission.MANAGER,
    ):
        return HTTPError('forbidden.', 403)

    submission.migrate_code_to_minio()
    return HTTPResponse('ok')
