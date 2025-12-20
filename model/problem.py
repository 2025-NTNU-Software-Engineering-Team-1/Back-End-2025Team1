import json
import hashlib
import statistics
import logging
from dataclasses import asdict
from io import BytesIO
import zipfile
import io
from datetime import datetime
from flask import Blueprint, request, send_file, current_app
from urllib import parse
from zipfile import BadZipFile
from mongo import *
from mongo import engine
from mongo import sandbox
from mongo.utils import drop_none, MinioClient, RedisCache
import hashlib
from mongo.problem import *
from mongo.submission import TrialSubmission
from .utils.problem_utils import build_config_and_pipeline as _build_config_and_pipeline
from .utils.problem_utils import (
    build_static_analysis_rules as _build_static_analysis_rules, )
from .utils.problem_utils import derive_build_strategy as _derive_build_strategy
from .auth import *
from .utils import *

PUBLIC_TESTCASES_TTL = 3600  # 1 hour Time-to-Live for Redis cache

__all__ = ['problem_api']

problem_api = Blueprint('problem_api', __name__)


def permission_error_response():
    return HTTPError('Not enough permission', 403)


def online_error_response():
    return HTTPError('Problem is unavailable', 403)


@problem_api.route('/', methods=['GET'])
@login_required(pat_scope=['read:problems'])
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
        'submitCount': Problem(p.problem_id).submit_count(user),
    } for p in data]
    return HTTPResponse('Success.', data=data)


@problem_api.route('/<int:problem_id>', methods=['GET'])
@problem_api.route('/view/<int:problem_id>', methods=['GET'])
@login_required(pat_scope=['read:problems'])
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
    config_payload, pipeline_payload = _build_config_and_pipeline(problem)
    if config_payload:
        data['config'] = config_payload
    if pipeline_payload:
        data['pipeline'] = pipeline_payload
    return HTTPResponse('Problem can view.', data=data)


@problem_api.route('/manage/<int:problem_id>', methods=['GET'])
@Request.doc('problem_id', 'problem', Problem)
@identity_verify(0, 1, pat_scope=['read:problems'])  # admin and teacher only
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
    config_payload, pipeline_payload = _build_config_and_pipeline(problem)
    if config_payload:
        info['config'] = config_payload
    if pipeline_payload:
        info['pipeline'] = pipeline_payload
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
            'case':
            request.files.get('case'),
            'custom_checker.py':
            request.files.get('custom_checker.py'),
            'makefile.zip':
            request.files.get('makefile.zip'),
            'Teacher_file':
            request.files.get('Teacher_file'),
            'score.py':
            request.files.get('score.py'),
            'score.json':
            request.files.get('score.json'),
            'local_service.zip':
            request.files.get('local_service.zip'),
            'resource_data.zip':
            request.files.get('resource_data.zip')
            or request.files.get('resourcedata.zip'),
            'resource_data_teacher.zip':
            request.files.get('resource_data_teacher.zip'),
            'dockerfiles.zip':
            request.files.get('dockerfiles.zip'),
        }

        valid_files = {k: v for k, v in files_data.items() if v is not None}
        # 如果之前已經有 asset，可以只更新 meta，不強制上傳檔案
        has_existing_assets = bool((problem.config or {}).get('assetPaths'))
        if not valid_files and not has_existing_assets:
            return HTTPError('No files provided', 400)
        meta_raw = request.form.get('meta')
        try:
            meta = json.loads(meta_raw) if meta_raw else {}
            if meta is not None and not isinstance(meta, dict):
                raise ValueError('meta must be a JSON object')
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return HTTPError(f'Invalid meta payload: {exc}', 400)

        problem.update_assets(
            user=user,
            files_data=valid_files,
            meta=meta,
        )
        # 校驗：resourceData 需要 allowRead
        cfg_check = (meta or {}).get('config') or {}
        pipe_check = (meta or {}).get('pipeline') or {}
        allow_read = pipe_check.get('allowRead', cfg_check.get('allowRead'))
        resource_data_enabled = cfg_check.get(
            'resourceData') or pipe_check.get('resourceData')
        if resource_data_enabled and not allow_read:
            return HTTPError('resourceData requires allowRead=true', 400)

        return HTTPResponse('Success.', data={'ok':
                                              True})  # (returns ok: true)

    except BadZipFile as e:
        return HTTPError(f'Invalid zip file: {str(e)}', 400)
    except Exception as e:
        return HTTPError(str(e), 400)


@problem_api.route('/manage', methods=['POST'])
@identity_verify(0, 1, pat_scope=['write:problems'])
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
    data = request.json or {}

    alias_pairs = (
        ('problem_name', 'problemName'),
        ('allowed_language', 'allowedLanguage'),
        ('can_view_stdout', 'canViewStdout'),
        ('default_code', 'defaultCode'),
        ('test_case_info', 'testCaseInfo'),
    )
    for dest, src in alias_pairs:
        if ks.get(dest) is None and data.get(src) is not None:
            ks[dest] = data[src]

    config_payload = data.get('config') or {}
    pipeline_payload = data.get('pipeline') or {}

    legacy_config = {}
    for key in (
            'compilation',
            'aiVTuber',
            'aiVTuberMaxToken',
            'aiVTuberMode',
            'acceptedFormat',
            'artifactCollection',
            'maxStudentZipSizeMB',
            'networkAccessRestriction',
            'resourceData',
    ):
        if config_payload.get(key) is not None:
            legacy_config[key] = config_payload[key]
    static_analysis = (config_payload.get('staticAnalysis')
                       or config_payload.get('staticAnalys')
                       or pipeline_payload.get('staticAnalysis'))
    if config_payload.get('networkAccessRestriction'):
        static_analysis = static_analysis or {}
        static_analysis['networkAccessRestriction'] = config_payload[
            'networkAccessRestriction']
    if static_analysis:
        legacy_config['staticAnalysis'] = static_analysis
        legacy_config['staticAnalys'] = static_analysis
    if legacy_config:
        ks['config'] = drop_none(legacy_config)

    legacy_pipeline = {}
    for key in (
            'allowRead',
            'allowWrite',
            'resourceData',
            'executionMode',
            'customChecker',
            'teacherFirst',
    ):
        if pipeline_payload.get(key) is not None:
            legacy_pipeline[key] = pipeline_payload[key]
            if key == 'resourceData' and 'resourceData' not in legacy_config:
                legacy_config['resourceData'] = pipeline_payload[key]
    if ('scoringScript' in pipeline_payload
            and pipeline_payload['scoringScript'] is not None):
        legacy_pipeline['scoringScript'] = pipeline_payload['scoringScript']
        legacy_pipeline['scoringScrip'] = pipeline_payload['scoringScript']
    if ('scoringScrip' in pipeline_payload
            and pipeline_payload['scoringScrip'] is not None):
        legacy_pipeline['scoringScript'] = pipeline_payload['scoringScrip']
    if ('staticAnalysis' in pipeline_payload
            and pipeline_payload['staticAnalysis'] is not None):
        legacy_pipeline['staticAnalysis'] = pipeline_payload['staticAnalysis']
    if legacy_pipeline:
        ks['pipeline'] = drop_none(legacy_pipeline)

    test_mode_payload = data.get('Test_Mode') or {}
    derived_test_mode = {}
    if 'trialMode' in config_payload:
        derived_test_mode['Enabled'] = config_payload['trialMode']
    if 'trialModeQuotaPerStudent' in config_payload:
        derived_test_mode['Quota_Per_Student'] = config_payload[
            'trialModeQuotaPerStudent']
    if not test_mode_payload:
        test_mode_payload = derived_test_mode
    else:
        test_mode_payload = {
            **test_mode_payload,
            **{
                k: v
                for k, v in derived_test_mode.items() if v is not None
            },
        }
    if test_mode_payload:
        ks['Test_Mode'] = drop_none(test_mode_payload)

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
@identity_verify(0, 1, pat_scope=['write:problems'])
@Request.doc('problem', Problem)
def delete_problem(user: User, problem: Problem):
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    problem.delete()
    return HTTPResponse('Success.', data={'ok': True})


@problem_api.route('/manage/<int:problem>', methods=['PUT'])
@identity_verify(0, 1, pat_scope=['write:problems'])
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

        data = request.json or {}
        config_payload = data.get('config') or {}
        pipeline_payload = data.get('pipeline') or {}

        legacy_config = kwargs.get('config') or {}
        for key in (
                'compilation',
                'aiVTuber',
                'aiVTuberMaxToken',
                'aiVTuberMode',
                'acceptedFormat',
                'artifactCollection',
                'maxStudentZipSizeMB',
                'networkAccessRestriction',
                'resourceData',
        ):
            if config_payload.get(key) is not None:
                legacy_config[key] = config_payload[key]
        static_analysis = (config_payload.get('staticAnalysis')
                           or config_payload.get('staticAnalys')
                           or pipeline_payload.get('staticAnalysis'))
        if config_payload.get('networkAccessRestriction'):
            static_analysis = static_analysis or {}
            static_analysis['networkAccessRestriction'] = config_payload[
                'networkAccessRestriction']
        if static_analysis:
            legacy_config['staticAnalysis'] = static_analysis
            legacy_config['staticAnalys'] = static_analysis
        if legacy_config:
            kwargs['config'] = drop_none(legacy_config)

        legacy_pipeline = kwargs.get('pipeline') or {}
        for key in (
                'allowRead',
                'allowWrite',
                'resourceData',
                'executionMode',
                'customChecker',
                'teacherFirst',
        ):
            if pipeline_payload.get(key) is not None:
                legacy_pipeline[key] = pipeline_payload[key]
                if key == 'resourceData' and 'resourceData' not in legacy_config:
                    legacy_config['resourceData'] = pipeline_payload[key]
            if ('scoringScript' in pipeline_payload
                    and pipeline_payload['scoringScript'] is not None):
                legacy_pipeline['scoringScript'] = pipeline_payload[
                    'scoringScript']
                legacy_pipeline['scoringScrip'] = pipeline_payload[
                    'scoringScript']
            if ('scoringScrip' in pipeline_payload
                    and pipeline_payload['scoringScrip'] is not None):
                legacy_pipeline['scoringScript'] = pipeline_payload[
                    'scoringScrip']
            if ('staticAnalysis' in pipeline_payload
                    and pipeline_payload['staticAnalysis'] is not None):
                legacy_pipeline['staticAnalysis'] = pipeline_payload[
                    'staticAnalysis']
            if legacy_pipeline:
                kwargs['pipeline'] = drop_none(legacy_pipeline)

        test_mode_payload = data.get('Test_Mode') or kwargs.get(
            'Test_Mode') or {}
        derived_test_mode = {}
        if 'trialMode' in config_payload:
            derived_test_mode['Enabled'] = config_payload['trialMode']
        if 'trialModeQuotaPerStudent' in config_payload:
            derived_test_mode['Quota_Per_Student'] = config_payload[
                'trialModeQuotaPerStudent']
        if derived_test_mode:
            test_mode_payload = {
                **test_mode_payload,
                **{
                    k: v
                    for k, v in derived_test_mode.items() if v is not None
                },
            }
        if test_mode_payload:
            kwargs['Test_Mode'] = drop_none(test_mode_payload)

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
        return HTTPResponse(
            'Test case upload initiated',
            data={
                'upload_id': upload_info.upload_id,
                'urls': upload_info.urls,
            },
        )
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
            if (not isinstance(part, dict) or 'ETag' not in part
                    or 'PartNumber' not in part):
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


SUPPORTED_ASSET_TYPES = {
    'checker',
    'scoring_script',
    'makefile',
    'teacher_file',
    'local_service',  # reserved
    'resource_data',
    'resource_data_teacher',
    'network_dockerfile',
}


@problem_api.get('/<int:problem_id>/asset-checksum')
@Request.args('token: str', 'asset_type: str')
def get_asset_checksum(token: str, problem_id: int, asset_type: str):
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    if asset_type not in SUPPORTED_ASSET_TYPES:
        return HTTPError(f'Unsupported asset type: {asset_type}', 400)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'Problem {problem_id} not found', 404)
    asset_path = (problem.config or {}).get('assetPaths', {}).get(asset_type)
    if not asset_path:
        return HTTPResponse(data={'checksum': None})
    minio_client = MinioClient()
    try:
        content = minio_client.download_file(asset_path)
    except Exception as exc:
        logger = None
        try:
            logger = current_app.logger
        except Exception:
            logger = logging.getLogger(__name__)
        if logger:
            logger.exception('Failed to fetch asset checksum')
        return HTTPError(f'Failed to fetch asset: {exc}', 500)
    digest = hashlib.md5(content).hexdigest()
    return HTTPResponse(data={'checksum': digest})


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
    '''Serve sandbox metadata (tasks, submission/execution modes, assets).'''
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'{problem} not found', 404)
    submission_mode = getattr(problem.test_case, 'submission_mode', 0) or 0
    config_payload, pipeline_payload = _build_config_and_pipeline(problem)
    meta = {
        'tasks':
        [json.loads(task.to_json()) for task in problem.test_case.tasks],
        'submissionMode': submission_mode,
    }
    execution_mode = pipeline_payload.get('executionMode', 'general')
    custom_checker = pipeline_payload.get(
        'customChecker', config_payload.get('customChecker', False))
    scoring_cfg = pipeline_payload.get(
        'scoringScript', config_payload.get('scoringScript',
                                            {'custom': False}))
    if isinstance(scoring_cfg, dict):
        scoring_cfg = scoring_cfg.get('custom', False)
    scoring_custom = bool(scoring_cfg)
    meta.update({
        'executionMode':
        execution_mode,
        'teacherFirst':
        pipeline_payload.get('teacherFirst', False),
        'allowRead':
        pipeline_payload.get('allowRead',
                             config_payload.get('allowRead', False)),
        'allowWrite':
        pipeline_payload.get('allowWrite',
                             config_payload.get('allowWrite', False)),
        'assetPaths':
        config_payload.get('assetPaths', {}),
        'buildStrategy':
        _derive_build_strategy(
            problem=problem,
            submission_mode=submission_mode,
            execution_mode=execution_mode,
        ),
        'resourceData':
        config_payload.get('resourceData', False),
        'resourceDataTeacher':
        config_payload.get('resourceDataTeacher', False),
        'customChecker':
        bool(custom_checker),
        'checkerAsset': (config_payload.get('assetPaths', {})
                         or {}).get('checker'),
        'scoringScript':
        scoring_custom,
        'scorerAsset': (config_payload.get('assetPaths', {})
                        or {}).get('scoring_script'),
        'artifactCollection':
        config_payload.get('artifactCollection', []),
    })
    network_cfg = config_payload.get('networkAccessRestriction')
    if network_cfg:
        meta['networkAccessRestriction'] = network_cfg
    return HTTPResponse(data=meta)


@problem_api.route('/<int:problem_id>/meta', methods=['PUT'])
@identity_verify(0, 1)
@Request.doc('problem_id', 'problem', Problem)
def update_problem_meta(user: User, problem: Problem):
    '''Update problem config/pipeline only (no files).'''
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    if not request.content_type or not request.content_type.startswith(
            'application/json'):
        return HTTPError(
            'Content-Type must be application/json',
            400,
            data={'contentType': request.content_type},
        )
    data = request.json or {}
    config_payload = data.get('config') or {}
    pipeline_payload = data.get('pipeline') or {}

    legacy_config = {}
    for key in (
            'compilation',
            'aiVTuber',
            'aiVTuberMaxToken',
            'aiVTuberMode',
            'acceptedFormat',
            'artifactCollection',
            'maxStudentZipSizeMB',
            'networkAccessRestriction',
    ):
        if config_payload.get(key) is not None:
            legacy_config[key] = config_payload[key]
    static_analysis = (config_payload.get('staticAnalysis')
                       or config_payload.get('staticAnalys')
                       or pipeline_payload.get('staticAnalysis'))
    if config_payload.get('networkAccessRestriction'):
        static_analysis = static_analysis or {}
        static_analysis['networkAccessRestriction'] = config_payload[
            'networkAccessRestriction']
    if static_analysis:
        legacy_config['staticAnalysis'] = static_analysis
        legacy_config['staticAnalys'] = static_analysis
    kwargs = {}
    if legacy_config:
        kwargs['config'] = drop_none(legacy_config)

    legacy_pipeline = {}
    for key in (
            'allowRead',
            'allowWrite',
            'executionMode',
            'customChecker',
            'teacherFirst',
    ):
        if pipeline_payload.get(key) is not None:
            legacy_pipeline[key] = pipeline_payload[key]
    if ('scoringScript' in pipeline_payload
            and pipeline_payload['scoringScript'] is not None):
        legacy_pipeline['scoringScript'] = pipeline_payload['scoringScript']
        legacy_pipeline['scoringScrip'] = pipeline_payload['scoringScript']
    if ('scoringScrip' in pipeline_payload
            and pipeline_payload['scoringScrip'] is not None):
        legacy_pipeline['scoringScript'] = pipeline_payload['scoringScrip']
    if ('staticAnalysis' in pipeline_payload
            and pipeline_payload['staticAnalysis'] is not None):
        legacy_pipeline['staticAnalysis'] = pipeline_payload['staticAnalysis']
    if legacy_pipeline:
        kwargs['pipeline'] = drop_none(legacy_pipeline)

    test_mode_payload = data.get('Test_Mode') or {}
    derived_test_mode = {}
    if 'trialMode' in config_payload:
        derived_test_mode['Enabled'] = config_payload['trialMode']
    if 'trialModeQuotaPerStudent' in config_payload:
        derived_test_mode['Quota_Per_Student'] = config_payload[
            'trialModeQuotaPerStudent']
    if derived_test_mode:
        test_mode_payload = {
            **test_mode_payload,
            **{
                k: v
                for k, v in derived_test_mode.items() if v is not None
            },
        }
    if test_mode_payload:
        kwargs['Test_Mode'] = drop_none(test_mode_payload)

    if not kwargs:
        return HTTPResponse('Success.')
    try:
        Problem.edit_problem(
            user=user,
            problem_id=problem.id,
            **kwargs,
        )
    except ValidationError as ve:
        return HTTPError(
            'Invalid or missing arguments.',
            400,
            data=ve.to_dict(),
        )
    except engine.DoesNotExist:
        return HTTPError('Course not found', 404)
    except ValueError as exc:
        return HTTPError(str(exc), 400)
    return HTTPResponse('Success.')


@problem_api.route('/<int:problem_id>/asset/<asset_type>', methods=['GET'])
@Request.args('token: str')
def download_problem_asset(token: str, problem_id: int, asset_type: str):
    '''Allow sandbox to download teacher-provided assets via assetPaths.'''
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'{problem} not found', 404)
    asset_paths = (problem.config or {}).get('assetPaths', {})
    path = asset_paths.get(asset_type)
    if not path:
        return HTTPError('Asset not found', 404)
    minio_client = MinioClient()
    try:
        obj = minio_client.client.get_object(minio_client.bucket, path)
        data = obj.read()
    except Exception as exc:
        return HTTPError(str(exc), 404)
    finally:
        try:
            obj.close()
            obj.release_conn()
        except Exception:
            pass
    filename = path.split('/')[-1] or f'{asset_type}'
    return send_file(
        BytesIO(data),
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=filename,
    )


@problem_api.route('/<int:problem_id>/asset/<asset_type>/download',
                   methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def download_problem_asset_manage(user: User, problem: Problem,
                                  asset_type: str):
    '''Allow managers (teacher/admin) to download uploaded assets.'''
    if not problem.permission(user, problem.Permission.MANAGE):
        return permission_error_response()
    if not problem.permission(user=user, req=problem.Permission.ONLINE):
        return online_error_response()
    asset_paths = (problem.config or {}).get('assetPaths', {})
    path = asset_paths.get(asset_type)
    if not path:
        return HTTPError('Asset not found', 404)
    minio_client = MinioClient()
    try:
        obj = minio_client.client.get_object(minio_client.bucket, path)
        data = obj.read()
    except Exception as exc:
        return HTTPError(str(exc), 404)
    finally:
        try:
            obj.close()
            obj.release_conn()
        except Exception:
            pass
    filename = path.split('/')[-1] or f'{asset_type}'
    return send_file(
        BytesIO(data),
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=filename,
    )


@problem_api.route('/<int:problem_id>/rules', methods=['GET'])
@Request.args('token: str')
def get_static_analysis_rules(token: str, problem_id: int):
    '''Expose static-analysis library restrictions for sandbox.'''
    if sandbox.find_by_token(token) is None:
        return HTTPError('Invalid sandbox token', 401)
    problem = Problem(problem_id)
    if not problem:
        return HTTPError(f'{problem} not found', 404)
    rules = _build_static_analysis_rules(problem) or {}

    return HTTPResponse(data=rules)


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
    ret['average'] = (None if total_students == 0 else
                      statistics.mean(students_high_scores))
    ret['std'] = (None if total_students <= 1 else
                  statistics.pstdev(students_high_scores))
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
        symbols = [
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
        headers = sorted({s for s in symbols if s.endswith('.h')})
        functions = sorted({s for s in symbols if not s.endswith('.h')})
        imports = []  # Python import list is intentionally empty for now

        return HTTPResponse(
            'Success.',
            data={
                'librarySymbols': {
                    'imports': imports,
                    'headers': headers,
                    'functions': functions,
                }
            },
        )

    except Exception as e:
        return HTTPError(str(e), 400)


@problem_api.get("/<int:problem_id>/public-testcases")
@login_required
def get_public_testcases(user, problem_id: int):
    # Load problem
    problem = Problem(problem_id)
    if not problem or not getattr(problem, "obj", None):
        return HTTPError("Problem not found.", 404)

    # Enforce test mode: if field exists and is False -> forbid
    if hasattr(problem.obj, "test_mode_enabled") and not getattr(
            problem.obj, "test_mode_enabled", False):
        return HTTPError("Test mode disabled.", 403)

    # Redis Cache Lookup
    cache_key = f'PROBLEM_PUBLIC_TESTCASES_{problem_id}'
    cache = RedisCache()

    if cache.exists(cache_key):
        try:
            # Cache hit: load from cache
            current_app.logger.debug(f"Cache hit for {cache_key}")
            cached_data = json.loads(cache.get(cache_key))
            return HTTPResponse("OK", data=cached_data)
        except Exception as e:
            current_app.logger.error(
                f"Cache hit but failed to parse for {cache_key}: {e}")
            # If fails: continue to fetch from MinIO

    # Cache miss or fail: Fetch from MinIO
    zip_path = getattr(problem, "public_cases_zip_minio_path", None)
    if not zip_path:
        return HTTPError("No public testcases.", 404)

    # Fetch ZIP from MinIO
    minio = MinioClient()
    try:
        obj = minio.client.get_object(minio.bucket, zip_path)
        raw = obj.read()
    except Exception as e:
        current_app.logger.error(
            f"Error loading public testcases for problem id-{problem_id}: {e}")
        return HTTPError(f"Failed to load testcases: {e}", 500)
    finally:
        try:
            obj.close()
            obj.release_conn()
        except Exception:
            pass

    # Defaults from first task (if exists)
    default_mem = None
    default_time = None
    try:
        if getattr(problem, "test_case", None) and problem.test_case.tasks:
            default_mem = problem.test_case.tasks[0].memory_limit
            default_time = problem.test_case.tasks[0].time_limit
    except Exception:
        pass

    # Parse ZIP
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        current_app.logger.warning(
            f"Invalid ZIP content for problem id-{problem_id}.")
        return HTTPError("Invalid ZIP content.", 500)

    names = set(zf.namelist())
    ins = [n for n in names if n.lower().endswith(".in")]
    cases = []
    for name in sorted(ins):
        base = name[:-3]  # strip '.in'
        out_name = base + ".out"
        try:
            inp = zf.read(name).decode("utf-8", errors="replace")
        except Exception:
            inp = ""
        try:
            outp = (zf.read(out_name).decode("utf-8", errors="replace")
                    if out_name in names else "")
        except Exception:
            outp = ""
        cases.append({
            "File_Name": base,
            "Memory_Limit": default_mem,
            "Time_Limit": default_time,
            "Input_Content": inp,
            "Output_Content": outp,
        })

    response_data = {"Trial_Cases": cases}

    # Store in Redis Cache
    try:
        cache.set(cache_key,
                  json.dumps(response_data),
                  ex=PUBLIC_TESTCASES_TTL)
    except Exception as e:
        current_app.logger.warning(f"Failed to set cache for {cache_key}: {e}")

    return HTTPResponse("OK", data=response_data)


@problem_api.route('/<int:problem_id>/trial/history', methods=['GET'])
@login_required
@Request.doc('problem_id', 'problem', Problem)
def view_trial_history(
    user: User,
    problem: Problem,
):
    """
    Get trial submission history for a problem
    """
    current_app.logger.info(
        f"User {user.username} is requesting trial history for problem id-{problem.id}"
    )
    # 1. Permission Check: Pass if user has VIEW permission
    if not problem.permission(user, problem.Permission.VIEW):
        current_app.logger.info(
            f"User {user.username} attempted to access trial history for problem id-{problem.id} without permission."
        )
        return permission_error_response()

    # 2. Call Model to handle business logic
    try:
        data = TrialSubmission.get_history_for_api(user=user, problem=problem)

        # 3. Return the result
        return HTTPResponse('Success.', data=data)
    except Exception as e:
        current_app.logger.error(f"Error retrieving trial history: {str(e)}")
        return HTTPError(f"Failed to retrieve trial history: {str(e)}", 500)


@problem_api.post("/<int:problem_id>/trial/request")
@login_required
@Request.json(
    "language_type: int",
    "use_default_test_cases: bool?",
)
def request_trial_submission(user,
                             problem_id: int,
                             language_type: int,
                             use_default_test_cases: bool = True):
    """
    Create a trial submission request
    
    Returns:
        Trial_Submission_Id if successful
    """
    current_app.logger.info(
        f"Requesting trial submission for problem id-{problem_id} by user {user.username}"
    )
    # Load problem
    problem_proxy = Problem(problem_id)
    if not problem_proxy or not getattr(problem_proxy, "obj", None):
        return HTTPError("Problem not found.", 404)

    problem = problem_proxy

    # Validate language type (0: C, 1: C++, 2: Python)
    if language_type not in [0, 1, 2]:
        return HTTPError(
            "Invalid language type. Must be 0 (C), 1: C++, 2: Python).", 400)

    # Check if user has permission to submit
    if not problem.permission(user, Problem.Permission.ONLINE):
        return HTTPError(
            "You don't have permission to submit to this problem.", 403)

    # Use TrialSubmission.add() instead of creating engine object directly
    try:
        trial_submission = TrialSubmission.add(
            problem_id=problem_id,
            username=user.username,
            lang=language_type,
            timestamp=datetime.now(),
            ip_addr=request.remote_addr,
            use_default_case=use_default_test_cases)

        return HTTPResponse(
            "Trial submission created successfully.",
            data={"Trial_Submission_Id": str(trial_submission.id)})
    except PermissionError as e:
        current_app.logger.info(
            f"Permission error for trial submission by user {user.username} on problem id-{problem_id}: {str(e)}"
        )
        return HTTPError(str(e), 403)
    except Exception as e:
        current_app.logger.error(f"Error creating trial submission: {str(e)}")
        return HTTPError(f"Failed to create trial submission: {str(e)}", 500)
