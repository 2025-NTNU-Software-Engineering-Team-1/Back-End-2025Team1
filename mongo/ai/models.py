"""
Mongo AI Models - Data layer for AI features.

This module contains all MongoDB document wrappers for AI-related features:
- AiModel: AI model configuration
- AiApiKey: API key management
- AiApiLog: Conversation history
- AiTokenUsage: Token usage tracking
"""
from datetime import datetime
from mongo import engine
from mongo.base import MongoBase

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

    def __eq__(self, other):
        return super().__eq__(other)

    @property
    def rpm_limit(self):
        if self.obj:
            return self.obj.rpm_limit
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
            'description': 'Google Gemini 2.5 Flash (Stable)',
            'is_active': True
        }, {
            'name': 'gemini-flash-lite-latest',
            'rpm_limit': 10,
            'tpm_limit': 250000,
            'rpd_limit': 20,
            'description': 'Google Gemini Flash Lite (Latest)',
            'is_active': True
        }, {
            'name': 'gemini-flash-latest',
            'rpm_limit': 5,
            'tpm_limit': 250000,
            'rpd_limit': 20,
            'description': 'Google Gemini Flash (Latest)',
            'is_active': True
        }, {
            'name': 'gemini-3-flash-preview',
            'rpm_limit': 5,
            'tpm_limit': 250000,
            'rpd_limit': 20,
            'description': 'Google Gemini 3.0 Flash (Preview)',
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
        if not getattr(self, 'obj', None) or not self.obj.id:
            self.obj = self.engine.objects(id=key_id).first()

    def __eq__(self, other):
        return super().__eq__(other)

    @classmethod
    def get_active_keys_by_course_name(cls, course_name: str):
        """Get all active API keys for a specific course by course_name (string)"""
        try:
            course_obj = engine.Course.objects(course_name=course_name).first()
            if not course_obj:
                return []

            keys = cls.engine.objects(course_name=course_obj, is_active=True)

            wrappers = []
            for k in keys:
                wrapper = cls(k.id)
                wrapper.obj = k
                wrapper.check_reset()
                wrappers.append(wrapper)
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

    # ========================================
    # Private helpers for get_keys_usage_by_course
    # ========================================

    @staticmethod
    def _aggregate_usage_stats(key_id, course_id, since_date=None):
        """
        Aggregate token usage stats per problem for a key.
        If since_date is provided, only count usage after that date.
        Returns: list of {problem_id, total_input, total_output}
        """
        match_cond = {'apiKey': key_id, 'courseName': course_id}
        if since_date:
            match_cond['timestamp'] = {'$gte': since_date}

        pipeline = [{
            '$match': match_cond
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
        return list(engine.AiTokenUsage.objects.aggregate(*pipeline))

    @staticmethod
    def _build_problem_usages(usage_stats, cycle_map):
        """
        Convert aggregated stats to problem_usages list format.
        Returns: (problem_usages list, cycle_total)
        """
        problem_usages = []
        cycle_total = 0

        for stat in usage_stats:
            p_id = stat.get('_id')
            if not p_id:
                continue

            total = stat.get('totalInput', 0) + stat.get('totalOutput', 0)
            prob = engine.Problem.objects(pk=p_id).only('problem_name').first()
            p_name = prob.problem_name if prob else f"Problem {p_id}"
            p_id_str = str(p_id)
            cycle_token = cycle_map.get(p_id_str, 0)
            cycle_total += cycle_token

            problem_usages.append({
                "problem_id": p_id_str,
                "problem_name": p_name,
                "total_token": total,
                "cycle_token": cycle_token
            })

        return problem_usages, cycle_total

    @staticmethod
    def _mask_key_value(raw_key):
        """Mask API key value for display."""
        if not raw_key or len(raw_key) <= 8:
            return "****"
        return f"{raw_key[:4]}****{raw_key[-4:]}"

    @classmethod
    def _build_key_info(cls, key, problem_usages, cycle_map, cycle_total):
        """Build the key info dict for API response."""
        last_reset = key.last_reset_date or datetime.now()
        return {
            "id": str(key.id),
            "key_name": key.key_name,
            "masked_value": cls._mask_key_value(key.key_value),
            "is_active": key.is_active,
            "input_token": key.input_token,
            "output_token": key.output_token,
            "request_count": key.request_count,
            "rpd": key.rpd,
            "last_reset_date": last_reset.isoformat() if last_reset else None,
            "cycle_total_token": cycle_total,
            "created_by":
            key.created_by.username if key.created_by else "System",
            "problem_usages": problem_usages,
        }

    @staticmethod
    def _find_zero_usage_problems(key, course_obj, existing_pids):
        """
        Find problems assigned to this key but with no usage records.
        Returns list of problem dicts with total_token=0.
        """
        check_fields = [
            'config.api_key',
            'config.apiKey',
            'config.ai_key',
            'config.aiKey',
            'config.api_key_id',
            'config.ai_key_id',
            'config.key_id',
            'config.aiChecker.apiKeyId',
        ]

        zero_usages = []
        for field in check_fields:
            for match_val in (key.id, str(key.id)):
                try:
                    qs = engine.Problem.objects(__raw__={
                        field: match_val,
                        'courses': course_obj.id
                    })
                except Exception:
                    continue

                for prob in qs:
                    pid = getattr(prob, 'problem_id', None) or getattr(
                        prob, 'pk', None)
                    pid_s = str(pid) if pid is not None else None
                    if pid_s in existing_pids:
                        continue
                    existing_pids.add(pid_s)

                    pname = getattr(prob, 'problem_name', None) or getattr(
                        prob, 'problemName', None) or f"Problem {pid}"
                    zero_usages.append({
                        'problem_id': pid_s,
                        'problem_name': pname,
                        'total_token': 0,
                        'cycle_token': 0
                    })

        return zero_usages

    # ========================================
    # Main public method
    # ========================================

    @classmethod
    def get_keys_usage_by_course(cls, course_name: str):
        """Get usage statistics for all API keys in a course."""
        course_obj = engine.Course.objects(course_name=course_name).first()
        if not course_obj:
            return []

        keys = cls.engine.objects(course_name=course_obj)
        key_map = {}

        for key in keys:
            last_reset = key.last_reset_date or datetime.now()

            # Get all-time and cycle usage stats
            usage_stats = cls._aggregate_usage_stats(key.id, course_obj.id)
            cycle_stats = cls._aggregate_usage_stats(key.id, course_obj.id,
                                                     last_reset)

            # Build cycle lookup map
            cycle_map = {
                str(s['_id']):
                s.get('totalInput', 0) + s.get('totalOutput', 0)
                for s in cycle_stats if s.get('_id')
            }

            # Build problem usages list
            problem_usages, cycle_total = cls._build_problem_usages(
                usage_stats, cycle_map)

            # Build key info
            kid = str(key.id)
            key_map[kid] = cls._build_key_info(key, problem_usages, cycle_map,
                                               cycle_total)

        # Add problems with zero usage (assigned but never used)
        for key in keys:
            kid = str(key.id)
            existing_pids = {
                u.get('problem_id')
                for u in key_map.get(kid, {}).get('problem_usages', [])
            }
            zero_usages = cls._find_zero_usage_problems(
                key, course_obj, existing_pids)
            if kid in key_map:
                key_map[kid]['problem_usages'].extend(zero_usages)

        return list(key_map.values())

    @classmethod
    def get_list_by_course(cls, course_name: str):
        """Get all API Keys for a specific course by course_name (string)"""
        course_obj = engine.Course.objects(course_name=course_name).first()
        if not course_obj:
            return []

        keys = cls.engine.objects(course_name=course_obj)
        result_list = []

        for key in keys:
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
        """建立新的 API Key"""
        real_course_doc = engine.Course.objects(id=course_id).first()
        if not real_course_doc:
            raise ValueError("Course not found")

        if cls.engine.objects(course_name=real_course_doc,
                              key_name=key_name).first():
            raise ValueError(
                f"Key name '{key_name}' already exists in this course.")

        real_created_by = created_by.obj if hasattr(created_by,
                                                    'obj') else created_by

        new_key = cls.engine(course_name=real_course_doc,
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
        """更新 Key (支援改名、狀態、甚至數值)"""
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
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return False

            message_obj = {'role': role, 'parts': [{'text': text}]}
            if emotion:
                message_obj['parts'][0]['emotion'] = emotion

            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            if not log:
                log = cls.engine(course_name=course_doc,
                                 username=username,
                                 history=[])
                log.save()

            log.update(push__history=message_obj)
            return True
        except Exception:
            return False

    @classmethod
    def get_history(cls, course_name: str, username: str):
        """Get conversation history for a student in a course"""
        try:
            course_doc = engine.Course.objects(course_name=course_name).first()
            if not course_doc:
                return []

            log = cls.engine.objects(course_name=course_doc,
                                     username=username).first()
            return log.history if log else []
        except Exception:
            return []

    @classmethod
    def update_tokens(cls, course_name: str, username: str, total_tokens: int):
        """Update total tokens used"""
        try:
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
    """Token usage tracking document."""

    @classmethod
    def add_usage(cls,
                  api_key_obj,
                  course_name: str,
                  input_tokens: int,
                  output_tokens: int,
                  problem_id=None):
        """Add a token usage record."""
        try:
            if isinstance(course_name, str):
                course_doc = engine.Course.objects(
                    course_name=course_name).first()
            else:
                course_doc = course_name

            if hasattr(course_doc, 'obj'):
                course_doc = course_doc.obj

            problem_doc = None
            if problem_id:
                problem_doc = engine.Problem.objects(pk=problem_id).first()

            usage = cls.engine(api_key=api_key_obj,
                               course_name=course_doc,
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
