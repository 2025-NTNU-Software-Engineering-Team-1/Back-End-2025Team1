"""
AI Vtuber Skin API Routes

Handles skin upload, retrieval, deletion, and user preferences.
"""
import io
from flask import Blueprint, request, send_file, current_app
from ulid import ULID

from mongo import User
from mongo.ai import (
    AiVtuberSkin,
    UserSkinPreference,
    MAX_SKIN_FILE_SIZE,
)

from model.auth import login_required
from model.utils import Request, HTTPError, HTTPResponse

__all__ = ['skin_api']

skin_api = Blueprint('skin_api', __name__)


@skin_api.route('/skins', methods=['GET'])
@login_required
def list_skins(user):
    """Get all available skins for the current user."""
    try:
        current_app.logger.info(
            f"[Skin List] User {user.username} listing skins")
        skins = AiVtuberSkin.get_available_skins(user.username)
        current_app.logger.info(
            f"[Skin List] Found {len(skins)} skins from DB")

        result = [{
            'skin_id': 'builtin_hiyori',
            'name': 'Hiyori (預設)',
            'thumbnail_path': '/live2d/hiyori_avatar.png',
            'is_builtin': True,
            'is_public': True,
            'uploaded_by': 'System',
            'file_size': 0,
        }]

        for skin in skins:
            current_app.logger.debug(
                f"[Skin List] Processing skin: {skin.skin_id}, {skin.name}")
            result.append({
                'skin_id':
                skin.skin_id,
                'name':
                skin.name,
                'thumbnail_path':
                skin.thumbnail_path,
                'is_builtin':
                skin.is_builtin,
                'is_public':
                skin.is_public,
                'uploaded_by':
                skin.uploaded_by.username if skin.uploaded_by else None,
                'file_size':
                skin.file_size,
            })

        current_app.logger.info(
            f"[Skin List] Returning {len(result)} skins total")
        return HTTPResponse(data=result)
    except Exception as e:
        current_app.logger.error(f"[Skin List] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins', methods=['POST'])
@login_required
def upload_skin(user):
    """Upload a new skin."""
    try:
        current_app.logger.info(
            f"[Skin Upload] User {user.username} attempting skin upload")

        # Check upload permission
        can_upload, reason = AiVtuberSkin.can_user_upload(
            user.username, user.role)
        if not can_upload:
            current_app.logger.warning(
                f"[Skin Upload] Permission denied for {user.username}: {reason}"
            )
            return HTTPError(reason, 403)

        if 'file' not in request.files:
            current_app.logger.warning(
                f"[Skin Upload] No file in request from {user.username}")
            return HTTPError('No file provided', 400)

        file = request.files['file']
        if not file.filename:
            current_app.logger.warning(
                f"[Skin Upload] Empty filename from {user.username}")
            return HTTPError('Empty filename', 400)

        current_app.logger.info(
            f"[Skin Upload] Received file: {file.filename}")

        # Check file size
        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)

        current_app.logger.info(f"[Skin Upload] File size: {file_size} bytes")

        if file_size > MAX_SKIN_FILE_SIZE:
            current_app.logger.warning(
                f"[Skin Upload] File too large: {file_size} > {MAX_SKIN_FILE_SIZE}"
            )
            return HTTPError(
                f'File too large. Max: {MAX_SKIN_FILE_SIZE // (1024*1024)} MB',
                400)

        # Validate ZIP (use mongo layer method)
        is_valid, result = AiVtuberSkin.validate_live2d_zip(file)
        if not is_valid:
            current_app.logger.warning(
                f"[Skin Upload] ZIP validation failed: {result}")
            return HTTPError(result, 400)

        # result is now a dict with model_json_name and emotion_mappings
        model_json_name = result['model_json_name']
        auto_emotion_mappings = result.get('emotion_mappings', {})

        # User-provided mappings override auto-detected ones
        user_emotion_mappings_str = request.form.get('emotion_mappings',
                                                     '').strip()
        if user_emotion_mappings_str:
            try:
                import json
                user_emotion_mappings = json.loads(user_emotion_mappings_str)
                if isinstance(user_emotion_mappings, dict):
                    emotion_mappings = user_emotion_mappings
                else:
                    emotion_mappings = auto_emotion_mappings
            except json.JSONDecodeError:
                current_app.logger.warning(
                    f"[Skin Upload] Invalid emotion_mappings JSON: {user_emotion_mappings_str}"
                )
                emotion_mappings = auto_emotion_mappings
        else:
            emotion_mappings = auto_emotion_mappings

        name = request.form.get('name', '').strip() or file.filename.rsplit(
            '.', 1)[0]
        skin_id = str(ULID())

        current_app.logger.info(
            f"[Skin Upload] Creating skin: id={skin_id}, name={name}, model_json={model_json_name}, emotions={emotion_mappings}"
        )

        # Upload to MinIO (use mongo layer method)
        minio_path, first_texture = AiVtuberSkin.upload_skin_file(
            skin_id, file, file_size)
        current_app.logger.info(
            f"[Skin Upload] Uploaded to MinIO: {minio_path}")

        # Handle user-uploaded thumbnail
        thumbnail_path = None
        thumbnail_file = request.files.get('thumbnail')
        if thumbnail_file and thumbnail_file.filename:
            # Upload thumbnail to MinIO
            from mongo.utils import MinioClient
            from mongo.ai import SKIN_MINIO_PREFIX

            thumb_ext = thumbnail_file.filename.rsplit(
                '.', 1)[-1] if '.' in thumbnail_file.filename else 'png'
            thumb_minio_path = f"{SKIN_MINIO_PREFIX}/user-uploaded/{skin_id}/thumbnail.{thumb_ext}"

            thumb_data = thumbnail_file.read()
            minio_client = MinioClient()
            minio_client.upload_file_object(
                io.BytesIO(thumb_data),
                thumb_minio_path,
                len(thumb_data),
                content_type=thumbnail_file.content_type or 'image/png')
            thumbnail_path = f"/api/ai/skins/{skin_id}/assets/thumbnail.{thumb_ext}"
            current_app.logger.info(
                f"[Skin Upload] User thumbnail uploaded: {thumbnail_path}")

        # Save to database with auto-detected emotion mappings
        AiVtuberSkin.create_skin(
            skin_id=skin_id,
            name=name,
            model_path=minio_path,
            model_json_name=model_json_name,
            uploaded_by_username=user.username,
            thumbnail_path=thumbnail_path,
            is_builtin=False,
            is_public=False,
            file_size=file_size,
            emotion_mappings=emotion_mappings,
        )
        current_app.logger.info(f"[Skin Upload] Saved to database: {skin_id}")

        return HTTPResponse('Skin uploaded successfully',
                            status_code=201,
                            data={
                                'skin_id': skin_id,
                                'name': name
                            })

    except Exception as e:
        current_app.logger.error(f"[Skin Upload] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>', methods=['GET'])
@login_required
def get_skin(user, skin_id):
    """Get skin details."""
    try:
        if skin_id == 'builtin_hiyori':
            return HTTPResponse(
                data={
                    'skin_id': 'builtin_hiyori',
                    'name': 'Hiyori (預設)',
                    'model_path': '/live2d/hiyori_pro_zh/runtime/',
                    'model_json_name': 'hiyori_pro_t11.model3.json',
                    'thumbnail_path': '/live2d/hiyori_avatar.png',
                    'is_builtin': True,
                    'emotion_mappings': {
                        'smile': 'F05',
                        'unhappy': 'F03',
                        'tired': 'F08',
                        'surprised': 'F06',
                    },
                })

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        # For user-uploaded skins, construct API path
        # Live2D will load from: /api/ai/skins/{skin_id}/assets/{filename}
        api_model_path = f"/api/ai/skins/{skin_id}/assets/"

        return HTTPResponse(
            data={
                'skin_id': skin.skin_id,
                'name': skin.name,
                'model_path': api_model_path,
                'model_json_name': skin.model_json_name,
                'thumbnail_path': skin.thumbnail_path,
                'is_builtin': skin.is_builtin,
                'is_public': skin.is_public,
                'file_size': skin.file_size,
                'emotion_mappings': skin.emotion_mappings or {},
            })
    except Exception as e:
        current_app.logger.error(f"[Skin Get] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>', methods=['PUT'])
@login_required
def update_skin(user, skin_id):
    """Update skin metadata (name, thumbnail, emotion_mappings)."""
    try:
        if skin_id.startswith('builtin_'):
            return HTTPError('Cannot modify built-in skin', 400)

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        # Only uploader, teachers, or admins can modify
        if user.role > 1 and skin.uploaded_by.username != user.username:
            return HTTPError('Permission denied', 403)

        # Update name if provided
        new_name = request.form.get('name', '').strip()
        if new_name:
            skin.name = new_name

        # Update emotion_mappings if provided
        emotion_mappings_str = request.form.get('emotion_mappings', '').strip()
        if emotion_mappings_str:
            try:
                import json
                emotion_mappings = json.loads(emotion_mappings_str)
                if isinstance(emotion_mappings, dict):
                    valid_emotions = {'smile', 'unhappy', 'tired', 'surprised'}
                    skin.emotion_mappings = {
                        k: v
                        for k, v in emotion_mappings.items()
                        if k in valid_emotions
                    }
            except json.JSONDecodeError:
                pass

        # Handle thumbnail upload
        thumbnail_file = request.files.get('thumbnail')
        if thumbnail_file and thumbnail_file.filename:
            from mongo.utils import MinioClient
            from mongo.ai import SKIN_MINIO_PREFIX

            thumb_ext = thumbnail_file.filename.rsplit(
                '.', 1)[-1] if '.' in thumbnail_file.filename else 'png'
            thumb_minio_path = f"{SKIN_MINIO_PREFIX}/user-uploaded/{skin_id}/thumbnail.{thumb_ext}"

            thumb_data = thumbnail_file.read()
            minio_client = MinioClient()
            minio_client.upload_file_object(
                io.BytesIO(thumb_data),
                thumb_minio_path,
                len(thumb_data),
                content_type=thumbnail_file.content_type or 'image/png')
            skin.thumbnail_path = f"/api/ai/skins/{skin_id}/assets/thumbnail.{thumb_ext}"

        skin.save()
        current_app.logger.info(f"[Skin Update] Updated skin {skin_id}")
        return HTTPResponse('Skin updated successfully')
    except Exception as e:
        current_app.logger.error(f"[Skin Update] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>/visibility', methods=['PATCH'])
@login_required
@Request.json('is_public: bool')
def update_skin_visibility(user, skin_id, is_public):
    """Toggle skin public/private visibility. Only teachers and admins can use this."""
    try:
        # Only teachers (role <= 1) and admins can change visibility
        if user.role > 1:
            return HTTPError(
                'Permission denied. Only teachers and admins can change visibility.',
                403)

        if skin_id.startswith('builtin_'):
            return HTTPError('Cannot modify built-in skin', 400)

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        skin.is_public = is_public
        skin.save()

        status = "public" if is_public else "private"
        current_app.logger.info(
            f"[Skin Visibility] Set {skin_id} to {status} by {user.username}")
        return HTTPResponse(f'Skin is now {status}')
    except Exception as e:
        current_app.logger.error(f"[Skin Visibility] Error: {e}",
                                 exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>/emotions', methods=['PUT'])
@login_required
@Request.json('mappings: dict')
def update_skin_emotions(user, skin_id, mappings):
    """Update emotion mappings for a skin."""
    try:
        if skin_id.startswith('builtin_'):
            return HTTPError('Cannot modify built-in skin', 400)

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        # Only uploader, teachers, or admins can modify
        if user.role > 1 and skin.uploaded_by.username != user.username:
            return HTTPError('Permission denied', 403)

        success = AiVtuberSkin.update_emotion_mappings(skin_id, mappings)
        if not success:
            return HTTPError('Failed to update mappings', 500)

        return HTTPResponse('Emotion mappings updated')
    except Exception as e:
        current_app.logger.error(f"[Skin Emotions] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>', methods=['DELETE'])
@login_required
def delete_skin(user, skin_id):
    """Delete a skin."""
    try:
        current_app.logger.info(
            f"[Skin Delete] User {user.username} deleting skin {skin_id}")

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        minio_path = skin.model_path

        success, error = AiVtuberSkin.delete_skin(skin_id, user.username)
        if not success:
            return HTTPError(error, 403)

        # Delete from MinIO (use mongo layer method)
        AiVtuberSkin.delete_skin_file(minio_path)
        current_app.logger.info(f"[Skin Delete] Deleted skin {skin_id}")

        return HTTPResponse('Skin deleted successfully')
    except Exception as e:
        current_app.logger.error(f"[Skin Delete] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>/download', methods=['GET'])
@login_required
def download_skin(user, skin_id):
    """Download skin ZIP file."""
    try:
        if skin_id.startswith('builtin_'):
            return HTTPError('Cannot download built-in skin', 400)

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        # Download from MinIO (use mongo layer method)
        data = AiVtuberSkin.download_skin_file(skin_id)

        return send_file(io.BytesIO(data),
                         mimetype='application/zip',
                         as_attachment=True,
                         download_name=f'{skin.name}.zip')
    except Exception as e:
        current_app.logger.error(f"[Skin Download] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/skins/<skin_id>/assets/<path:filename>', methods=['GET'])
@login_required
def get_skin_asset(user, skin_id, filename):
    """Proxy skin assets from MinIO for Live2D to load."""
    try:
        from mongo.utils import MinioClient
        from mongo.ai import SKIN_MINIO_PREFIX

        if skin_id.startswith('builtin_'):
            return HTTPError('Use static path for built-in skins', 400)

        skin = AiVtuberSkin.get_by_id(skin_id)
        if not skin:
            return HTTPError('Skin not found', 404)

        # Construct MinIO path
        minio_path = f"{SKIN_MINIO_PREFIX}/user-uploaded/{skin_id}/{filename}"

        # Download from MinIO
        minio_client = MinioClient()
        data = minio_client.download_file(minio_path)

        # Determine content type
        content_type = 'application/octet-stream'
        if filename.endswith('.json'):
            content_type = 'application/json'
        elif filename.endswith('.png'):
            content_type = 'image/png'
        elif filename.endswith('.moc3'):
            content_type = 'application/octet-stream'

        return send_file(
            io.BytesIO(data),
            mimetype=content_type,
        )
    except Exception as e:
        current_app.logger.error(f"[Skin Asset] Error loading {filename}: {e}")
        return HTTPError(str(e), 404)


# =============================================================================
# User Preference Endpoints
# =============================================================================


@skin_api.route('/user-preference', methods=['GET'])
@login_required
def get_user_preference(user):
    """Get user's skin preference."""
    try:
        skin_id = UserSkinPreference.get_preference(user.username)
        return HTTPResponse(data={'selected_skin_id': skin_id})
    except Exception as e:
        current_app.logger.error(f"[Skin Preference Get] Error: {e}",
                                 exc_info=True)
        return HTTPError(str(e), 500)


@skin_api.route('/user-preference', methods=['PUT'])
@login_required
@Request.json('skin_id: str')
def set_user_preference(user, skin_id):
    """Set user's skin preference."""
    try:
        success = UserSkinPreference.set_preference(user.username, skin_id)
        if not success:
            return HTTPError('Failed to set preference. Skin may not exist.',
                             400)
        return HTTPResponse('Preference updated')
    except Exception as e:
        current_app.logger.error(f"[Skin Preference Set] Error: {e}",
                                 exc_info=True)
        return HTTPError(str(e), 500)


# =============================================================================
# Admin Endpoints
# =============================================================================


@skin_api.route('/storage-stats', methods=['GET'])
@login_required
def get_storage_stats(user):
    """Get storage statistics (admin only)."""
    try:
        if user.role != User.Role.ADMIN:
            return HTTPError('Permission denied', 403)

        stats = AiVtuberSkin.get_storage_stats()
        return HTTPResponse(data=stats)
    except Exception as e:
        current_app.logger.error(f"[Storage Stats] Error: {e}", exc_info=True)
        return HTTPError(str(e), 500)
