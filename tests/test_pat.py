import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from tests.base_tester import BaseTester
from model.profile import profile_api
from model.utils.pat import hash_pat_token, _clean_token
from mongo.engine import PersonalAccessToken


class TestPATHelpers:
    """Test helper functions that don't require Flask context"""

    def setup_method(self):
        """Reset PATs in DB before each test"""
        PersonalAccessToken.objects().delete()
        test_token = "noj_pat_test_secret"
        PersonalAccessToken(
            pat_id='test_001',
            name='Test PAT',
            owner='test_user',
            hash=hash_pat_token(test_token),
            scope=['read', 'write'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        ).save()

    def test_hash_pat_token(self):
        """Test PAT token hash generation"""
        token = "noj_pat_test_secret"
        hash_val = hash_pat_token(token)

        assert isinstance(hash_val, str)
        assert len(hash_val) == 64

        assert hash_pat_token(token) == hash_val

        assert hash_pat_token("noj_pat_different_secret") != hash_val

    def test_clean_token(self):
        """Test token mapping from MongoDB object"""
        pat = PersonalAccessToken.objects.get(pat_id='test_001')
        cleaned = _clean_token(pat)

        assert cleaned['Name'] == 'Test PAT'
        assert cleaned['ID'] == 'test_001'
        assert cleaned['Owner'] == 'test_user'
        assert cleaned['Status'] == 'Active'
        assert cleaned['Scope'] == ['read', 'write']
        assert 'Hash' not in cleaned
        assert 'Is_Revoked' not in cleaned

    def test_clean_token_with_status(self):
        """Test _clean_token includes correct status"""
        pat = PersonalAccessToken.objects.get(pat_id='test_001')
        cleaned = _clean_token(pat)
        assert cleaned['Status'] == 'Active'

        pat.update(is_revoked=True)
        pat.reload()
        cleaned = _clean_token(pat)
        assert cleaned['Status'] == 'Deactivated'

    def test_api_token_store_manipulation(self):
        """Test PAT creation and listing via MongoDB"""
        new_token = "noj_pat_new_secret"
        PersonalAccessToken(
            pat_id='new_001',
            name='New Token',
            owner='another_user',
            hash=hash_pat_token(new_token),
            scope=['read'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        ).save()

        all_tokens = PersonalAccessToken.objects()
        assert len(all_tokens) == 2

        owners = [t.owner for t in all_tokens]
        assert 'test_user' in owners
        assert 'another_user' in owners

    def test_token_revocation_simulation(self):
        """Test simulating token revocation"""
        pat = PersonalAccessToken.objects.get(pat_id='test_001')
        pat.update(is_revoked=True,
                   revoked_by='admin',
                   revoked_time=datetime.now(timezone.utc))
        pat.reload()

        cleaned = _clean_token(pat)
        assert cleaned['Status'] == 'Deactivated'


class TestPATRoutes(BaseTester):
    """Test PAT Flask routes with authentication"""

    def setup_method(self):
        """Reset PATs and seed a student token in DB"""
        PersonalAccessToken.objects().delete()
        student_token = "noj_pat_student_secret"
        PersonalAccessToken(
            pat_id='student_001',
            name='Student PAT',
            owner='student',
            hash=hash_pat_token(student_token),
            scope=['read', 'write'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        ).save()

    def test_get_tokens_endpoint(self, client_student):
        """Test GET /profile/api_token"""
        rv = client_student.get('/profile/api_token')
        json_data = rv.get_json()

        assert rv.status_code == 200
        assert json_data['status'] == 'ok'
        assert 'data' in json_data
        assert 'Tokens' in json_data['data']

        tokens = json_data['data']['Tokens']
        assert len(tokens) == 1
        assert tokens[0]['Name'] == 'Student PAT'
        assert tokens[0]['Owner'] == 'student'

        assert 'Hash' not in tokens[0]
        assert 'Is_Revoked' not in tokens[0]

    def test_get_scope_endpoint(self, client_student):
        """Test GET /profile/api_token/getscope"""
        rv = client_student.get('/profile/api_token/getscope')
        json_data = rv.get_json()

        assert rv.status_code == 200
        assert json_data['status'] == 'ok'
        assert 'data' in json_data
        assert 'Scope' in json_data['data']

        scopes = json_data['data']['Scope']
        assert 'read:user' in scopes
        assert 'write:submissions' in scopes
        assert 'read:problems' in scopes

    def test_create_token_endpoint(self, client_student):
        """Test POST /profile/api_token/create"""
        token_data = {
            'Name': 'New Test Token',
            'Due_Time': None,
            'Scope': ['read:user']
        }

        rv = client_student.post('/profile/api_token/create', json=token_data)
        json_data = rv.get_json()

        assert rv.status_code == 200
        assert json_data['status'] == 'ok'
        assert json_data['message'] == 'Token Created'
        assert 'data' in json_data

        data = json_data['data']
        assert data['Type'] == 'OK'
        assert data['Message'] == 'Token Created'
        assert 'Token' in data

        token = data['Token']
        assert token.startswith('noj_pat_')

        assert PersonalAccessToken.objects(owner='student').count() == 2

        new_tokens = PersonalAccessToken.objects(owner='student',
                                                 pat_id__ne='student_001')
        assert len(new_tokens) == 1
        new_token = new_tokens[0]
        assert new_token.owner == 'student'
        assert new_token.name == 'New Test Token'
        assert new_token.scope == ['read:user']

    def test_edit_token_endpoint(self, client_student):
        """Test PATCH /profile/api_token/edit/<pat_id>"""
        pat_id = 'student_001'
        edit_data = {
            'data': {
                'Name': 'Updated Token Name',
                'Scope': ['read:courses', 'write:submissions']
            }
        }

        rv = client_student.patch(f'/profile/api_token/edit/{pat_id}',
                                  json=edit_data)
        json_data = rv.get_json()

        assert rv.status_code == 200
        assert json_data['status'] == 'ok'
        assert json_data['message'] == 'Token updated'

        data = json_data['data']
        assert data['Type'] == 'OK'
        assert data['Message'] == 'Token updated'

        token = PersonalAccessToken.objects.get(pat_id=pat_id)
        assert token.name == 'Updated Token Name'
        assert token.scope == ['read:courses', 'write:submissions']

    def test_edit_nonexistent_token(self, client_student):
        """Test editing non-existent token returns 404"""
        edit_data = {'data': {'Name': 'Should Fail'}}

        rv = client_student.patch('/profile/api_token/edit/nonexistent',
                                  json=edit_data)
        json_data = rv.get_json()

        assert rv.status_code == 404
        assert json_data['status'] == 'err'
        assert 'Token not found' in json_data['message']

    def test_deactivate_token_endpoint(self, client_student):
        """Test PATCH /profile/api_token/deactivate/<pat_id>"""
        pat_id = 'student_001'

        rv = client_student.patch(f'/profile/api_token/deactivate/{pat_id}')
        json_data = rv.get_json()

        error_data = json_data.get('data', {})

        backend_message = error_data.get(
            'Message', 'Backend did not provide detailed error message')

        assert rv.status_code == 200, f"\nBack-End error: {backend_message}"
        assert json_data['status'] == 'ok'
        assert json_data['message'] == 'Token revoked'

        data = json_data['data']
        assert data['Type'] == 'OK'
        assert data['Message'] == 'Token revoked'

        token = PersonalAccessToken.objects.get(pat_id=pat_id)
        assert token.is_revoked is True
        assert token.revoked_by == 'student'

    def test_deactivate_already_revoked_token(self, client_student):
        """Test deactivating already revoked token returns 400"""
        PersonalAccessToken.objects(pat_id='student_001').update(
            is_revoked=True)

        pat_id = 'student_001'
        rv = client_student.patch(f'/profile/api_token/deactivate/{pat_id}')
        json_data = rv.get_json()

        assert rv.status_code == 400
        assert json_data['status'] == 'err'
        assert 'already revoked' in json_data['message']

    def test_unauthorized_access(self, client):
        """Test that endpoints require authentication"""
        endpoints = [
            ('/profile/api_token', 'GET'),
            ('/profile/api_token/getscope', 'GET'),
            ('/profile/api_token/create', 'POST'),
            ('/profile/api_token/edit/test', 'PATCH'),
            ('/profile/api_token/deactivate/test', 'PATCH'),
        ]

        for endpoint, method in endpoints:
            if method == 'GET':
                rv = client.get(endpoint)
            elif method == 'POST':
                rv = client.post(endpoint, json={})
            elif method == 'PATCH':
                rv = client.patch(endpoint, json={})

            assert rv.status_code == 403

    def test_cross_user_token_access(self, app):
        """Test that users can't access each other's tokens"""
        PersonalAccessToken.objects().delete()
        student_token = "noj_pat_student_secret"
        teacher_token = "noj_pat_teacher_secret"
        PersonalAccessToken(
            pat_id='student_001',
            name='Student PAT',
            owner='student',
            hash=hash_pat_token(student_token),
            scope=['read:courses', 'write:submissions'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        ).save()
        PersonalAccessToken(
            pat_id='teacher_001',
            name='Teacher PAT',
            owner='teacher',
            hash=hash_pat_token(teacher_token),
            scope=['grade:submissions'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
            created_time=datetime.now(timezone.utc),
            is_revoked=False,
        ).save()

        from mongo import User

        client_student = app.test_client()
        client_student.set_cookie('piann',
                                  User('student').secret,
                                  domain='test.test')

        client_teacher = app.test_client()
        client_teacher.set_cookie('piann',
                                  User('teacher').secret,
                                  domain='test.test')

        rv = client_student.get('/profile/api_token')
        json_data = rv.get_json()
        tokens = json_data['data']['Tokens']
        assert len(tokens) == 1
        assert tokens[0]['Owner'] == 'student'

        rv = client_teacher.get('/profile/api_token')
        json_data = rv.get_json()
        tokens = json_data['data']['Tokens']
        assert len(tokens) == 1
        assert tokens[0]['Owner'] == 'teacher'

        rv = client_student.patch('/profile/api_token/edit/teacher_001',
                                  json={'data': {
                                      'Name': 'Hacked'
                                  }})
        assert rv.status_code == 403
