"""
AI Vtuber Skin Management

This module handles AI Vtuber skin/avatar customization:
- AiVtuberSkin: Skin data storage and management
- UserSkinPreference: User's selected skin preference
"""
import zipfile
from datetime import datetime
from mongo import engine
from mongo.base import MongoBase
from mongo.utils import MinioClient

__all__ = [
    'AiVtuberSkin',
    'UserSkinPreference',
    'SKIN_UPLOAD_LIMITS',
    'MAX_SKIN_FILE_SIZE',
    'SKIN_MINIO_PREFIX',
]

# Upload limits by role
# User.Role: ADMIN=0, TEACHER=1, STUDENT=2, TA=3
Role = engine.User.Role
SKIN_UPLOAD_LIMITS = {
    Role.ADMIN: None,  # Admin: unlimited
    Role.TEACHER: None,  # Teacher: unlimited
    Role.STUDENT: 3,  # Student: 3 skins max
    Role.TA: None,  # TA: unlimited
}

MAX_SKIN_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
SKIN_MINIO_PREFIX = 'ai-vtuber-skins'


class AiVtuberSkin(MongoBase, engine=engine.AiVtuberSkin):
    """
    AI Vtuber skin wrapper class.
    Handles skin upload, retrieval, and deletion with upload limits.
    """

    def __init__(self, skin_id=None):
        if skin_id:
            self.obj = self.engine.objects(skin_id=skin_id).first()
        else:
            self.obj = None

    def __eq__(self, other):
        return super().__eq__(other)

    @classmethod
    def get_by_id(cls, skin_id: str):
        """Get skin by skin_id."""
        return cls.engine.objects(skin_id=skin_id).first()

    @classmethod
    def get_user_upload_count(cls, username: str) -> int:
        """Get the number of skins uploaded by a user."""
        try:
            user_doc = engine.User.objects(username=username).first()
            if not user_doc:
                return 0
            return cls.engine.objects(uploaded_by=user_doc,
                                      is_builtin=False).count()
        except Exception:
            return 0

    @classmethod
    def can_user_upload(cls, username: str, user_role: int) -> tuple:
        """
        Check if user can upload a new skin.
        Returns: (can_upload: bool, reason: str)
        """
        limit = SKIN_UPLOAD_LIMITS.get(user_role)
        if limit is None:
            return (True, "")

        current_count = cls.get_user_upload_count(username)
        if current_count >= limit:
            return (False, f"Upload limit reached ({limit} skins max)")
        return (True, "")

    @classmethod
    def get_available_skins(cls, username: str = None):
        """
        Get all skins available to a user.
        Includes: built-in skins, public skins, user's own skins
        """
        query = {
            '$or': [
                {
                    'isBuiltin': True
                },
                {
                    'isPublic': True
                },
            ]
        }

        if username:
            user_doc = engine.User.objects(username=username).first()
            if user_doc:
                query['$or'].append({'uploadedBy': user_doc.pk})

        skins = cls.engine.objects(__raw__=query)
        return list(skins)

    @classmethod
    def create_skin(cls,
                    skin_id: str,
                    name: str,
                    model_path: str,
                    model_json_name: str,
                    uploaded_by_username: str,
                    thumbnail_path: str = None,
                    is_builtin: bool = False,
                    is_public: bool = False,
                    file_size: int = 0,
                    emotion_mappings: dict = None):
        """Create a new skin entry."""
        user_doc = engine.User.objects(username=uploaded_by_username).first()
        if not user_doc:
            raise ValueError(f"User '{uploaded_by_username}' not found")

        skin = cls.engine(
            skin_id=skin_id,
            name=name,
            model_path=model_path,
            model_json_name=model_json_name,
            uploaded_by=user_doc,
            thumbnail_path=thumbnail_path,
            is_builtin=is_builtin,
            is_public=is_public,
            file_size=file_size,
            emotion_mappings=emotion_mappings or {},
            created_at=datetime.now(),
        )
        skin.save()
        return skin

    @classmethod
    def update_emotion_mappings(cls, skin_id: str, mappings: dict) -> bool:
        """
        Update emotion mappings for a skin.
        mappings: dict with keys like 'smile', 'unhappy', 'tired', 'surprised'
                  and values as expression IDs (e.g., 'F05') or None
        """
        skin = cls.get_by_id(skin_id)
        if not skin:
            return False

        # Validate keys - only allow known emotion types
        valid_emotions = {'smile', 'unhappy', 'tired', 'surprised'}
        cleaned = {k: v for k, v in mappings.items() if k in valid_emotions}

        skin.emotion_mappings = cleaned
        skin.save()
        return True

    # ==========================================================================
    # MinIO Operations
    # ==========================================================================

    @staticmethod
    def validate_live2d_zip(file_obj) -> tuple:
        """
        Validate that the uploaded ZIP contains a valid Live2D model.
        Returns: (is_valid: bool, result: dict or error_message: str)
        
        result dict contains:
        - model_json_name: str
        - emotion_mappings: dict (auto-detected from Expressions)
        """
        try:
            import json
            file_obj.seek(0)

            # macOS zip 檢測
            from model.utils.file import zip_sanitize
            zip_bytes = file_obj.read()
            is_valid, sanitize_error = zip_sanitize(zip_bytes)
            if not is_valid:
                return (False, sanitize_error)
            file_obj.seek(0)

            with zipfile.ZipFile(file_obj, 'r') as zf:
                names = zf.namelist()
                model_files = [n for n in names if n.endswith('.model3.json')]
                if not model_files:
                    return (False, "ZIP must contain a .model3.json file")

                model_json_path = model_files[0]
                model_json_name = model_json_path.split(
                    '/')[-1] if '/' in model_json_path else model_json_path

                # Try to parse model3.json to extract Expressions
                emotion_mappings = {}
                try:
                    model_content = zf.read(model_json_path).decode('utf-8')
                    model_data = json.loads(model_content)

                    # Look for Expressions in FileReferences
                    expressions = model_data.get('FileReferences',
                                                 {}).get('Expressions', [])

                    if expressions:
                        # Try to auto-map emotions based on expression names
                        for exp in expressions:
                            exp_name = exp.get('Name', '')
                            exp_file = exp.get('File', '')

                            # Common patterns for emotion mapping
                            name_lower = exp_name.lower()
                            file_lower = exp_file.lower()

                            # Smile patterns
                            if any(p in name_lower or p in file_lower
                                   for p in ['smile', 'happy', 'joy', 'f05']):
                                if 'smile' not in emotion_mappings:
                                    emotion_mappings['smile'] = exp_name
                            # Unhappy patterns
                            elif any(p in name_lower or p in file_lower for p
                                     in ['unhappy', 'sad', 'angry', 'f03']):
                                if 'unhappy' not in emotion_mappings:
                                    emotion_mappings['unhappy'] = exp_name
                            # Tired patterns
                            elif any(p in name_lower or p in file_lower for p
                                     in ['tired', 'sleepy', 'bored', 'f08']):
                                if 'tired' not in emotion_mappings:
                                    emotion_mappings['tired'] = exp_name
                            # Surprised patterns
                            elif any(p in name_lower or p in file_lower
                                     for p in ['surprise', 'shock', 'f06']):
                                if 'surprised' not in emotion_mappings:
                                    emotion_mappings['surprised'] = exp_name
                except Exception:
                    # If parsing fails, just proceed without mappings
                    pass

                return (True, {
                    'model_json_name': model_json_name,
                    'emotion_mappings': emotion_mappings,
                })
        except zipfile.BadZipFile:
            return (False, "Invalid ZIP file")
        except Exception as e:
            return (False, f"Validation error: {str(e)}")
        finally:
            file_obj.seek(0)

    @classmethod
    def upload_skin_file(cls, skin_id: str, file_obj, file_size: int) -> tuple:
        """
        Extract ZIP and upload each file to MinIO.
        Returns: (base_path: str, first_texture_path: str or None)
        """
        import io
        import logging
        logger = logging.getLogger(__name__)

        minio_client = MinioClient()
        base_path = f"{SKIN_MINIO_PREFIX}/user-uploaded/{skin_id}"
        first_texture = None

        file_obj.seek(0)
        with zipfile.ZipFile(file_obj, 'r') as zf:
            logger.info(f"[Skin Upload] ZIP contents: {zf.namelist()}")

            for name in zf.namelist():
                # Skip directories
                if name.endswith('/'):
                    continue

                # Read file content
                content = zf.read(name)

                # Determine content type
                content_type = 'application/octet-stream'
                if name.endswith('.json'):
                    content_type = 'application/json'
                elif name.endswith('.png'):
                    content_type = 'image/png'
                    # Capture first texture as thumbnail
                    if first_texture is None:
                        first_texture = name
                elif name.endswith('.moc3'):
                    content_type = 'application/octet-stream'

                # Keep the original path structure from ZIP
                minio_path = f"{base_path}/{name}"

                logger.info(f"[Skin Upload] Uploading: {name} -> {minio_path}")

                # Upload to MinIO
                minio_client.upload_file_object(io.BytesIO(content),
                                                minio_path,
                                                len(content),
                                                content_type=content_type)

        # Return the base path and first texture for thumbnail
        return (f"{base_path}/", first_texture)

    @classmethod
    def download_skin_file(cls, skin_id: str) -> bytes:
        """Download skin ZIP from MinIO."""
        skin = cls.get_by_id(skin_id)
        if not skin:
            raise ValueError("Skin not found")

        minio_client = MinioClient()
        return minio_client.download_file(skin.model_path)

    @classmethod
    def delete_skin_file(cls, minio_path: str) -> bool:
        """Delete all skin files from MinIO (entire directory)."""
        try:
            minio_client = MinioClient()
            # minio_path is like "ai-vtuber-skins/user-uploaded/{skin_id}/"
            # List and delete all objects with this prefix
            prefix = minio_path.rstrip('/')
            objects = minio_client.client.list_objects(minio_client.bucket,
                                                       prefix=prefix,
                                                       recursive=True)
            for obj in objects:
                minio_client.client.remove_object(minio_client.bucket,
                                                  obj.object_name)
            return True
        except Exception:
            return False

    @classmethod
    def delete_skin(cls, skin_id: str, requesting_username: str) -> tuple:
        """
        Delete a skin. Only uploader, teachers, or admins can delete.
        Returns: (success: bool, error_message: str)
        """
        skin = cls.get_by_id(skin_id)
        if not skin:
            return (False, "Skin not found")

        if skin.is_builtin:
            return (False, "Cannot delete built-in skin")

        user_doc = engine.User.objects(username=requesting_username).first()
        if not user_doc:
            return (False, "User not found")

        # Only students (role=2) are restricted to their own skins
        # Admins (0), Teachers (1), and TAs (3) can delete any skin
        if user_doc.role == Role.STUDENT:  # STUDENT
            if skin.uploaded_by.username != requesting_username:
                return (False, "Permission denied")

        skin.delete()
        return (True, "")

    @classmethod
    def get_storage_stats(cls):
        """Get storage statistics for admin dashboard."""
        pipeline = [{
            '$group': {
                '_id': '$uploadedBy',
                'total_size': {
                    '$sum': '$fileSize'
                },
                'count': {
                    '$sum': 1
                }
            }
        }]
        stats = list(cls.engine.objects.aggregate(*pipeline))

        total_size = sum(s.get('total_size', 0) for s in stats)
        total_count = sum(s.get('count', 0) for s in stats)

        user_breakdown = []
        for stat in stats:
            user_id = stat.get('_id')
            if user_id:
                user = engine.User.objects(pk=user_id).first()
                username = user.username if user else str(user_id)
            else:
                username = "Unknown"

            user_breakdown.append({
                'username': username,
                'size': stat.get('total_size', 0),
                'count': stat.get('count', 0),
            })

        return {
            'total_size': total_size,
            'total_count': total_count,
            'per_user': user_breakdown,
        }


class UserSkinPreference(MongoBase, engine=engine.UserSkinPreference):
    """User's AI Vtuber skin preference wrapper."""

    def __init__(self, username=None):
        if username:
            user_doc = engine.User.objects(username=username).first()
            if user_doc:
                self.obj = self.engine.objects(user=user_doc).first()
            else:
                self.obj = None
        else:
            self.obj = None

    def __eq__(self, other):
        return super().__eq__(other)

    @classmethod
    def get_preference(cls, username: str) -> str:
        """Get user's selected skin ID. Returns 'builtin_hiyori' if not set."""
        user_doc = engine.User.objects(username=username).first()
        if not user_doc:
            return 'builtin_hiyori'

        pref = cls.engine.objects(user=user_doc).first()
        if not pref:
            return 'builtin_hiyori'

        return pref.selected_skin_id

    @classmethod
    def set_preference(cls, username: str, skin_id: str) -> bool:
        """Set user's skin preference. Creates if doesn't exist."""
        user_doc = engine.User.objects(username=username).first()
        if not user_doc:
            return False

        # Verify skin exists (skip for builtin)
        if not skin_id.startswith('builtin_'):
            skin = AiVtuberSkin.get_by_id(skin_id)
            if not skin:
                return False

        pref = cls.engine.objects(user=user_doc).first()
        if pref:
            pref.update(set__selected_skin_id=skin_id,
                        set__updated_at=datetime.now())
        else:
            new_pref = cls.engine(
                user=user_doc,
                selected_skin_id=skin_id,
                updated_at=datetime.now(),
            )
            new_pref.save()

        return True
