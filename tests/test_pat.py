import pytest
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from tests.base_tester import BaseTester
from model.profile import profile_api
from mongo.pat import PersonalAccessToken


class TestPATHelpers:
    """Test helper functions that don't require Flask context"""

    def setup_method(self):
        """Reset PATs in DB before each test"""
        PersonalAccessToken.objects().delete()
        # Use hash_token with full token string
        test_token = "noj_pat_test_secret"
        PersonalAccessToken.add(
            pat_id='test_001',
            name='Test PAT',
            owner='test_user',
            hash_val=PersonalAccessToken.hash_token(test_token),
            scope=['read', 'write'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30))

    def test_hash_pat_token(self):
        """Test PAT token hash generation"""
        token = "noj_pat_test_secret"
        hash_val = PersonalAccessToken.hash_token(token)

        assert isinstance(hash_val, str)
        assert len(hash_val) == 64  # SHA-256 hex digest is 64 chars

        # Same token should produce same hash
        assert PersonalAccessToken.hash_token(token) == hash_val

        # Different token should produce different hash
        assert PersonalAccessToken.hash_token(
            "noj_pat_different_secret") != hash_val

    def test_clean_token(self):
        """Test token mapping from MongoDB object"""
        pat = PersonalAccessToken(
            PersonalAccessToken.objects.get(pat_id='test_001'))
        cleaned = pat.to_dict()

        assert cleaned['Name'] == 'Test PAT'
        assert cleaned['ID'] == 'test_001'
        assert cleaned['Owner'] == 'test_user'
        assert cleaned['Status'] == 'Active'
        assert cleaned['Scope'] == ['read', 'write']
        # Internal fields should not be present in mapping
        assert 'Hash' not in cleaned
        assert 'Is_Revoked' not in cleaned

    def test_clean_token_with_status(self):
        """Test to_dict includes correct status"""
        # Test active token
        pat = PersonalAccessToken(
            PersonalAccessToken.objects.get(pat_id='test_001'))
        cleaned = pat.to_dict()
        assert cleaned['Status'] == 'Active'

        # Test revoked token
        pat.update(is_revoked=True)
        pat.reload()
        cleaned = pat.to_dict()
        assert cleaned['Status'] == 'Deactivated'

    def test_api_token_store_manipulation(self):
        """Test PAT creation and listing via MongoDB"""
        new_token = "noj_pat_new_secret"
        PersonalAccessToken.add(
            pat_id='new_001',
            name='New Token',
            owner='another_user',
            hash_val=PersonalAccessToken.hash_token(new_token),
            scope=['read'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30),
        )

        # Should now have 2 tokens in database
        all_tokens = PersonalAccessToken.objects()
        assert len(all_tokens) == 2

        owners = [t.owner for t in all_tokens]
        assert 'test_user' in owners
        assert 'another_user' in owners

    def test_token_revocation_simulation(self):
        """Test simulating token revocation"""
        # Revoke the test token in DB
        pat = PersonalAccessToken(
            PersonalAccessToken.objects.get(pat_id='test_001'))
        pat.update(is_revoked=True,
                   revoked_by='admin',
                   revoked_time=datetime.now(timezone.utc))
        pat.reload()

        # Clean token mapping should reflect status change
        cleaned = pat.to_dict()
        assert cleaned['Status'] == 'Deactivated'

    def test_generate_token(self):
        """Test PAT generation wrapper"""
        # Test basic generation
        plaintext, pat = PersonalAccessToken.generate(name="Gen Token",
                                                      owner="gen_user",
                                                      scope=['read'],
                                                      due_time=None)

        assert plaintext.startswith("noj_pat_")
        assert len(plaintext) > 40
        assert pat.name == "Gen Token"
        assert pat.owner == "gen_user"
        # Verify hash matches
        assert pat.hash == PersonalAccessToken.hash_token(plaintext)

        # Test generation with valid due time
        future = datetime.now(timezone.utc) + timedelta(days=1)
        _, pat_future = PersonalAccessToken.generate(name="Future Token",
                                                     owner="gen_user",
                                                     scope=['read'],
                                                     due_time=future)
        assert pat_future.status == "active"

        # Test generation with past due time (should fail)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(ValueError):
            PersonalAccessToken.generate(name="Past Token",
                                         owner="gen_user",
                                         scope=['read'],
                                         due_time=past)


class TestPATRoutes(BaseTester):
    """Test PAT Flask routes with authentication"""

    def setup_method(self):
        """Reset PATs and seed a student token in DB"""
        PersonalAccessToken.objects().delete()
        student_token = "noj_pat_student_secret"
        PersonalAccessToken.add(
            pat_id='student_001',
            name='Student PAT',
            owner='student',
            hash_val=PersonalAccessToken.hash_token(student_token),
            scope=['read', 'write'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30))

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

        # Should not contain internal fields
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

        # Token should be in format "noj_pat_<secret>"
        token = data['Token']
        assert token.startswith('noj_pat_')

        # Should now have 2 tokens for this owner in DB
        assert PersonalAccessToken.objects(owner='student').count() == 2

        # Find the new token by excluding the existing one
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

        # Check that token was actually updated in DB
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

        backend_message = error_data.get('Message', '後端未提供詳細錯誤訊息')

        assert rv.status_code == 200, f"\nBack-End error: {backend_message}"
        assert json_data['status'] == 'ok'
        assert json_data['message'] == 'Token revoked'

        data = json_data['data']
        assert data['Type'] == 'OK'
        assert data['Message'] == 'Token revoked'

        # Check that token was actually revoked in DB
        token = PersonalAccessToken.objects.get(pat_id=pat_id)
        assert token.is_revoked is True
        assert token.revoked_by == 'student'

    def test_deactivate_already_revoked_token(self, client_student):
        """Test deactivating already revoked token returns 400"""
        # First revoke the token in DB
        PersonalAccessToken.objects(pat_id='student_001').update(
            is_revoked=True)

        pat_id = 'student_001'
        rv = client_student.patch(f'/profile/api_token/deactivate/{pat_id}')
        json_data = rv.get_json()

        assert rv.status_code == 400
        assert json_data['status'] == 'err'
        assert 'already revoked' in json_data['message']

    def test_admin_revoke_token(self, client_admin):
        """Test that admin can revoke any user's token"""
        # Admin revoking student's token
        pat_id = 'student_001'
        rv = client_admin.patch(f'/profile/api_token/deactivate/{pat_id}')
        json_data = rv.get_json()

        assert rv.status_code == 200
        assert json_data['status'] == 'ok'
        assert json_data['message'] == 'Token revoked'

        # Verify in DB
        token = PersonalAccessToken(
            PersonalAccessToken.objects.get(pat_id=pat_id))
        assert token.is_revoked is True
        assert token.revoked_by == 'admin'

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

            assert rv.status_code == 403  # Should require login

    def test_cross_user_token_access(self, app):
        """Test that users can't access each other's tokens"""
        # Reset and set up specific tokens for this test in DB
        PersonalAccessToken.objects().delete()
        student_token = "noj_pat_student_secret"
        teacher_token = "noj_pat_teacher_secret"

        PersonalAccessToken.add(
            pat_id='student_001',
            name='Student PAT',
            owner='student',
            hash_val=PersonalAccessToken.hash_token(student_token),
            scope=['read:courses', 'write:submissions'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30))

        PersonalAccessToken.add(
            pat_id='teacher_001',
            name='Teacher PAT',
            owner='teacher',
            hash_val=PersonalAccessToken.hash_token(teacher_token),
            scope=['grade:submissions'],
            due_time=datetime.now(timezone.utc) + timedelta(days=30))

        # Create separate client instances to avoid cookie interference
        from mongo import User

        # Student client
        client_student = app.test_client()
        client_student.set_cookie('piann',
                                  User('student').secret,
                                  domain='test.test')

        # Teacher client
        client_teacher = app.test_client()
        client_teacher.set_cookie('piann',
                                  User('teacher').secret,
                                  domain='test.test')

        # Student should only see their own token
        rv = client_student.get('/profile/api_token')
        json_data = rv.get_json()
        tokens = json_data['data']['Tokens']
        assert len(tokens) == 1
        assert tokens[0]['Owner'] == 'student'

        # Teacher should only see their own token
        rv = client_teacher.get('/profile/api_token')
        json_data = rv.get_json()
        tokens = json_data['data']['Tokens']
        assert len(tokens) == 1
        assert tokens[0]['Owner'] == 'teacher'

        # Student shouldn't be able to edit teacher's token
        rv = client_student.patch('/profile/api_token/edit/teacher_001',
                                  json={'data': {
                                      'Name': 'Hacked'
                                  }})
        assert rv.status_code == 403
