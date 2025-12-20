from datetime import datetime
from . import engine
from .base import MongoBase
from .course import Course

__all__ = [
    'AiModel',
    'AiApiKey',
    'AiApiLog',
    'AiTokenUsage',
]


class AiModel(MongoBase, engine=engine.AiModel):
    """
    AI Model configuration document.
    """

    def __init__(self, name):
        self.obj = self.engine.objects(name=name).first()

    @property
    def rpm_limit(self):
        if self.obj: return self.obj.rpm_limit
        return 5

    @classmethod
    def get_by_name(cls, name: str):
        """Get AI model by name"""
        return engine.AiModel.objects(name=name).first()

    @classmethod
    def get_active(cls):
        """Get all active AI models"""
        return engine.AiModel.objects(is_active=True)

    @classmethod
    def initialize_default_models(cls):
        """
        Initialize or update default AI models.
        """
        defaults = [{
            'name': 'gemini-2.5-flash',
            'rpm_limit': 5,
            'tpm_limit': 250000,
            'rpd_limit': 20,
            'description': 'Google Gemini 2.5 Flash',
            'is_active': True
        }, {
            'name': 'gemini-2.5-flash-lite',
            'rpm_limit': 10,
            'tpm_limit': 250000,
            'rpd_limit': 20,
            'description': 'Google Gemini 2.5 Flash Lite',
            'is_active': True
        }]

        for config in defaults:
            try:
                model = engine.AiModel.objects(name=config['name']).first()
                if not model:
                    engine.AiModel(**config).save()
                else:
                    model.update(**config)
            except Exception as e:
                print(f"Failed to init model {config['name']}: {e}")

    @classmethod
    def get_rpm_limit(cls, name: str, default: int = 5):
        """Get RPM limit for a specific model"""
        # If not found use default (Gemini Flash free tier usually 5 RPM)
        # But it is highly possible that Google changes their limits
        try:
            model = cls.get_by_name(name)
            return model.rpm_limit
        except Exception:
            return default


class AiApiKey(MongoBase, engine=engine.AiApiKey):
    """
    AI API Key management document.
    course_name 是 ReferenceField('Course')，需要傳入 Course 的 engine document
    """

    def __init__(self, key_id):
        self.obj = self.engine.objects(id=key_id).first()

    @classmethod
    def get_active_keys_by_course_name(cls, course_name: str):
        """Get all active API keys for a specific course by course_name (string)"""
        try:
            # 先取得 Course engine document
            course_obj = engine.Course.objects(course_name=course_name).first()
            if not course_obj:
                return []

            # 用 Course document reference 查詢 keys
            keys = cls.engine.objects(course_name=course_obj, is_active=True)

            wrappers = []
            for k in keys:
                # 建立 Wrapper，並手動注入 obj 以避免重新查詢
                wrapper = cls(k.id)
                wrapper.obj = k

                # Check for RPD reset
                wrapper.check_reset()

                wrappers.append(wrapper)
            # Fix: The original code had an indentation error (return inside loop)
            # Assuming we want to return ALL wrappers
            return wrappers
        except Exception:
            return []

    def check_reset(self):
        """
        Check if RPD needs to be reset based on RPD_RESET_INTERVAL.
        """
        if not self.obj:
            return

        now = datetime.now()
        last_reset = self.obj.last_reset_date

        # Lazy check using memory first
        if now >= last_reset + engine.RPD_RESET_INTERVAL:
            try:
                self.obj.update(set__rpd=0, set__last_reset_date=now)
                self.obj.reload()
            except Exception:
                pass

    def increment_usage(self, input_tokens: int, output_tokens: int):
        """Increment key usage counters"""
        if self.obj:
            self.obj.update(inc__request_count=1,
                            inc__rpd=1,
                            inc__input_token=input_tokens,
                            inc__output_token=output_tokens,
                            set__updated_at=datetime.now())
            self.obj.reload()

    @classmethod
    def get_keys_usage_by_course(cls, course_name: str):
        course_obj = engine.Course.objects(course_name=course_name).first()
        if not course_obj:
            return []

        keys = cls.engine.objects(course_name=course_obj)

        result_list = []

        for key in keys:
            pipeline = [{
                '$match': {
                    'apiKey': key.id,
                    'courseName': course_obj.id
                }
            }, {
                '$group': {
                    '_id': '$problemId',
                    'totalInput': {
                        '$sum': '$input_tokens'
                    },
                    'totalOutput': {
                        '$sum': '$output_tokens'
                    }
                }
            }]

            usage_stats = list(
                engine.AiTokenUsage.objects.aggregate(*pipeline))

            problem_usages = []
            for stat in usage_stats:
                p_id = stat['_id']
                total = stat.get('totalInput', 0) + stat.get('totalOutput', 0)
                if p_id:

                    prob = engine.Problem.objects(
                        pk=p_id).only('problem_name').first()
                    p_name = prob.problem_name if prob else f"Problem {p_id}"

                    problem_usages.append({
                        "problem_id": str(p_id),
                        "problem_name": p_name,
                        "total_token": total
                    })

            # Masked Value of API Key
            raw_key = key.key_value or ""
            masked = f"{raw_key[:4]}****{raw_key[-4:]}" if len(
                raw_key) > 8 else "****"

            result_list.append({
                "id":
                str(key.id),
                "key_name":
                key.key_name,
                "masked_value":
                masked,
                "is_active":
                key.is_active,
                "input_token":
                key.input_token,
                "output_token":
                key.output_token,
                "request_count":
                key.request_count,
                "created_by":
                key.created_by.username if key.created_by else "System",
                "problem_usages":
                problem_usages
            })

        return result_list

    @classmethod
    def get_list_by_course(cls, course_name: str):
        """
        To get all API Keys for a specific course by course_name (string)
        """
        course_obj = engine.Course.objects(course_name=course_name).first()
        if not course_obj:
            return []

        keys = cls.engine.objects(course_name=course_obj)
        result_list = []

        for key in keys:
            # Mask Key Value
            raw_key = key.key_value or ""
            masked = f"{raw_key[:4]}****{raw_key[-4:]}" if len(
                raw_key) > 8 else "****"

            result_list.append({
                "id":
                str(key.id),
                "key_name":
                key.key_name,
                "masked_value":
                masked,
                "is_active":
                key.is_active,
                "input_token":
                key.input_token,
                "output_token":
                key.output_token,
                "request_count":
                key.request_count,
                # If created_by is None, show "System"
                "created_by":
                key.created_by.username if key.created_by else "System"
            })

        return result_list

    @classmethod
    def add_key(cls,
                course_id,
                key_name,
                key_value,
                created_by,
                is_active=True):
        """
        [新增] 建立新的 API Key
        """
        # 修正查詢條件，使用 course_name=course_id
        # The argument name 'course_id' in add_key is an ObjectId.
        # 'course_name' field in AiApiKey is ReferenceField('Course').
        # So we should find the course by ID first.
        real_course_doc = engine.Course.objects(id=course_id).first()
        if not real_course_doc:
            raise ValueError("Course not found")

        if cls.engine.objects(course_name=real_course_doc,
                              key_name=key_name).first():
            raise ValueError(
                f"Key name '{key_name}' already exists in this course.")

        # Unwrap created_by if it is a wrapper (MongoBase)
        real_created_by = created_by.obj if hasattr(created_by,
                                                    'obj') else created_by

        new_key = cls.engine(
            course_name=real_course_doc,  # 對應 ReferenceField
            key_name=key_name,
            key_value=key_value,
            created_by=real_created_by,
            is_active=is_active,
            created_at=datetime.now(),
            updated_at=datetime.now())
        new_key.save()
        return new_key

    @classmethod
    def update_key(cls, key_id, **kwargs):
        """
        [新增] 更新 Key (支援改名、狀態、甚至數值)
        """
        kwargs['updated_at'] = datetime.now()
        update_data = {f"set__{k}": v for k, v in kwargs.items()}

        result = cls.engine.objects(id=key_id).update_one(**update_data)
        return result > 0

    @classmethod
    def delete_key(cls, key_id):
        result = cls.engine.objects(id=key_id).delete()
        return result > 0

    @classmethod
    def get_key_by_id(cls, key_id):
        return cls.engine.objects(id=key_id).first()


class AiApiLog(MongoBase, engine=engine.AiApiLog):
    """
    AI API conversation log document.
    course_name 是 ReferenceField('Course')
    """

    @classmethod
    def add_message(cls,
                    course_name: str,
                    username: str,
                    role: str,
                    text: str,
                    emotion: str = None):
        """Add a message to conversation history"""
        try:
            # 先取得 Course engine document
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return False

            message_obj = {'role': role, 'parts': [{'text': text}]}
            if emotion:
                message_obj['parts'][0]['emotion'] = emotion

            # Find or create log entry
            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            if not log:
                log = cls.engine(course_name=course_doc,
                                 username=username,
                                 history=[])
                log.save()

            log.update(push__history=message_obj)
            return True
        except Exception as e:
            return False

    @classmethod
    def get_history(cls, course_name: str, username: str):
        """Get conversation history for a student in a course"""
        try:
            # 先取得 Course engine document
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return []

            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            return log.history if log else []
        except Exception as e:
            return []

    @classmethod
    def update_tokens(cls, course_name: str, username: str, total_tokens: int):
        """Update total tokens used"""
        try:
            # 先取得 Course engine document
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return False

            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            if log:
                log.update(inc__total_tokens=total_tokens)
                return True
            return False
        except Exception:
            return False

    @classmethod
    def clear_history(cls, course_name: str, username: str):
        """Clear conversation history"""
        try:
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return False

            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            if log:
                log.update(set__history=[])
                return True
            return False
        except Exception:
            return False


class AiTokenUsage(MongoBase, engine=engine.AiTokenUsage):
    """
    Token usage tracking document.
    """

    @classmethod
    def add_usage(cls,
                  api_key_obj,
                  course_name: str,
                  input_tokens: int,
                  output_tokens: int,
                  problem_id=None):
        """
        Add a token usage record.
        Returns: True if successful, False otherwise.
        """
        try:
            if isinstance(course_name, str):
                course_doc = engine.Course.objects(
                    course_name=course_name).first()
            else:
                course_doc = course_name

            if hasattr(course_doc, 'obj'):
                course_doc = course_doc.obj

            # 確保 problem_id 是 Document (ReferenceField)
            problem_doc = None
            if problem_id:
                # 嘗試查找 Problem Document
                problem_doc = engine.Problem.objects(pk=problem_id).first()

            usage = cls.engine(
                api_key=api_key_obj,
                course_name=course_doc,  # We must use the engine document here
                problem_id=problem_doc,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                timestamp=datetime.now())
            usage.save()
            return True
        except Exception as e:
            print(f"[AiTokenUsage.add_usage] Error: {e}")
            return False


def migrate_ai_data():
    """
    Migrate legacy data for AI features.
    1. Set default AI model for courses if missing.
    2. Set last_reset_date and rpd for keys if missing.
    """
    # 1. Migrate Courses
    try:
        courses = engine.Course.objects(is_ai_vt_enabled=None)
        courses.update(set__is_ai_vt_enabled=True)

        default_model = engine.AiModel.objects(
            name=engine.DEFAULT_AI_MODEL).first()
        if default_model:
            courses = engine.Course.objects(ai_model=None)
            courses.update(set__ai_model=default_model)
    except Exception as e:
        print(f"Course migration failed: {e}")

    # 2. Migrate Keys
    try:
        keys = engine.AiApiKey.objects(last_reset_date=None)
        keys.update(set__last_reset_date=datetime.now())

        keys = engine.AiApiKey.objects(rpd=None)
        keys.update(set__rpd=0)
    except Exception as e:
        print(f"Key migration failed: {e}")
