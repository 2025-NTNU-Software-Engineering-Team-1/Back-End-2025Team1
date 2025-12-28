"""
Default Skin Initialization

This module handles uploading the built-in default Live2D skin to MinIO
on first startup. This allows the frontend to be smaller by not bundling
the default model files.
"""
import io
import os
import zipfile
import logging
from pathlib import Path

from mongo.utils import MinioClient
from mongo.ai.skins import SKIN_MINIO_PREFIX

__all__ = ['ensure_default_skin_uploaded']

logger = logging.getLogger(__name__)

# Path to the default skin assets
ASSETS_DIR = Path(__file__).parent.parent.parent / 'assets' / 'default_skin'
DEFAULT_SKIN_ZIP = ASSETS_DIR / 'hiyori_pro_zh.zip'
DEFAULT_AVATAR = ASSETS_DIR / 'hiyori_avatar.png'

# MinIO path for the built-in skin
BUILTIN_SKIN_PREFIX = f'{SKIN_MINIO_PREFIX}/builtin/hiyori_pro_zh'


def _check_skin_exists(minio_client: MinioClient) -> bool:
    """Check if the default skin already exists in MinIO."""
    try:
        # Try to list objects with the prefix
        objects = list(
            minio_client.client.list_objects(minio_client.bucket,
                                             prefix=f'{BUILTIN_SKIN_PREFIX}/',
                                             max_keys=1))
        return len(objects) > 0
    except Exception as e:
        logger.warning(f"[DefaultSkin] Error checking skin existence: {e}")
        return False


def _upload_zip_contents(minio_client: MinioClient, zip_path: Path) -> bool:
    """Extract and upload ZIP contents to MinIO."""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for name in zf.namelist():
                # Skip directories
                if name.endswith('/'):
                    continue

                content = zf.read(name)

                # Determine content type
                content_type = 'application/octet-stream'
                if name.endswith('.json'):
                    content_type = 'application/json'
                elif name.endswith('.png'):
                    content_type = 'image/png'
                elif name.endswith('.moc3'):
                    content_type = 'application/octet-stream'
                elif name.endswith('.exp3.json'):
                    content_type = 'application/json'
                elif name.endswith('.motion3.json'):
                    content_type = 'application/json'

                # Upload to MinIO
                minio_path = f'{BUILTIN_SKIN_PREFIX}/{name}'
                minio_client.upload_file_object(io.BytesIO(content),
                                                minio_path,
                                                len(content),
                                                content_type=content_type)
                # logger.debug(f"[DefaultSkin] Uploaded: {name}") (Removed to reduce noise)

        return True
    except Exception as e:
        logger.error(f"[DefaultSkin] Error uploading ZIP contents: {e}")
        return False


def _upload_avatar(minio_client: MinioClient, avatar_path: Path) -> bool:
    """Upload the avatar thumbnail image."""
    try:
        if not avatar_path.exists():
            logger.warning(f"[DefaultSkin] Avatar not found: {avatar_path}")
            return False

        with open(avatar_path, 'rb') as f:
            content = f.read()

        minio_path = f'{BUILTIN_SKIN_PREFIX}/hiyori_avatar.png'
        minio_client.upload_file_object(io.BytesIO(content),
                                        minio_path,
                                        len(content),
                                        content_type='image/png')
        # logger.debug(f"[DefaultSkin] Uploaded avatar: {minio_path}") (Removed to reduce noise)
        return True
    except Exception as e:
        logger.error(f"[DefaultSkin] Error uploading avatar: {e}")
        return False


def ensure_default_skin_uploaded() -> bool:
    """
    Ensure the default Live2D skin is uploaded to MinIO.

    This function is idempotent - it will only upload if the skin
    doesn't already exist in MinIO.

    Returns:
        True if skin exists or was uploaded successfully, False otherwise.
    """
    try:
        minio_client = MinioClient()
    except ValueError as e:
        logger.warning(f"[DefaultSkin] MinIO not configured: {e}")
        return False

    # Check if already uploaded
    if _check_skin_exists(minio_client):
        logger.info("[DefaultSkin] Default skin already exists in MinIO")
        return True

    # Check if ZIP file exists
    if not DEFAULT_SKIN_ZIP.exists():
        logger.warning(
            f"[DefaultSkin] Default skin ZIP not found: {DEFAULT_SKIN_ZIP}")
        return False

    logger.info("[DefaultSkin] Uploading default skin to MinIO...")

    # Upload ZIP contents
    if not _upload_zip_contents(minio_client, DEFAULT_SKIN_ZIP):
        return False

    # Upload avatar
    _upload_avatar(minio_client, DEFAULT_AVATAR)

    logger.info("[DefaultSkin] Default skin uploaded successfully")
    return True
