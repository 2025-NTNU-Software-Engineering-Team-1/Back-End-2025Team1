import os

FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False') == 'True'
MINIO_HOST = os.getenv('MINIO_HOST')
MINIO_ACCESS_KEY = os.getenv('MINIO_ACCESS_KEY')
MINIO_SECRET_KEY = os.getenv('MINIO_SECRET_KEY')
MINIO_BUCKET = os.getenv('MINIO_BUCKET', 'normal-oj-testing')
# MinIO SSL setting: defaults to (not FLASK_DEBUG), can be overridden via MINIO_SECURE env var
_minio_secure_env = os.getenv('MINIO_SECURE')
MINIO_SECURE = _minio_secure_env.lower(
) == 'true' if _minio_secure_env else not FLASK_DEBUG
