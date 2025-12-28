import copy
import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from flask import after_this_request, request, send_file
from mongo import Course, Problem, User
from mongo.problem.archive_utils import (
    ASSET_COMPONENTS,
    DEFAULT_MAX_COMPRESSION_RATIO,
    DEFAULT_MAX_FILE_SIZE_MB,
    DEFAULT_MAX_TOTAL_SIZE_MB,
    build_component_hash,
    build_problem_content_hash,
    canonical_json_bytes,
    pretty_json_bytes,
    redact_meta,
    safe_zip_name,
    sha256_bytes,
    strip_meta_for_hash,
    strip_submission_mode,
    validate_zip_entries,
)
from mongo.utils import MinioClient

from .auth import login_required
from .utils import HTTPError, HTTPResponse, Request
from .utils.problem_utils import build_config_and_pipeline as _build_config_and_pipeline


def permission_error_response():
    return HTTPError('Not enough permission', 403)


def _parse_course_list(course_name: Optional[str],
                       courses_raw: Optional[str]) -> List[str]:
    if courses_raw:
        courses_raw = courses_raw.strip()
        try:
            if courses_raw.startswith('['):
                courses = json.loads(courses_raw)
            else:
                courses = [
                    c.strip() for c in courses_raw.split(',') if c.strip()
                ]
        except (TypeError, ValueError, json.JSONDecodeError):
            raise ValueError(
                'courses must be a JSON array or comma-separated string')
    elif course_name:
        courses = [course_name]
    else:
        raise ValueError('course or courses are required')

    if not isinstance(courses, list):
        raise ValueError('courses must be a list')

    normalized: List[str] = []
    seen = set()
    for name in courses:
        name = str(name).strip()
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    if not normalized:
        raise ValueError('courses must be a non-empty list')
    return normalized


def _resolve_import_targets(user: User,
                            course_names: List[str]) -> List[Tuple[str, User]]:
    targets: List[Tuple[str, User]] = []
    for name in course_names:
        course = Course(name)
        if not course:
            raise ValueError(f'Course not found: {name}')
        if not course.permission(user, Course.Permission.MODIFY):
            raise PermissionError(f'No modify permission for course: {name}')
        teacher = course.teacher
        if not teacher:
            raise ValueError(f'Course has no teacher: {name}')
        owner_user = User(teacher.username)
        if not owner_user:
            raise ValueError('Course teacher not found')
        targets.append((course.course_name, owner_user))
    return targets


def _normalize_export_config(config: Dict) -> Dict:
    if not isinstance(config, dict):
        return {}

    config.setdefault('trialMode', False)
    config.setdefault('trialResultVisible', False)
    config.setdefault('trialResultDownloadable', False)
    config.setdefault('aiVTuber', False)
    config.setdefault('aiVTuberMode', 'gemini-2.5-flash-lite')
    if config.get('aiMaxToken') is None:
        config['aiMaxToken'] = 500
    config.setdefault('acceptedFormat', 'code')
    config.setdefault('compilation', False)
    config.setdefault('resourceData', False)
    config.setdefault('resourceDataTeacher', False)
    config.setdefault('maxStudentZipSizeMB', 50)
    config.setdefault('exposeTestcase', False)
    config.setdefault('artifactCollection', [])

    ai_checker = config.get('aiChecker')
    if isinstance(ai_checker, dict):
        ai_checker.setdefault('enabled', False)

    return config


def _normalize_export_pipeline(pipeline: Dict) -> Dict:
    if not isinstance(pipeline, dict):
        return {}

    pipeline.setdefault('allowRead', False)
    pipeline.setdefault('allowWrite', False)
    pipeline.setdefault('executionMode', 'general')
    pipeline.setdefault('customChecker', False)
    pipeline.setdefault('teacherFirst', False)
    pipeline.setdefault('scoringScript', {'custom': False})

    static_analysis = pipeline.get('staticAnalysis') or {}
    if not isinstance(static_analysis, dict):
        static_analysis = {}
    libs = static_analysis.get('libraryRestrictions')
    if not isinstance(libs, dict):
        static_analysis['libraryRestrictions'] = {
            'enabled': False,
            'whitelist': {
                'syntax': [],
                'imports': [],
                'headers': [],
                'functions': [],
            },
            'blacklist': {
                'syntax': [],
                'imports': [],
                'headers': [],
                'functions': [],
            },
        }
    else:
        libs.setdefault('enabled', False)
        libs.setdefault('whitelist', {
            'syntax': [],
            'imports': [],
            'headers': [],
            'functions': [],
        })
        libs.setdefault('blacklist', {
            'syntax': [],
            'imports': [],
            'headers': [],
            'functions': [],
        })
        for mode in ('whitelist', 'blacklist'):
            for key in ('syntax', 'imports', 'headers', 'functions'):
                libs[mode].setdefault(key, [])
    pipeline['staticAnalysis'] = static_analysis

    return pipeline


def _build_export_meta(problem: Problem) -> Dict:
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
    info['defaultCode'] = problem.default_code
    config_payload, pipeline_payload = _build_config_and_pipeline(problem)
    trial_enabled = problem.trial_mode_enabled
    config_payload['trialMode'] = trial_enabled
    config_payload['testMode'] = trial_enabled
    if config_payload:
        info['config'] = _normalize_export_config(config_payload)
    if pipeline_payload:
        info['pipeline'] = _normalize_export_pipeline(pipeline_payload)
    return info


def _extract_asset_sources(
    problem: Problem,
    config: Dict,
) -> Tuple[List[Dict], Dict]:
    asset_paths = copy.deepcopy(config.get('assetPaths') or {})
    asset_paths.pop('scoring_config', None)
    updated_asset_paths = {
        k: v
        for k, v in asset_paths.items() if k not in ASSET_COMPONENTS
    }
    entries = []

    for asset_type, (component_id, base_dir) in ASSET_COMPONENTS.items():
        source_path = None
        if asset_type == 'public_testdata':
            source_path = problem.public_cases_zip_minio_path or asset_paths.get(
                'public_testdata')
        elif asset_type == 'ac_code':
            source_path = problem.ac_code_minio_path or asset_paths.get(
                'ac_code')
        else:
            source_path = asset_paths.get(asset_type)
        if not source_path:
            continue
        filename = Path(source_path).name
        archive_path = f"{base_dir}/{filename}"
        updated_asset_paths[asset_type] = archive_path
        entries.append({
            'asset_type': asset_type,
            'component_id': component_id,
            'source_path': source_path,
            'archive_path': archive_path,
        })
    return entries, updated_asset_paths


def _stream_to_zip(zf, arcname: str, reader) -> Tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with zf.open(arcname, 'w') as dest:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            dest.write(chunk)
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def _stream_to_hash(reader) -> Tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = reader.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def _stream_minio_to_zip(
    minio_client: MinioClient,
    object_path: str,
    zf,
    arcname: str,
) -> Tuple[str, int]:
    resp = minio_client.client.get_object(minio_client.bucket, object_path)
    try:
        return _stream_to_zip(zf, arcname, resp)
    finally:
        resp.close()
        resp.release_conn()


def _stream_minio_to_hash(
    minio_client: MinioClient,
    object_path: str,
) -> Tuple[str, int]:
    resp = minio_client.client.get_object(minio_client.bucket, object_path)
    try:
        return _stream_to_hash(resp)
    finally:
        resp.close()
        resp.release_conn()


def _stream_file_to_zip(file_obj, zf, arcname: str) -> Tuple[str, int]:
    try:
        file_obj.seek(0)
    except Exception:
        pass
    return _stream_to_zip(zf, arcname, file_obj)


def _stream_file_to_hash(file_obj) -> Tuple[str, int]:
    try:
        file_obj.seek(0)
    except Exception:
        pass
    return _stream_to_hash(file_obj)


def _build_manifest(
    exported_by: str,
    components: Dict[str, Dict],
    files: Dict[str, Dict],
    redactions: List[str],
    component_hashes: Dict[str, str],
) -> Dict:
    return {
        'formatVersion': '1.1',
        'exportedAt': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'exportedBy': exported_by,
        'sourceSystem': 'Normal-OJ',
        'problemContentHash': build_problem_content_hash(component_hashes),
        'components': components,
        'files': files,
        'redactions': redactions,
    }


def _component_required(meta: Dict) -> List[str]:
    required = ['core.meta', 'core.testcase']
    config = meta.get('config') or {}
    pipeline = meta.get('pipeline') or {}
    if pipeline.get('customChecker'):
        required.append('assets.checker')
    if pipeline.get('executionMode') == 'functionOnly':
        required.append('assets.makefile')
    if pipeline.get('executionMode') == 'interactive':
        required.append('assets.teacher_file')
    scoring_cfg = pipeline.get('scoringScript')
    if isinstance(scoring_cfg, dict):
        scoring_cfg = scoring_cfg.get('custom', False)
    if scoring_cfg:
        required.append('assets.scoring_script')
    if _is_trial_mode_enabled(config):
        required.append('trial.public_testdata')
        required.append('trial.ac_code')
    if config.get('resourceData'):
        required.append('assets.resource_data')
    if config.get('resourceDataTeacher'):
        required.append('assets.resource_data_teacher')
    return required


def _ensure_admin_or_owner(user: User, problem: Problem):
    if user.role == 0 or problem.owner == user.username:
        return
    raise PermissionError('Not enough permission to export this problem')


def _is_trial_mode_enabled(config: Dict) -> bool:
    trial_mode = config.get('trialMode')
    if trial_mode is None:
        trial_mode = config.get('testMode', False)
    return bool(trial_mode)


def _scoring_script_enabled(pipeline: Dict, config: Dict) -> bool:
    scoring_cfg = pipeline.get('scoringScript')
    if scoring_cfg is None:
        scoring_cfg = config.get('scoringScript')
    if isinstance(scoring_cfg, dict):
        return bool(scoring_cfg.get('custom'))
    return bool(scoring_cfg)


def _collect_asset_feature_mismatches(meta: Dict,
                                      included_components: set) -> List[str]:
    config = meta.get('config') or {}
    pipeline = meta.get('pipeline') or {}
    execution_mode = pipeline.get('executionMode') or config.get(
        'executionMode')
    trial_mode = _is_trial_mode_enabled(config)

    checks = {
        'assets.checker':
        bool(pipeline.get('customChecker') or config.get('customChecker')),
        'assets.makefile':
        execution_mode == 'functionOnly',
        'assets.teacher_file':
        execution_mode == 'interactive',
        'assets.scoring_script':
        _scoring_script_enabled(pipeline, config),
        'trial.public_testdata':
        bool(trial_mode),
        'trial.ac_code':
        bool(trial_mode),
        'assets.resource_data':
        bool(config.get('resourceData')),
        'assets.resource_data_teacher':
        bool(config.get('resourceDataTeacher')),
        'assets.network_dockerfile':
        bool(config.get('networkAccessEnabled')),
        'assets.local_service':
        bool(config.get('networkAccessEnabled')),
    }
    return [
        component_id for component_id, enabled in checks.items()
        if component_id in included_components and not enabled
    ]


def _validate_asset_feature_alignment(meta: Dict,
                                      included_components: set) -> None:
    mismatches = _collect_asset_feature_mismatches(meta, included_components)
    if mismatches:
        raise ValueError(
            'Assets included but corresponding feature is disabled: '
            f'{", ".join(sorted(mismatches))}')


def _apply_meta_component_filter(
    meta: Dict,
    include_filter: Optional[set],
) -> None:
    if not include_filter:
        return
    config = meta.get('config') or {}
    pipeline = meta.get('pipeline') or {}

    if 'settings.network' not in include_filter:
        config.pop('networkAccessEnabled', None)
        config.pop('networkAccessRestriction', None)
        static_analysis = pipeline.get('staticAnalysis')
        if isinstance(static_analysis, dict):
            static_analysis.pop('networkAccessRestriction', None)

    if 'settings.ai_ta' not in include_filter:
        config.pop('aiVTuber', None)
        config.pop('aiVTuberMode', None)
        config.pop('aiMaxToken', None)
        config.pop('aiVTuberApiKeys', None)

    if 'settings.artifact' not in include_filter:
        config.pop('artifactCollection', None)

    if 'settings.file_access' not in include_filter:
        config.pop('allowRead', None)
        config.pop('allowWrite', None)
        pipeline.pop('allowRead', None)
        pipeline.pop('allowWrite', None)

    if 'static_analysis' not in include_filter:
        pipeline.pop('staticAnalysis', None)
        config.pop('staticAnalysis', None)
        config.pop('staticAnalys', None)

    meta['config'] = config
    meta['pipeline'] = pipeline


def _normalize_network_access(config: Dict, included_components: set) -> None:
    if not isinstance(config, dict):
        return

    network_enabled = config.get('networkAccessEnabled')
    nar = config.get('networkAccessRestriction')
    nar_dict = nar if isinstance(nar, dict) else {}

    external = nar_dict.get('external')
    sidecars = nar_dict.get('sidecars')
    has_external = False
    has_sidecars = False
    if isinstance(external, dict):
        has_external = bool(external.get('ip') or external.get('url'))
    if isinstance(sidecars, list):
        has_sidecars = len(sidecars) > 0

    # Legacy schema detection (keep data, but enable switch if present).
    has_legacy = False
    if isinstance(nar_dict, dict):
        has_legacy = bool(
            nar_dict.get('enabled') or nar_dict.get('firewallExtranet')
            or nar_dict.get('connectWithLocal'))

    has_network_assets = any(component_id in included_components
                             for component_id in ('assets.network_dockerfile',
                                                  'assets.local_service'))

    if network_enabled is None:
        network_enabled = has_external or has_sidecars or has_legacy or has_network_assets

    config['networkAccessEnabled'] = bool(network_enabled)

    if config['networkAccessEnabled']:
        if not isinstance(nar, dict):
            config['networkAccessRestriction'] = {
                'sidecars': [],
                'external': {
                    'model': 'White',
                    'ip': [],
                    'url': [],
                },
            }
        else:
            nar.setdefault('sidecars', [])
            external_cfg = nar.get('external')
            if not isinstance(external_cfg, dict):
                nar['external'] = {
                    'model': 'White',
                    'ip': [],
                    'url': [],
                }
            else:
                external_cfg.setdefault('model', 'White')
                external_cfg.setdefault('ip', [])
                external_cfg.setdefault('url', [])


def _write_problem_to_zip(
    zf,
    user: User,
    problem: Problem,
    prefix: str = '',
    components: Optional[List[str]] = None,
) -> Dict:
    _ensure_admin_or_owner(user, problem)
    if prefix and not prefix.endswith('/'):
        prefix = f'{prefix}/'

    meta = _build_export_meta(problem)
    meta.pop('ACUser', None)
    meta.pop('submitter', None)
    meta.pop('submitCount', None)
    meta.pop('trialSubmissionCount', None)

    meta, redactions = redact_meta(meta)
    strip_submission_mode(meta)

    config = meta.get('config') or {}
    asset_entries, updated_asset_paths = _extract_asset_sources(
        problem,
        config,
    )
    component_ids = {entry['component_id'] for entry in asset_entries}
    _normalize_network_access(config, component_ids)
    config['assetPaths'] = updated_asset_paths
    meta['config'] = config

    include_filter = set(components or [])
    if include_filter:
        include_filter.update({'core.meta', 'core.testcase'})
        _apply_meta_component_filter(meta, include_filter)
        include_filter.update(_component_required(meta))

    present_components = {entry['component_id'] for entry in asset_entries}
    missing_required = [
        cid for cid in _component_required(meta)
        if cid not in present_components and cid not in ('core.meta',
                                                         'core.testcase')
    ]
    if missing_required:
        raise ValueError('Required components are enabled but missing files: '
                         f'{", ".join(sorted(missing_required))}')

    included_components = set(present_components)
    if include_filter:
        included_components = {
            cid
            for cid in present_components if cid in include_filter
        }
    _validate_asset_feature_alignment(meta, included_components)

    files_manifest: Dict[str, Dict] = {}
    component_files: Dict[str, List[Dict]] = {}
    component_hashes: Dict[str, str] = {}
    minio_client = MinioClient()

    meta_bytes = pretty_json_bytes(meta)
    meta_sha = sha256_bytes(meta_bytes)
    zf.writestr(f'{prefix}meta.json', meta_bytes)
    files_manifest['meta.json'] = {
        'sha256': meta_sha,
        'size': len(meta_bytes),
        'role': 'core.meta',
    }
    component_files.setdefault('core.meta', []).append({
        'path': 'meta.json',
        'sha256': meta_sha,
        'size': len(meta_bytes),
    })

    if not problem.is_test_case_ready():
        raise ValueError('Test case is not ready')
    if problem.test_case.case_zip_minio_path:
        test_sha, test_size = _stream_minio_to_zip(
            minio_client,
            problem.test_case.case_zip_minio_path,
            zf,
            f'{prefix}testcase.zip',
        )
    else:
        test_case_obj = problem.get_test_case()
        test_sha, test_size = _stream_file_to_zip(
            test_case_obj,
            zf,
            f'{prefix}testcase.zip',
        )
    files_manifest['testcase.zip'] = {
        'sha256': test_sha,
        'size': test_size,
        'role': 'core.testcase',
    }
    component_files.setdefault('core.testcase', []).append({
        'path': 'testcase.zip',
        'sha256': test_sha,
        'size': test_size,
    })

    for entry in asset_entries:
        component_id = entry['component_id']
        arcname = entry['archive_path']
        include = (not include_filter or component_id in include_filter)
        if include:
            sha, size = _stream_minio_to_zip(
                minio_client,
                entry['source_path'],
                zf,
                f'{prefix}{arcname}',
            )
            files_manifest[arcname] = {
                'sha256': sha,
                'size': size,
                'role': component_id,
            }
        else:
            sha, size = _stream_minio_to_hash(
                minio_client,
                entry['source_path'],
            )
        component_files.setdefault(component_id, []).append({
            'path': arcname,
            'sha256': sha,
            'size': size,
        })

    components_manifest: Dict[str, Dict] = {}
    for component_id, files in component_files.items():
        if component_id == 'core.meta':
            meta_hash_payload = canonical_json_bytes(strip_meta_for_hash(meta))
            component_hashes[
                component_id] = f"sha256:{sha256_bytes(meta_hash_payload)}"
        else:
            component_hashes[component_id] = build_component_hash(
                component_id,
                files,
            )
        included = (not include_filter or component_id in include_filter)
        if component_id in ('core.meta', 'core.testcase'):
            included = True
        file_paths = [f['path'] for f in files] if included else []
        components_manifest[component_id] = {
            'included': included,
            'hash': component_hashes[component_id],
            'files': file_paths,
        }

    manifest = _build_manifest(
        exported_by=user.username,
        components=components_manifest,
        files=files_manifest,
        redactions=redactions,
        component_hashes=component_hashes,
    )
    manifest_bytes = pretty_json_bytes(manifest)
    zf.writestr(f'{prefix}manifest.json', manifest_bytes)
    return manifest


def _export_problem_archive(
    user: User,
    problem: Problem,
    components: Optional[List[str]] = None,
) -> Tuple[str, Dict]:
    tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.noj.zip')
    tmp_file.close()
    with zipfile.ZipFile(tmp_file.name, 'w', zipfile.ZIP_DEFLATED) as zf:
        manifest = _write_problem_to_zip(
            zf,
            user,
            problem,
            prefix='',
            components=components,
        )
    return tmp_file.name, manifest


def _import_problem_archive(
    user: User,
    zip_file: zipfile.ZipFile,
    courses: List[str],
    status_override: Optional[int] = None,
    prefix: str = '',
    components: Optional[List[str]] = None,
    owner_user: Optional[User] = None,
) -> Dict:
    validate_zip_entries(
        zip_file,
        max_file_size=DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024,
        max_total_size=DEFAULT_MAX_TOTAL_SIZE_MB * 1024 * 1024,
        max_ratio=DEFAULT_MAX_COMPRESSION_RATIO,
    )
    if prefix and not prefix.endswith('/'):
        prefix = f'{prefix}/'

    manifest_path = f'{prefix}manifest.json'
    meta_path = f'{prefix}meta.json'
    if manifest_path not in zip_file.namelist():
        raise ValueError('manifest.json missing')
    if meta_path not in zip_file.namelist():
        raise ValueError('meta.json missing')

    manifest = json.loads(zip_file.read(manifest_path))
    meta = json.loads(zip_file.read(meta_path))
    strip_submission_mode(meta)
    meta, _ = redact_meta(meta)
    if 'testCase' not in meta and 'testCaseInfo' in meta:
        meta['testCase'] = meta.get('testCaseInfo')
    if isinstance(meta.get('config'), dict):
        asset_paths_meta = meta['config'].get('assetPaths')
        if isinstance(asset_paths_meta, dict):
            asset_paths_meta.pop('scoring_config', None)

    required_fields = [
        'problemName',
        'description',
        'allowedLanguage',
        'quota',
        'type',
        'status',
        'testCase',
        'canViewStdout',
        'defaultCode',
        'config',
        'pipeline',
    ]
    missing = [key for key in required_fields if key not in meta]
    if missing:
        raise ValueError(f'Missing required fields: {", ".join(missing)}')

    components_manifest = manifest.get('components') or {}
    included_components = {
        cid
        for cid, data in components_manifest.items()
        if data.get('included', True)
    }
    if not components_manifest:
        included_components = set()
        for file_meta in (manifest.get('files') or {}).values():
            role = file_meta.get('role')
            if role:
                included_components.add(role)
        included_components.update({'core.meta', 'core.testcase'})

    if components:
        requested_components = set(components)
        requested_components.update({'core.meta', 'core.testcase'})
        included_components = {
            cid
            for cid in included_components if cid in requested_components
        }
        _apply_meta_component_filter(meta, requested_components)

    _normalize_network_access(meta.get('config') or {}, included_components)

    _validate_asset_feature_alignment(meta, included_components)

    required_components = _component_required(meta)
    missing_components = [
        cid for cid in required_components if cid not in included_components
    ]
    if missing_components:
        raise ValueError(
            f'Missing required components: {", ".join(missing_components)}')

    files_manifest = manifest.get('files') or {}
    if components and files_manifest:
        filtered_files: Dict[str, Dict] = {}
        for rel_path, meta_info in files_manifest.items():
            role = meta_info.get('role') if isinstance(meta_info,
                                                       dict) else None
            if role and role not in included_components:
                continue
            filtered_files[rel_path] = meta_info
        files_manifest = filtered_files
    staging_dir = tempfile.mkdtemp(prefix='problem-import-')
    file_map: Dict[str, str] = {}

    try:
        for rel_path, meta_info in files_manifest.items():
            entry_name = f'{prefix}{rel_path}'
            if not safe_zip_name(entry_name):
                raise ValueError(f'Unsafe zip entry: {entry_name}')
            if entry_name not in zip_file.namelist():
                raise ValueError(f'Missing file: {rel_path}')
            info = zip_file.getinfo(entry_name)
            with zip_file.open(info) as src:
                local_path = os.path.join(staging_dir, rel_path)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, 'wb') as dst:
                    digest = hashlib.sha256()
                    total = 0
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
            expected_sha = meta_info.get('sha256')
            expected_size = meta_info.get('size')
            if expected_sha and digest.hexdigest() != expected_sha:
                raise ValueError(f'Checksum mismatch: {rel_path}')
            if expected_size is not None and total != expected_size:
                raise ValueError(f'Size mismatch: {rel_path}')
            file_map[rel_path] = local_path

        status = (status_override if status_override is not None else meta.get(
            'status', 1))
        owner = owner_user or user
        problem_id = Problem.add(
            user=owner,
            courses=courses,
            problem_name=meta.get('problemName', ''),
            status=status,
            description=meta.get('description'),
            tags=meta.get('tags'),
            type=meta.get('type'),
            test_case_info=meta.get('testCase'),
            can_view_stdout=meta.get('canViewStdout', True),
            allowed_language=meta.get('allowedLanguage'),
            quota=meta.get('quota'),
            default_code=meta.get('defaultCode', ''),
            config=meta.get('config'),
            pipeline=meta.get('pipeline'),
        )

        problem = Problem(problem_id)
        minio_client = MinioClient()
        uploaded_paths: List[str] = []
        try:
            testcase_path = file_map.get('testcase.zip')
            if not testcase_path:
                raise ValueError('testcase.zip missing')
            with open(testcase_path, 'rb') as tc_file:
                problem._validate_test_case(tc_file)
                tc_file.seek(0)
                problem._save_test_case_zip(tc_file)
            if problem.test_case.case_zip_minio_path:
                uploaded_paths.append(problem.test_case.case_zip_minio_path)

            asset_paths_meta = (meta.get('config')
                                or {}).get('assetPaths') or {}
            new_asset_paths = {
                k: v
                for k, v in asset_paths_meta.items()
                if k not in ASSET_COMPONENTS
            }

            for asset_type, (component_id,
                             _base_dir) in ASSET_COMPONENTS.items():
                if component_id not in included_components:
                    continue
                rel_path = asset_paths_meta.get(asset_type)
                if not rel_path:
                    continue
                local_path = file_map.get(rel_path)
                if not local_path:
                    raise ValueError(f'Missing asset file: {rel_path}')
                filename = Path(rel_path).name
                if asset_type == 'public_testdata':
                    dest_path = f'problem/{problem_id}/public_testdata/public_testdata.zip'
                elif asset_type == 'ac_code':
                    dest_path = f'problem/{problem_id}/ac_code/{filename}'
                else:
                    dest_path = f'problem/{problem_id}/{asset_type}/{filename}'

                with open(local_path, 'rb') as src:
                    minio_client.upload_file_object(
                        src,
                        dest_path,
                        length=os.path.getsize(local_path),
                        content_type='application/octet-stream',
                    )
                uploaded_paths.append(dest_path)
                new_asset_paths[asset_type] = dest_path

                if asset_type == 'public_testdata':
                    problem.update(public_cases_zip_minio_path=dest_path)
                if asset_type == 'ac_code':
                    ext = Path(filename).suffix.lower().lstrip('.')
                    lang_map = {'c': 0, 'cpp': 1, 'py': 2}
                    problem.update(
                        ac_code_minio_path=dest_path,
                        ac_code_language=lang_map.get(ext),
                    )
                if (asset_type == 'teacher_file'
                        and 'teacherLang' not in new_asset_paths):
                    ext = Path(filename).suffix.lower().lstrip('.')
                    if ext in ('c', 'cpp', 'py'):
                        new_asset_paths['teacherLang'] = ext

            if new_asset_paths:
                Problem.edit_problem(
                    user=owner,
                    problem_id=problem_id,
                    config={
                        'assetPaths': new_asset_paths,
                    },
                )

            return {
                'problemId': problem_id,
                'problemName': meta.get('problemName', ''),
            }
        except Exception:
            for path in uploaded_paths:
                try:
                    minio_client.client.remove_object(minio_client.bucket,
                                                      path)
                except Exception:
                    pass
            try:
                if problem and problem.obj:
                    problem.obj.delete()
            except Exception:
                pass
            raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def init_problem_io(problem_api):

    @problem_api.route('/<int:problem_id>/export', methods=['GET'])
    @login_required(pat_scope=['read:problems'])
    @Request.doc('problem_id', 'problem', Problem)
    def export_problem(user: User, problem: Problem):
        components = request.args.get('components')
        include_components = (
            [c.strip() for c in components.split(',')
             if c.strip()] if components else None)
        try:
            zip_path, _manifest = _export_problem_archive(
                user=user,
                problem=problem,
                components=include_components,
            )
        except PermissionError:
            return permission_error_response()
        except Exception as exc:
            return HTTPError(str(exc), 400)

        @after_this_request
        def _cleanup(response):
            try:
                os.remove(zip_path)
            except Exception:
                pass
            return response

        return send_file(
            zip_path,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'problem_{problem.problem_id}.noj.zip',
        )

    @problem_api.route('/export-batch', methods=['POST'])
    @login_required(pat_scope=['read:problems'])
    def export_problem_batch(user: User):
        data = request.json or {}
        problem_ids = data.get('problemIds') or []
        components_raw = data.get('components')
        if not isinstance(problem_ids, list) or not problem_ids:
            return HTTPError('problemIds must be a non-empty list', 400)

        components = None
        if isinstance(components_raw, list):
            components = [
                str(c).strip() for c in components_raw if str(c).strip()
            ]
        elif isinstance(components_raw, str):
            components = [
                c.strip() for c in components_raw.split(',') if c.strip()
            ]

        tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.noj.zip')
        tmp_file.close()
        problems_manifest = []
        failed = []

        with zipfile.ZipFile(tmp_file.name, 'w', zipfile.ZIP_DEFLATED) as zf:
            for pid in problem_ids:
                try:
                    problem = Problem(pid)
                    if not problem:
                        raise ValueError('Problem not found')
                    manifest = _write_problem_to_zip(
                        zf,
                        user=user,
                        problem=problem,
                        prefix=f'problem_{pid}',
                        components=components,
                    )
                    problems_manifest.append({
                        'problemContentHash':
                        manifest.get('problemContentHash'),
                        'folder':
                        f'problem_{pid}',
                        'originalId':
                        pid,
                        'name':
                        problem.problem_name,
                    })
                except PermissionError:
                    failed.append({
                        'problemId': pid,
                        'reason': 'permission denied',
                    })
                except Exception as exc:
                    failed.append({
                        'problemId': pid,
                        'reason': str(exc),
                    })

            batch_manifest = {
                'formatVersion': '1.1',
                'exportedAt': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
                'exportedBy': user.username,
                'problemCount': len(problems_manifest),
                'problems': problems_manifest,
                'failed': failed,
            }
            zf.writestr('manifest.json', pretty_json_bytes(batch_manifest))

        @after_this_request
        def _cleanup(response):
            try:
                os.remove(tmp_file.name)
            except Exception:
                pass
            return response

        return send_file(
            tmp_file.name,
            mimetype='application/zip',
            as_attachment=True,
            download_name='problems_export.noj.zip',
        )

    @problem_api.route('/import', methods=['POST'])
    @login_required(pat_scope=['write:problems'])
    def import_problem(user: User):
        upload = request.files.get('file')
        course_name = request.form.get('course')
        status = request.form.get('status')
        components_raw = request.form.get('components')
        courses_raw = request.form.get('courses')
        if not upload:
            return HTTPError('file is required', 400)
        try:
            course_names = _parse_course_list(course_name, courses_raw)
            targets = _resolve_import_targets(user, course_names)
        except PermissionError as exc:
            return HTTPError(str(exc), 403)
        except ValueError as exc:
            return HTTPError(str(exc), 400)

        try:
            status_override = int(status) if status is not None else None
        except (TypeError, ValueError):
            return HTTPError('status must be integer', 400)

        components = (
            [c.strip() for c in components_raw.split(',')
             if c.strip()] if components_raw else None)
        try:
            with zipfile.ZipFile(upload) as zf:
                imported = []
                failed = []
                for course, owner_user in targets:
                    try:
                        result = _import_problem_archive(
                            user=user,
                            zip_file=zf,
                            courses=[course],
                            status_override=status_override,
                            components=components,
                            owner_user=owner_user,
                        )
                        result['course'] = course
                        imported.append(result)
                    except Exception as exc:
                        failed.append({
                            'course': course,
                            'reason': str(exc),
                        })
            if len(imported) == 1 and not failed:
                payload = dict(imported[0])
                payload['imported'] = imported
                payload['failed'] = failed
                return HTTPResponse('ok', data=payload)
            return HTTPResponse('ok',
                                data={
                                    'imported': imported,
                                    'failed': failed
                                })
        except Exception as exc:
            return HTTPError(str(exc), 400)

    @problem_api.route('/import-batch', methods=['POST'])
    @login_required(pat_scope=['write:problems'])
    def import_problem_batch(user: User):
        upload = request.files.get('file')
        course_name = request.form.get('course')
        status = request.form.get('status')
        components_raw = request.form.get('components')
        courses_raw = request.form.get('courses')
        if not upload:
            return HTTPError('file is required', 400)
        try:
            course_names = _parse_course_list(course_name, courses_raw)
            targets = _resolve_import_targets(user, course_names)
        except PermissionError as exc:
            return HTTPError(str(exc), 403)
        except ValueError as exc:
            return HTTPError(str(exc), 400)
        try:
            status_override = int(status) if status is not None else None
        except (TypeError, ValueError):
            return HTTPError('status must be integer', 400)

        components = (
            [c.strip() for c in components_raw.split(',')
             if c.strip()] if components_raw else None)
        imported = []
        failed = []
        try:
            with zipfile.ZipFile(upload) as zf:
                if 'manifest.json' not in zf.namelist():
                    return HTTPError('manifest.json missing', 400)
                batch_manifest = json.loads(zf.read('manifest.json'))
                problems = batch_manifest.get('problems') or []
                for item in problems:
                    folder = item.get('folder')
                    if not folder:
                        for course, _owner in targets:
                            failed.append({
                                'originalId': item.get('originalId'),
                                'course': course,
                                'reason': 'missing folder',
                            })
                        continue
                    for course, owner_user in targets:
                        try:
                            result = _import_problem_archive(
                                user=user,
                                zip_file=zf,
                                courses=[course],
                                status_override=status_override,
                                prefix=folder,
                                components=components,
                                owner_user=owner_user,
                            )
                            imported.append({
                                'originalId':
                                item.get('originalId'),
                                'newId':
                                result.get('problemId'),
                                'name':
                                result.get('problemName'),
                                'course':
                                course,
                            })
                        except Exception as exc:
                            failed.append({
                                'originalId': item.get('originalId'),
                                'course': course,
                                'reason': str(exc),
                            })
            return HTTPResponse('ok',
                                data={
                                    'imported': imported,
                                    'failed': failed
                                })
        except Exception as exc:
            return HTTPError(str(exc), 400)
