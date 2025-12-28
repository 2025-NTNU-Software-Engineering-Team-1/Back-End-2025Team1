import copy
import hashlib
import json
import os
import uuid
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_MAX_FILE_SIZE_MB = int(
    os.getenv('PROBLEM_IMPORT_MAX_FILE_SIZE_MB', '100'))
DEFAULT_MAX_TOTAL_SIZE_MB = int(
    os.getenv('PROBLEM_IMPORT_MAX_TOTAL_SIZE_MB', '500'))
DEFAULT_MAX_COMPRESSION_RATIO = float(
    os.getenv('PROBLEM_IMPORT_MAX_COMPRESSION_RATIO', '100.0'))

HASH_EXCLUDED_FIELDS = {
    'problemName',
    'courses',
    'status',
    'tags',
    'ACUser',
    'submitter',
    'submitCount',
    'trialSubmissionCount',
    'exportedAt',
    'exportedBy',
    'owner',
}

REDACTED_PATHS = (
    ('config', 'aiVTuberApiKeys'),
    ('config', 'aiChecker', 'apiKeyId'),
)

ASSET_COMPONENTS = {
    'checker': ('assets.checker', 'assets/checker'),
    'makefile': ('assets.makefile', 'assets/makefile'),
    'teacher_file': ('assets.teacher_file', 'assets/teacher'),
    'scoring_script': ('assets.scoring_script', 'assets/scoring'),
    'local_service': ('assets.local_service', 'assets/local_service'),
    'resource_data': ('assets.resource_data', 'assets/resource_data'),
    'resource_data_teacher':
    ('assets.resource_data_teacher', 'assets/resource_data_teacher'),
    'network_dockerfile':
    ('assets.network_dockerfile', 'assets/network_dockerfile'),
    'public_testdata': ('trial.public_testdata', 'assets/trial'),
    'ac_code': ('trial.ac_code', 'assets/trial'),
}


def normalize_newlines(value):
    if isinstance(value, str):
        return value.replace('\r\n', '\n')
    if isinstance(value, list):
        return [normalize_newlines(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize_newlines(v) for k, v in value.items()}
    return value


def canonical_json_bytes(value: dict) -> bytes:
    normalized = normalize_newlines(value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    return payload.encode('utf-8')


def pretty_json_bytes(value: dict, indent: int = 2) -> bytes:
    normalized = normalize_newlines(value)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        indent=indent,
    )
    if not payload.endswith('\n'):
        payload = f"{payload}\n"
    return payload.encode('utf-8')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(file_obj, chunk_size: int = 1024 * 1024) -> Tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


def safe_zip_name(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute():
        return False
    if any(part == '..' for part in path.parts):
        return False
    return True


def validate_zip_info(
    info,
    max_file_size: int,
    max_ratio: float,
):
    if info.file_size > max_file_size:
        raise ValueError(f'File too large: {info.filename}')
    if info.compress_size == 0 and info.file_size > 0:
        raise ValueError(f'Invalid compression size: {info.filename}')
    if info.compress_size > 0:
        ratio = info.file_size / info.compress_size
        if ratio > max_ratio:
            raise ValueError(f'Compression ratio too high: {info.filename}')


def validate_zip_entries(
    zip_file,
    max_file_size: Optional[int] = None,
    max_total_size: Optional[int] = None,
    max_ratio: Optional[float] = None,
) -> List:
    max_file_size = max_file_size or (DEFAULT_MAX_FILE_SIZE_MB * 1024 * 1024)
    max_total_size = max_total_size or (DEFAULT_MAX_TOTAL_SIZE_MB * 1024 *
                                        1024)
    max_ratio = max_ratio or DEFAULT_MAX_COMPRESSION_RATIO

    total_size = 0
    entries = []
    for info in zip_file.infolist():
        if info.is_dir():
            continue
        if not safe_zip_name(info.filename):
            raise ValueError(f'Unsafe zip entry: {info.filename}')
        validate_zip_info(info, max_file_size, max_ratio)
        total_size += info.file_size
        if total_size > max_total_size:
            raise ValueError('Zip total size exceeds limit')
        entries.append(info)
    return entries


def _remove_path(data: dict, path: Tuple[str, ...]) -> bool:
    cur = data
    for key in path[:-1]:
        if not isinstance(cur, dict) or key not in cur:
            return False
        cur = cur[key]
    if isinstance(cur, dict) and path[-1] in cur:
        del cur[path[-1]]
        return True
    return False


def redact_meta(meta: dict) -> Tuple[dict, List[str]]:
    redacted = copy.deepcopy(meta)
    redactions = []
    for path in REDACTED_PATHS:
        if _remove_path(redacted, path):
            redactions.append('.'.join(path))
    return redacted, redactions


def strip_meta_for_hash(meta: dict) -> dict:
    stripped = copy.deepcopy(meta)
    for key in list(stripped.keys()):
        if key in HASH_EXCLUDED_FIELDS:
            del stripped[key]
    if isinstance(stripped.get('testCase'), dict):
        stripped['testCase'].pop('submissionMode', None)
    if isinstance(stripped.get('testCaseInfo'), dict):
        stripped['testCaseInfo'].pop('submissionMode', None)
    stripped, _ = redact_meta(stripped)
    return stripped


def strip_submission_mode(meta: dict):
    if isinstance(meta.get('testCase'), dict):
        meta['testCase'].pop('submissionMode', None)
    if isinstance(meta.get('testCaseInfo'), dict):
        meta['testCaseInfo'].pop('submissionMode', None)


def build_component_hash(component_id: str, files: Iterable[Dict[str,
                                                                 str]]) -> str:
    payload = {
        'id':
        component_id,
        'files':
        sorted(
            [{
                'path': f['path'],
                'sha256': f['sha256'],
                'size': f['size'],
            } for f in files],
            key=lambda x: x['path'],
        ),
    }
    return f"sha256:{sha256_bytes(canonical_json_bytes(payload))}"


def build_problem_content_hash(component_hashes: Dict[str, str]) -> str:
    payload = {
        'components':
        sorted(
            [{
                'id': component_id,
                'hash': component_hash,
            } for component_id, component_hash in component_hashes.items()],
            key=lambda x: x['id'],
        ),
    }
    return f"sha256:{sha256_bytes(canonical_json_bytes(payload))}"


def generate_import_id() -> str:
    return uuid.uuid4().hex
