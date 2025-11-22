import json
import hashlib
import statistics
from dataclasses import asdict
from flask import Blueprint, request, send_file
from urllib import parse
from zipfile import BadZipFile
from mongo import *
from mongo import engine
from mongo import sandbox
from mongo.utils import drop_none
from mongo.problem import *
from .auth import *
from .utils import *

__all__ = ['problem_api']

problem_api = Blueprint('problem_api', __name__)


def permission_error_response():
    return HTTPError('Not enough permission', 403)


def online_error_response():
    return HTTPError('Problem is unavailable', 403)


@problem_api.route('/', methods=['GET'])
@login_required
@Request.args(
    'offset',
    'count',
    'problem_id',
    'tags',
    'name',
    'course',
)
def view_problem_list(
    user,
    offset,
    count,
    tags,
    problem_id,
    name,
    course,
):
    # casting args
    try:
        if offset is not None:
            offset = int(offset)
        if count is not None:
            count = int(count)
    except (TypeError, ValueError):
        return HTTPError(
            'offset and count must be integer!',
            400,
        )
    problem_id, name, tags, course = (parse.unquote(p or '') or None
                                      for p in (problem_id, name, tags,
                                                course))
    try:
        ks = {
            'user': user,
            'offset': offset,
            'count': count,
            'tags': tags and tags.split(','),
            'problem_id': problem_id,
            'name': name,
            'course': course,
        }
        ks = {k: v for k, v in ks.items() if v is not None}
        data = Problem.get_problem_list(**ks)
    except IndexError:
        return HTTPError('invalid offset', 400)
    data = [{
        'problemId': p.problem_id,
        'problemName': p.problem_name,
        'status': p.problem_status,
        'ACUser': p.ac_user,
        'submitter': p.submitter,
        'tags': p.tags,
        'type': p.problem_type,
        'quota': p.quota,
        'submitCount': Problem(p.problem_id).submit_count(user)
    } for p in data]
    return HTTPResponse('Success.', data=data)


@problem_api.route('/<int:problem_id>', methods=['GET'])
@problem_api.route('/view/<int:problem_id>', methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def view_problem(user: User, problem: Problem):
    if not problem.permission(user=user, req=problem.Permission.VIEW):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()

    # ip validation
    if not problem.is_valid_ip(get_ip()):
        return HTTPError('Invalid IP address.', 403)
    # filter data
    data = problem.detailed_info(
        'problemName',
        'description',
        'owner',
        'tags',
        'allowedLanguage',
        'courses',
        'quota',
        'ACUser',
        'submitter',
        'canViewStdout',
        'config',
        defaultCode='defaultCode',
        status='problemStatus',
        type='problemType',
        testCase='testCase__tasks',
    )
    if problem.obj.problem_type == 1:
        data.update({'fillInTemplate': problem.obj.test_case.fill_in_template})
    data.update({
        'submitCount': problem.submit_count(user),
        'highScore': problem.get_high_score(user=user),
    })
    return HTTPResponse('Problem can view.', data=data)


@problem_api.route('/manage/<int:problem_id>', methods=['GET'])
@Request.doc('problem_id', 'problem', Problem)
@identity_verify(0, 1)  # admin and teacher only
def get_problem_detailed(user, problem: Problem):
    '''
    Get problem's detailed information
    '''
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    info = problem.detailed_info(
        'courses',
        'problemName',
        'description',
        'tags',
        'testCase',
        'ACUser',
        'submitter',
        'allowedLanguage',
        'canViewStdout',
        'quota',
        'config',
        status='problemStatus',
        type='problemType',
    )
    info.update({'submitCount': problem.submit_count(user)})
    return HTTPResponse(
        'Success.',
        data=info,
    )


@problem_api.route('/<int:problem>/assets', methods=['PUT'])
@identity_verify(0, 1)
@Request.doc('problem', Problem)
def upload_problem_assets(user: User, problem: Problem):

    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()  #
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()  #

    try:
        files_data = {
            'case': request.files.get('case'),
            'checker.py': request.files.get('checker.py'),
            'makefile.zip': request.files.get('makefile.zip'),
            'Teacher_file': request.files.get('Teacher_file'),
            'score.py': request.files.get('score.py'),
            'score.json': request.files.get('score.json'),
            'local_service.zip': request.files.get('local_service.zip'),
        }

        valid_files = {k: v for k, v in files_data.items() if v is not None}

        if not valid_files:
            return HTTPError('No files provided', 400)

        problem.update_assets(files_data=valid_files)

        return HTTPResponse('Success.', data={'ok': True})  # (回傳 ok: true)

    except BadZipFile as e:
        return HTTPError(f'Invalid zip file: {str(e)}', 400)
    except Exception as e:
        return HTTPError(str(e), 400)


@problem_api.route('/manage', methods=['POST'])
@identity_verify(0, 1)
@Request.json(
    'courses: list',
    'status',
    'type',
    'description',
    'tags',
    'problem_name',
    'quota',
    'test_case_info',
    'can_view_stdout',
    'allowed_language',
    'default_code',
)
def create_problem(user: User, **ks):
    # Get optional parameters from request.json
    data = request.json or {}
    ks['config'] = data.get('config')
    ks['pipeline'] = data.get('pipeline')
    ks['test_mode'] = data.get(
        'Test_Mode')  # Note: Test_Mode in request, test_mode in code

    try:
        pid = Problem.add(user=user, **ks)
    except ValidationError as e:
        return HTTPError(
            'Invalid or missing arguments.',
            400,
            data=e.to_dict(),
        )
    except DoesNotExist as e:
        return HTTPError('Course not found', 404)
    except ValueError as e:
        return HTTPError(str(e), 400)
    return HTTPResponse(data={'problemId': pid})


@problem_api.route('/manage/<int:problem>', methods=['DELETE'])
@identity_verify(0, 1)
@Request.doc('problem', Problem)
def delete_problem(user: User, problem: Problem):
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    problem.delete()
    return HTTPResponse('Success.', data={'ok': True})


@problem_api.route('/manage/<int:problem>', methods=['PUT'])
@identity_verify(0, 1)
@Request.doc('problem', Problem)
def manage_problem(user: User, problem: Problem):

    @Request.json(
        'problemName',
        'description',
        'courses',
        'tags',
        'allowedLanguage',
        'quota',
        'type',
        'status',
        'testCaseInfo',
        'canViewStdout',
        'defaultCode',
        'config',
        'pipeline',
        'Test_Mode',
    )
    def modify_problem(**p_ks):
        kwargs = {
            'problem_name': p_ks.pop('problemName', None),
            'description': p_ks.pop('description', None),
            'courses': p_ks.pop('courses', None),
            'tags': p_ks.pop('tags', None),
            'allowed_language': p_ks.pop('allowedLanguage', None),
            'quota': p_ks.pop('quota', None),
            'type': p_ks.pop('type', None),
            'status': p_ks.pop('status', None),
            'test_case_info': p_ks.pop('testCaseInfo', None),
            'can_view_stdout': p_ks.pop('canViewStdout', None),
            'default_code': p_ks.pop('defaultCode', None),
            'config': p_ks.pop('config', None),
            'pipeline': p_ks.pop('pipeline', None),
            'Test_Mode': p_ks.pop('Test_Mode', None),
        }

        Problem.edit_problem(
            user=user,
            problem_id=problem.id,
            **drop_none(kwargs),
        )

        return HTTPResponse()

    @Request.files('case')
    def modify_problem_test_case(case):
        try:
            problem.update_test_case(case)
        except engine.DoesNotExist as e:
            return HTTPError(str(e), 404)
        except (ValueError, BadZipFile) as e:
            return HTTPError(str(e), 400)
        except BadTestCase as e:
            return HTTPError(str(e), 400)
        return HTTPResponse('Success.')

    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()

    # edit problem
    try:
        # modify problem meta
        if request.content_type.startswith('application/json'):
            return modify_problem()
        # upload testcase file
        elif request.content_type.startswith('multipart/form-data'):
            return modify_problem_test_case()
        else:
            return HTTPError(
                'Unknown content type',
                400,
                data={'contentType': request.content_type},
            )
    except ValidationError as ve:
        return HTTPError(
            'Invalid or missing arguments.',
            400,
            data=ve.to_dict(),
        )
    except engine.DoesNotExist:
        return HTTPError('Course not found.', 404)


@problem_api.post('/<int:problem>/initiate-test-case-upload')
@identity_verify(0, 1)
@Request.doc('problem', Problem)
@Request.json('length: int', 'part_size: int')
def initiate_test_case_upload(
    user: User,
    problem: Problem,
    length: int,
    part_size: int,
):
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    try:
        if length <= 0 or part_size <= 0:
            return HTTPError('Invalid length or part_size', 400)

        if part_size > length:
            return HTTPError('part_size cannot be greater than length', 400)
        upload_info = problem.generate_urls_for_uploading_test_case(
            length, part_size)
        return HTTPResponse('Test case upload initiated',
                            data={
                                'upload_id': upload_info.upload_id,
                                'urls': upload_info.urls,
                            })
    except ValueError as e:
        return HTTPError(f'Invalid parameters: {str(e)}', 400)
    except Exception as e:
        return HTTPError(str(e), 400)


@problem_api.post('/<int:problem>/complete-test-case-upload')
@identity_verify(0, 1)
@Request.doc('problem', Problem)
@Request.json('upload_id', 'parts: list')
def complete_test_case_upload(
    user: User,
    problem: Problem,
    upload_id: str,
    parts: list,
):
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()

    try:
        from minio.datatypes import Part
        if not isinstance(parts, list) or len(parts) == 0:
            return HTTPError('Invalid parts list', 400)

        part_objects = []
        for part in parts:
            if not isinstance(
                    part,
                    dict) or 'ETag' not in part or 'PartNumber' not in part:
                return HTTPError('Invalid part format', 400)

            part_objects.append(
                Part(part_number=part['PartNumber'], etag=part['ETag']))
        problem.complete_test_case_upload(upload_id, part_objects)
        return HTTPResponse('Test case upload completed',
                            data={'ok': True},
                            status_code=200)

    except BadTestCase as e:
        return HTTPError(str(e), 400)
    except ValueError as e:
        return HTTPError(f'Invalid parameters: {str(e)}', 400)
    except Exception as e:
        return HTTPError(str(e), 400)


@problem_api.route('/<int:problem_id>/test-case', methods=['GET'])
@problem_api.route('/<int:problem_id>/testcase', methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def get_test_case(user: User, problem: Problem):
    can_manage = problem.permission(user, problem.Permission.MANAGE)
    if not can_manage and not problem.has_course_modify_permission(user):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    return send_file(
        problem.get_test_case(),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'testdata-{problem.id}.zip',
    )


# FIXME: Find a better name
@problem_api.route('/<int:problem_id>/testdata', methods=['GET'])
@Request.args('token: str')
@Request.doc('problem_id', 'problem', Problem)
def get_testdata(token: str, problem: Problem):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    return send_file(
        problem.get_test_case(),
        mimetype='application/zip',
        as_attachment=True,
        download_name=f'testdata-{problem.id}.zip',
    )


@problem_api.route('/<int:problem_id>/checksum', methods=['GET'])
@Request.args('token: str')
def get_checksum(token: str, problem_id: int):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'{problem} not found', 404)
    submission_mode = getattr(problem.test_case, 'submission_mode', 0) or 0
    meta = json.dumps({
        'tasks':
        [json.loads(task.to_json()) for task in problem.test_case.tasks],
        'submissionMode':
        submission_mode,
    }).encode()
    # TODO: use etag of bucket object
    content = problem.get_test_case().read() + meta
    digest = hashlib.md5(content).hexdigest()
    return HTTPResponse(data=digest)


@problem_api.route('/<int:problem_id>/meta', methods=['GET'])
@Request.args('token: str')
def get_meta(token: str, problem_id: int):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'{problem} not found', 404)
    submission_mode = getattr(problem.test_case, 'submission_mode', 0) or 0
    meta = {
        'tasks':
        [json.loads(task.to_json()) for task in problem.test_case.tasks],
        'submissionMode': submission_mode,
    }
    return HTTPResponse(data=meta)


@problem_api.route('/<int:problem_id>/high-score', methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def high_score(user: User, problem: Problem):
    return HTTPResponse(data={
        'score': problem.get_high_score(user=user),
    })


@problem_api.route('/clone', methods=['POST'])
@problem_api.route('/copy', methods=['POST'])
@identity_verify(0, 1)
@Request.json('problem_id: int', 'target', 'status')
@Request.doc('problem_id', 'problem', Problem)
def clone_problem(
    user: User,
    problem: Problem,
    target,
    status,
):
    if not problem.permission(user, problem.Permission.VIEW):
        return HTTPError('Problem can not view.', 403)
    override = drop_none({'status': status})
    new_problem_id = problem.copy_to(
        user=user,
        target=target,
        **override,
    )
    return HTTPResponse(
        'Success.',
        data={'problemId': new_problem_id},
    )


@problem_api.route('/publish', methods=['POST'])
@identity_verify(0, 1)
@Request.json('problem_id')
@Request.doc('problem_id', 'problem', Problem)
def publish_problem(user, problem: Problem):
    if user.role == 1 and problem.owner != user.username:
        return HTTPError('Not the owner.', 403)
    Problem.release_problem(problem.problem_id)
    return HTTPResponse('Success.')


@problem_api.route('/<int:problem_id>/stats', methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def problem_stats(user: User, problem: Problem):
    if not problem.permission(user, problem.Permission.VIEW):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    ret = {}
    # gather unique students from all related courses
    student_usernames = set()
    for course in problem.courses:
        student_usernames.update(course.student_nicknames.keys())
    students = []
    for username in student_usernames:
        user_obj = User(username)
        if user_obj:
            students.append(user_obj)
    total_students = len(students)
    students_high_scores = [problem.get_high_score(user=u) for u in students]

    # These score statistics are only counting the scores of the students in the course.
    if total_students:
        student_docs = [u.obj for u in students]
        ac_users = len(
            engine.Submission.objects(
                problem=problem.id,
                status=0,
                user__in=student_docs,
            ).distinct('user'))
        tried_users = len(
            engine.Submission.objects(problem=problem.id,
                                      user__in=student_docs).distinct('user'))
    else:
        ac_users = 0
        tried_users = 0
    ret['acUserRatio'] = [ac_users, total_students]
    ret['triedUserCount'] = tried_users
    ret['average'] = None if total_students == 0 else statistics.mean(
        students_high_scores)
    ret['std'] = None if total_students <= 1 else statistics.pstdev(
        students_high_scores)
    ret['scoreDistribution'] = students_high_scores

    # Submission status counts (only include statuses that actually exist)
    status_count = {}
    for key, value in problem.get_submission_status().items():
        status_count[str(key)] = value
    ret['statusCount'] = status_count
    params = {
        'user': user,
        'offset': 0,
        'count': 10,
        'problem': problem.id,
        'status': 0,
    }
    top_10_runtime_submissions = [
        s.to_dict() for s in Submission.filter(**params, sort_by='runTime')
    ]
    ret['top10RunTime'] = top_10_runtime_submissions
    top_10_memory_submissions = [
        s.to_dict() for s in Submission.filter(**params, sort_by='memoryUsage')
    ]
    ret['top10MemoryUsage'] = top_10_memory_submissions
    return HTTPResponse('Success.', data=ret)


@problem_api.post('/<int:problem_id>/migrate-test-case')
@login_required
@identity_verify(0)  # admin only
@Request.doc('problem_id', 'problem', Problem)
def problem_migrate_test_case(user: User, problem: Problem):
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    problem.migrate_gridfs_to_minio()
    return HTTPResponse('Success.')


#


@problem_api.route('/static-analysis/options', methods=['GET'])
def get_static_analysis_options():
    try:
        library_symbols = [
            'stdio.h',
            'stdlib.h',
            'string.h',
            'math.h',
            'time.h',
            'ctype.h',
            'assert.h',
            'errno.h',
            'float.h',
            'limits.h',
            'locale.h',
            'setjmp.h',
            'signal.h',
            'stdarg.h',
            'stddef.h',
            'stdint.h',
            'stdbool.h',
            'sys/types.h',
            'sys/stat.h',
            'fcntl.h',
            'unistd.h',
            'pthread.h',
            'iostream',
            'vector',
            'string',
            'algorithm',
            'map',
            'set',
            'queue',
            'stack',
            'deque',
            'memory',
        ]

        return HTTPResponse('Success.',
                            data={'librarySymbols': library_symbols})

    except Exception as e:
        return HTTPError(str(e), 400)
