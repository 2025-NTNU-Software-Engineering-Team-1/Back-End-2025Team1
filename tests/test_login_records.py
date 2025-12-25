"""
Login Records API tests.
Tests for viewing login records for Admin, Teacher/TA, and users.
"""

import pytest
import secrets
from mongo import *
from mongo import engine
from mongo.course import Course
from tests import utils


@pytest.fixture(autouse=True)
def clean_db():
    utils.drop_db()


@pytest.fixture
def setup_users():
    """Create required users for testing."""
    # first_admin is created by default in some test setups
    # but we need to ensure teacher and student exist
    try:
        utils.user.create_user(username='first_admin', role=0)
    except engine.NotUniqueError:
        pass
    try:
        utils.user.create_user(username='teacher', role=1)
    except engine.NotUniqueError:
        pass
    try:
        utils.user.create_user(username='student', role=2)
    except engine.NotUniqueError:
        pass


class TestAdminLoginRecords:
    """Tests for Admin login records access."""

    def test_admin_can_access_all_records(self, forge_client):
        """Admin can access GET /login-records"""
        client = forge_client('first_admin')
        rv = client.get('/login-records')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'
        assert 'records' in rv.get_json()['data']
        assert 'total' in rv.get_json()['data']

    def test_admin_can_use_pagination(self, forge_client):
        """Admin can use offset and limit params"""
        client = forge_client('first_admin')
        rv = client.get('/login-records?offset=0&limit=10')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_admin_can_filter_by_username(self, forge_client):
        """Admin can filter records by username"""
        client = forge_client('first_admin')
        rv = client.get('/login-records?username=admin')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_admin_can_filter_by_success(self, forge_client):
        """Admin can filter records by success status"""
        client = forge_client('first_admin')
        rv = client.get('/login-records?success=true')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_admin_can_download_csv(self, forge_client):
        """Admin can download login records as CSV"""
        client = forge_client('first_admin')
        rv = client.get('/login-records/download')
        assert rv.status_code == 200
        assert 'text/csv' in rv.content_type

    def test_invalid_offset_returns_400(self, forge_client):
        """Invalid offset value returns 400 error"""
        client = forge_client('first_admin')
        rv = client.get('/login-records?offset=invalid')
        assert rv.status_code == 400, rv.get_json()
        assert 'must be integers' in rv.get_json()['message']


class TestNonAdminCannotAccessAllRecords:
    """Tests for non-admin access restrictions."""

    def test_teacher_cannot_access_all_records(self, forge_client,
                                               setup_users):
        """Teacher cannot access GET /login-records"""
        client = forge_client('teacher')
        rv = client.get('/login-records')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Permission denied. Admin only.'

    def test_student_cannot_access_all_records(self, forge_client,
                                               setup_users):
        """Student cannot access GET /login-records"""
        client = forge_client('student')
        rv = client.get('/login-records')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Permission denied. Admin only.'

    def test_teacher_cannot_download_all_records(self, forge_client,
                                                 setup_users):
        """Teacher cannot download all login records"""
        client = forge_client('teacher')
        rv = client.get('/login-records/download')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Permission denied. Admin only.'

    def test_student_cannot_download_all_records(self, forge_client,
                                                 setup_users):
        """Student cannot download all login records"""
        client = forge_client('student')
        rv = client.get('/login-records/download')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Permission denied. Admin only.'


class TestUserOwnRecords:
    """Tests for user accessing their own records."""

    def test_admin_can_access_own_records(self, forge_client):
        """Admin can access GET /login-records/me"""
        client = forge_client('first_admin')
        rv = client.get('/login-records/me')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'
        assert 'records' in rv.get_json()['data']
        assert 'total' in rv.get_json()['data']

    def test_teacher_can_access_own_records(self, forge_client, setup_users):
        """Teacher can access GET /login-records/me"""
        client = forge_client('teacher')
        rv = client.get('/login-records/me')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_student_can_access_own_records(self, forge_client, setup_users):
        """Student can access GET /login-records/me"""
        client = forge_client('student')
        rv = client.get('/login-records/me')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_user_can_use_pagination(self, forge_client):
        """User can use offset and limit params on own records"""
        client = forge_client('first_admin')
        rv = client.get('/login-records/me?offset=0&limit=10')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_user_can_download_own_csv(self, forge_client):
        """User can download their own login records as CSV"""
        client = forge_client('first_admin')
        rv = client.get('/login-records/me/download')
        assert rv.status_code == 200
        assert 'text/csv' in rv.content_type


class TestCourseLoginRecords:
    """Tests for course login records access."""

    def test_teacher_can_access_course_records(self, forge_client,
                                               setup_users):
        """Teacher can access their own course login records"""
        course_name = f'test_course_{secrets.token_hex(4)}'
        Course.add_course(course_name, 'teacher')

        client = forge_client('teacher')
        rv = client.get(f'/course/{course_name}/login-records')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'
        assert 'records' in rv.get_json()['data']
        assert 'total' in rv.get_json()['data']

    def test_admin_can_access_any_course_records(self, forge_client,
                                                 setup_users):
        """Admin can access any course login records"""
        course_name = f'test_course_{secrets.token_hex(4)}'
        Course.add_course(course_name, 'teacher')

        client = forge_client('first_admin')
        rv = client.get(f'/course/{course_name}/login-records')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['status'] == 'ok'

    def test_student_cannot_access_course_records(self, forge_client,
                                                  setup_users):
        """Student cannot access course login records"""
        course_name = f'test_course_{secrets.token_hex(4)}'
        Course.add_course(course_name, 'teacher')

        client = forge_client('student')
        rv = client.get(f'/course/{course_name}/login-records')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json(
        )['message'] == 'Permission denied. Teacher or TA only.'

    def test_teacher_can_download_course_csv(self, forge_client, setup_users):
        """Teacher can download course login records as CSV"""
        course_name = f'test_course_{secrets.token_hex(4)}'
        Course.add_course(course_name, 'teacher')

        client = forge_client('teacher')
        rv = client.get(f'/course/{course_name}/login-records/download')
        assert rv.status_code == 200
        assert 'text/csv' in rv.content_type

    def test_student_cannot_download_course_csv(self, forge_client,
                                                setup_users):
        """Student cannot download course login records"""
        course_name = f'test_course_{secrets.token_hex(4)}'
        Course.add_course(course_name, 'teacher')

        client = forge_client('student')
        rv = client.get(f'/course/{course_name}/login-records/download')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json(
        )['message'] == 'Permission denied. Teacher or TA only.'

    def test_nonexistent_course_returns_404(self, forge_client):
        """Accessing login records of nonexistent course returns 404"""
        client = forge_client('first_admin')
        rv = client.get('/course/nonexistent_course_xyz/login-records')
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'Course not found.'
