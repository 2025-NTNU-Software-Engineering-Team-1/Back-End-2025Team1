import pytest
import os
from unittest.mock import patch
from mongo import engine, AiApiKey
from tests import utils
from datetime import datetime

GEMINI_API_KEY = os.getenv('GEMINI_API_TEST_KEY', 'sk-test-key')

# Make sure to add GEMINI KEY by this command to run real AI tests.
# Command:
# $env:GEMINI_API_TEST_KEY="KEY-HERE"  # PowerShell


# ==========================================
# Base Class: Common Setup
# ==========================================
@pytest.mark.usefixtures("setup_minio")
class BaseAiTest:

    @pytest.fixture(autouse=True)
    def setup_ai_environment(self, make_course, problem_ids):
        """
        Setup a course, enable AI, configure model/key, and prepare a problem.
        Runs before each test method in subclasses.
        """
        self.teacher = 'teacher'
        self.student = 'student'
        self.course_name = 'test ai'

        # 1. Ensure Users exist (Idempotent check)
        if not engine.User.objects(username=self.teacher):
            utils.user.create_user(username=self.teacher,
                                   email='teacher@test.com',
                                   role=1)

        if not engine.User.objects(username=self.student):
            utils.user.create_user(username=self.student,
                                   email='student@test.com',
                                   role=2)

        # 2. Setup Course with Teacher
        teacher_doc = engine.User.objects(username=self.teacher).first()

        # Delete existing course first to ensure clean state
        engine.Course.objects(course_name=self.course_name).delete()

        self.course = engine.Course(course_name=self.course_name,
                                    teacher=teacher_doc)
        self.course.save()

        # 3. Setup Problem
        self.pids = problem_ids(self.teacher, 1)
        self.pid = self.pids[0]

        # 4. Setup AI Model (Idempotent check)
        self.ai_model_name = 'gemini-2.5-flash'
        self.ai_model = engine.AiModel.objects(name=self.ai_model_name).first()
        if not self.ai_model:
            self.ai_model = engine.AiModel(name=self.ai_model_name,
                                           rpm_limit=15,
                                           tpm_limit=1000000,
                                           rpd_limit=200,
                                           is_active=True)
            self.ai_model.save()

        # 5. Enable AI for Course and assign Model
        self.course.update(is_ai_vt_enabled=True, ai_model=self.ai_model)
        self.course.reload()  # Reload to get updated values

        # 6. Setup API Key
        real_api_key = GEMINI_API_KEY

        self.api_key = engine.AiApiKey.objects(key_value=real_api_key).first()
        if not self.api_key:
            self.api_key = engine.AiApiKey(key_value=real_api_key,
                                           key_name='test-key',
                                           course_name=self.course,
                                           created_by=teacher_doc,
                                           is_active=True,
                                           rpd=0,
                                           request_count=0,
                                           input_token=0,
                                           output_token=0)
            self.api_key.save()
        else:
            # Update course_name if we reused an old key
            self.api_key.update(course_name=self.course)


# ==========================================
# Class 1: Chatbot / Student Interaction
# ==========================================
class TestAiChatbot(BaseAiTest):
    """
    測試學生與 AI 互動的相關功能 (Ask, History, Log)
    """

    def test_ask_chatbot_sunny_mock(self, client_student):
        """
        Test AI chatbot ask endpoint with MOCK AI response.
        """
        payload = {
            "course_name": self.course_name,
            "problem_id": self.pid,
            "message": "How do I solve this?",
            "current_code": "print('hello')"
        }

        with patch('model.utils.ai.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "text":
                            '{"data": [{"text": "Try using a loop!", "emotion": "smile"}]}'
                        }]
                    }
                }],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 50
                }
            }

            rv = client_student.post('/ai/chatbot/ask', json=payload)
            assert rv.status_code == 200, f"Failed: {rv.get_json()}"

            body = rv.get_json()
            assert 'data' in body
            assert isinstance(body['data'], dict)
            assert 'data' in body['data']

            # Verify mock response
            first_item = body['data']['data'][0]
            assert first_item['text'] == 'Try using a loop!'
            assert first_item['emotion'] == 'smile'

    # @pytest.mark.skip(reason="Real AI test - only run manually")
    # @pytest.mark.real_ai
    def test_ask_chatbot_sunny_real(self, client_student):
        """
        Test AI chatbot ask endpoint with REAL AI API call.
        (Previously missing in refactor)
        """
        if not GEMINI_API_KEY or GEMINI_API_KEY == 'sk-test-key':
            pytest.skip("GEMINI_API_KEY not set. Set it to run real AI tests.")

        payload = {
            "course_name": self.course_name,
            "problem_id": self.pid,
            "message": "How do I solve this?",
            "current_code": "print('hello')"
        }

        # Real API call without mock
        rv = client_student.post('/ai/chatbot/ask', json=payload)
        assert rv.status_code == 200, f"Failed: {rv.get_json()}"

        body = rv.get_json()
        assert 'data' in body

        # Verify response structure
        assert isinstance(body['data'], dict)
        assert 'data' in body['data']
        assert isinstance(body['data']['data'], list)
        assert len(body['data']['data']) > 0

        # Check first response item
        first_item = body['data']['data'][0]
        assert 'text' in first_item
        assert 'emotion' in first_item
        assert first_item['emotion'] in [
            "smile", "unhappy", "tired", "surprised"
        ]

    def test_get_history_sunny(self, client_student):
        '''
        Test GET /ai/chatbot/history with course_name.
        '''
        # Inject dummy history into DB
        log = engine.AiApiLog.objects(course_name=self.course,
                                      username=self.student).first()
        if not log:
            log = engine.AiApiLog(course_name=self.course,
                                  username=self.student,
                                  history=[])
            log.save()

        new_history = [{
            'role': 'user',
            'parts': [{
                'text': 'Question 1'
            }]
        }, {
            'role': 'model',
            'parts': [{
                'text': 'Answer 1',
                'emotion': 'neutral'
            }]
        }]

        log.update(set__history=new_history)
        log.reload()

        # Send Request
        rv = client_student.get(
            f'/ai/chatbot/history?course_name={self.course_name}')

        # Verify Response
        assert rv.status_code == 200, f"Failed: {rv.get_json()}"
        data = rv.get_json()['data']

        assert len(data) >= 2
        assert data[-2]['role'] == 'user'
        assert data[-2]['text'] == 'Question 1'
        assert data[-1]['role'] == 'model'
        assert data[-1]['text'] == 'Answer 1'

    def test_ask_missing_params_400(self, client_student):
        """
        Missing message/problem_id/course_name should return 400.
        (Refactor Note: Restored full checks)
        """
        # Case 1: Missing course_name
        payload = {
            "problem_id": self.pid,
            "message": "Hi",
        }
        rv = client_student.post('/ai/chatbot/ask', json=payload)
        assert rv.status_code == 400

        # Case 2: Missing problem_id
        payload2 = {
            "course_name": self.course_name,
            "message": "Hi",
        }
        rv2 = client_student.post('/ai/chatbot/ask', json=payload2)
        assert rv2.status_code == 400

        # Case 3: Missing message
        payload3 = {
            "course_name": self.course_name,
            "problem_id": self.pid,
        }
        rv3 = client_student.post('/ai/chatbot/ask', json=payload3)
        assert rv3.status_code == 400

    def test_ask_no_keys_403(self, client_student):
        """
        When no API keys are active for the course, return 403.
        """
        # Remove all keys for this course
        engine.AiApiKey.objects(course_name=self.course).delete()

        with patch('model.utils.ai.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            payload = {
                "course_name": self.course_name,
                "problem_id": self.pid,
                "message": "Help me!",
                "current_code": "print('hello')",
            }
            rv = client_student.post('/ai/chatbot/ask', json=payload)
            assert rv.status_code == 403

    def test_ask_increments_key_usage(self, client_student):
        """
        Verify request_count, rpd, input_token, output_token are incremented.
        (Refactor Note: Restored checks for rpd and output_token)
        """
        key = engine.AiApiKey.objects(course_name=self.course,
                                      is_active=True).first()
        assert key is not None

        before = {
            "request_count": key.request_count,
            "rpd": key.rpd,
            "input_token": key.input_token,
            "output_token": key.output_token,
        }

        mock_gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text":
                        "{\"data\": [{\"text\": \"ok\", \"emotion\": \"2\"}]}"
                    }]
                }
            }],
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 7
            },
        }

        with patch('model.utils.ai.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_gemini_response

            payload = {
                "course_name": self.course_name,
                "problem_id": self.pid,
                "message": "Token test",
                "current_code": "print(1)",
            }
            rv = client_student.post('/ai/chatbot/ask', json=payload)
            assert rv.status_code == 200

        # Reload key values
        key.reload()
        assert key.request_count == before["request_count"] + 1
        assert key.rpd == before["rpd"] + 1
        assert key.input_token == before["input_token"] + 11
        assert key.output_token == before["output_token"] + 7

    def test_ask_fallback_emotion_default_thinking(self, client_student):
        """
        If provider returns non-JSON content, fallback emotion should be normalized.
        (Previously missing in refactor)
        """
        bad_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text": "NOT JSON HERE"
                    }]
                }
            }],
            "usageMetadata": {
                "promptTokenCount": 3,
                "candidatesTokenCount": 2
            },
        }

        with patch('model.utils.ai.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = bad_response

            payload = {
                "course_name": self.course_name,
                "problem_id": self.pid,
                "message": "Fallback?",
                "current_code": "",
            }
            rv = client_student.post('/ai/chatbot/ask', json=payload)
            assert rv.status_code == 200

            body = rv.get_json()
            inner = body.get('data', {})
            # Emotion should be normalized to "smile" (default)
            assert inner["data"][0]["emotion"] == "smile"

    def test_history_flatten_text(self, client_student):
        """
        History should flatten parts to plain text (ignore emotion in output).
        (Previously missing in refactor)
        """
        log = engine.AiApiLog.objects(course_name=self.course,
                                      username=self.student).first()
        if not log:
            log = engine.AiApiLog(course_name=self.course,
                                  username=self.student,
                                  history=[])
            log.save()

        new_history = [
            {
                'role': 'user',
                'parts': [{
                    'text': 'Question A'
                }]
            },
            {
                'role': 'model',
                'parts': [{
                    'text': 'Answer A',
                    'emotion': '8'
                }]
            },
            {
                'role': 'model',
                'parts': [{
                    'text': 'Extra'
                }]
            },
        ]
        log.update(set__history=new_history)
        log.reload()

        rv = client_student.get(
            f'/ai/chatbot/history?course_name={self.course_name}')
        assert rv.status_code == 200
        data = rv.get_json().get('data', [])

        assert any(item.get('text') == 'Question A' for item in data)
        assert any(item.get('text') == 'Answer A' for item in data)
        assert any(item.get('text') == 'Extra' for item in data)

    def test_ask_creates_token_usage_record(self, client_student):
        """
        Verify that asking a question creates a record in AiTokenUsage.
        """
        engine.AiTokenUsage.objects(course_name=self.course).delete()

        mock_gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "text":
                        '{"data": [{"text": "Token usage check", "emotion": "smile"}]}'
                    }]
                }
            }],
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50
            },
        }

        with patch('model.utils.ai.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = mock_gemini_response

            payload = {
                "course_name": self.course_name,
                "problem_id": self.pid,
                "message": "Testing token log",
                "current_code": "pass",
            }
            rv = client_student.post('/ai/chatbot/ask', json=payload)
            assert rv.status_code == 200

        # Check DB
        usage = engine.AiTokenUsage.objects(course_name=self.course).first()
        assert usage is not None, "AiTokenUsage record was not created"

        # Verify fields
        assert usage.api_key == self.api_key
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

        # Check Problem
        expected_problem = engine.Problem.objects(pk=self.pid).first()
        assert usage.problem_id == expected_problem


# ==========================================
# Class 2: Management / Teacher Operations
# ==========================================
class TestAiManagement(BaseAiTest):
    """
    測試教師端的管理功能 (Key CRUD, Usage, Suggestion)
    """

    def test_get_key_suggestion(self, client_teacher):
        """
        Test key suggestion calculation logic.
        """
        # Add dummy students
        self.course.student_nicknames = {
            f's{i}': f'Student {i}'
            for i in range(5)
        }
        self.course.save()

        # RPM=10, Effective=5. Students=5 => ceil(5/5)=1
        rv = client_teacher.get(
            f'/course/{self.course_name}/ai/key/suggestion')
        assert rv.status_code == 200
        data = rv.get_json()['data']
        assert data['student_count'] == 5
        assert data['suggested_key_count'] == 1

        # Increase students
        self.course.student_nicknames = {
            f's{i}': f'Student {i}'
            for i in range(60)
        }
        self.course.save()

        # RPM=10 (Flash Lite), Effective=5 (10*0.5).
        # Students=60 => ceil(60/5)=12
        rv = client_teacher.get(
            f'/course/{self.course_name}/ai/key/suggestion')
        assert rv.status_code == 200
        assert rv.get_json()['data']['suggested_key_count'] == 12

    def test_course_ai_key_lifecycle(self, client_teacher):
        """
        Test GET list, POST add, PATCH update, DELETE key.
        """
        # 1. GET List
        rv = client_teacher.get(f'/course/{self.course_name}/ai/key')
        assert rv.status_code == 200
        keys = rv.get_json()['data']['keys']
        assert len(keys) >= 1

        # 2. POST Add New Key
        payload = {
            "key_name": "lifecycle-key",
            "value": "sk-lifecycle-val",
            "is_active": True
        }
        rv = client_teacher.post(f'/course/{self.course_name}/ai/key',
                                 json=payload)
        assert rv.status_code == 200
        new_key_id = rv.get_json()['data']['id']

        # 3. PATCH Update Key
        update_payload = {"is_active": False}
        rv = client_teacher.patch(
            f'/course/{self.course_name}/ai/key/{new_key_id}',
            json=update_payload)
        assert rv.status_code == 200

        updated_key = engine.AiApiKey.objects(id=new_key_id).first()
        assert updated_key.is_active is False

        # 4. DELETE Key
        rv = client_teacher.delete(
            f'/course/{self.course_name}/ai/key/{new_key_id}')
        assert rv.status_code == 200
        assert engine.AiApiKey.objects(id=new_key_id).first() is None

    def test_get_course_ai_usage_stats(self, client_teacher):
        """
        Verify usage stats aggregation from AiTokenUsage.
        """
        # Insert usage records manually
        engine.AiTokenUsage.objects(course_name=self.course).delete()
        engine.AiTokenUsage(
            api_key=self.api_key,
            course_name=self.course,
            problem_id=engine.Problem.objects(pk=self.pid).first(),
            input_tokens=100,
            output_tokens=100,
            timestamp=datetime.now()).save()

        rv = client_teacher.get(f'/course/{self.course_name}/aisetting/usage')
        assert rv.status_code == 200

        data = rv.get_json()['data']
        keys = data['keys']
        target_key = next((k for k in keys if k['id'] == str(self.api_key.id)),
                          None)
        assert target_key is not None
        assert len(target_key['problem_usages']) == 1
        assert target_key['problem_usages'][0]['total_token'] == 200

    def test_ai_checker_assigned_problem_counts(self, client_teacher):
        """
        AI Checker assigned problems should appear in usage even with zero tokens.
        """
        engine.AiTokenUsage.objects(course_name=self.course).delete()

        problem_doc = engine.Problem.objects(pk=self.pid).first()
        assert problem_doc is not None
        problem_doc.update(set__courses=[self.course],
                           set__config={
                               "aiChecker": {
                                   "enabled": True,
                                   "apiKeyId": str(self.api_key.id),
                                   "model": "gemini-2.5-flash",
                               },
                           })

        rv = client_teacher.get(f'/course/{self.course_name}/aisetting/usage')
        assert rv.status_code == 200

        data = rv.get_json()['data']
        keys = data['keys']
        target_key = next((k for k in keys if k['id'] == str(self.api_key.id)),
                          None)
        assert target_key is not None
        assert any(
            u.get('problem_id') == str(self.pid)
            for u in target_key.get('problem_usages', []))


from datetime import timedelta
from mongo.ai import AiModel


# ==========================================
# Class 3: Features & Migration
# ==========================================
class TestAiFeatures(BaseAiTest):
    """
    Test new AI features: RPD Reset, Default Models, Migration.
    Uses Mocks to avoid real DB interaction where possible.
    """

    def test_default_models_initialization(self):
        """
        Verify that default models are created correctly.
        """
        # Clear existing models
        engine.AiModel.objects.delete()

        AiModel.initialize_default_models()

        # Check Flash
        flash = engine.AiModel.objects(name='gemini-2.5-flash').first()
        assert flash is not None
        assert flash.rpm_limit == 5
        assert flash.rpd_limit == 20

        # Check Flash Lite
        lite = engine.AiModel.objects(name='gemini-flash-lite-latest').first()
        assert lite is not None
        assert lite.rpm_limit == 10

        # Check 3.0 Pro should NOT exist
        pro = engine.AiModel.objects(name='gemini-3.0-pro').first()
        assert pro is None

    @patch('mongo.ai.models.datetime')
    @patch('mongo.ai.models.engine.RPD_RESET_INTERVAL',
           new=timedelta(hours=24))
    def test_rpd_reset_logic(self, mock_datetime):
        """
        Verify RPD reset logic with time mocking.
        """
        # Setup: Key with RPD=10, Reset Time = Yesterday
        # Use a fixed start time
        start_time = datetime(2025, 1, 1, 12, 0, 0)
        mock_datetime.now.return_value = start_time

        key = engine.AiApiKey(
            key_value="reset-test-key",
            key_name="reset-test",
            course_name=self.course,
            created_by=engine.User.objects(username=self.teacher).first(),
            is_active=True,
            rpd=10,
            last_reset_date=start_time)
        key.save()

        wrapper = AiApiKey(key.id)
        wrapper.obj = key

        # 1. Check BEFORE 24h (No Reset)
        # Advance 23 hours
        mock_datetime.now.return_value = start_time + timedelta(hours=23)
        wrapper.check_reset()

        key.reload()
        assert key.rpd == 10
        assert key.last_reset_date == start_time

        # 2. Check AFTER 24h (Should Reset)
        # Advance 25 hours
        reset_time = start_time + timedelta(hours=25)
        mock_datetime.now.return_value = reset_time
        wrapper.check_reset()

        key.reload()
        assert key.rpd == 0
        assert key.last_reset_date == reset_time

    def test_migration_logic(self):
        """
        Verify that migration adds missing fields.
        """
        # 1. Setup Data with missing fields
        # Create a raw Course without ai_model
        raw_course = engine.Course(
            course_name="Legacy Course",
            teacher=engine.User.objects(username=self.teacher).first())
        raw_course.save()
        # Explicitly unset fields using simple update (bypass default if possible)
        # Or just rely on the fact that if we pass None/Missing it might work on some DBs
        # Here we trust MongoEngine defaults might fill some, but let's try to simulate 'None'
        raw_course.update(unset__ai_model=1)

        # Create Key without last_reset_date
        raw_key = engine.AiApiKey(
            key_value="legacy-key",
            key_name="legacy-key",
            course_name=raw_course,
            created_by=engine.User.objects(username=self.teacher).first())
        raw_key.save()
        raw_key.update(unset__last_reset_date=1, unset__rpd=1)

        # Ensure default model exists for migration
        AiModel.initialize_default_models()

        # 2. Run Migration
        from mongo.ai import migrate_ai_data
        migrate_ai_data()

        # 3. Verify Course
        raw_course.reload()
        assert raw_course.is_ai_vt_enabled is True
        assert raw_course.ai_model.name == 'gemini-flash-lite-latest'

        # 4. Verify Key
        raw_key.reload()
        assert raw_key.last_reset_date is not None
        assert raw_key.rpd == 0
