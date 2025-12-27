from tests.base_tester import BaseTester
from mongo import *
from mongo import engine
import secrets
import pytest


class TestCourseTeacherTAPermission(BaseTester):
    '''Test permissions for Global Teacher acting as Course TA'''

    def test_teacher_ta_course_access(self, forge_client):
        from tests import utils as test_utils

        # 1. Create a Global Teacher (who will be a TA)
        ta_username = f'ta_{secrets.token_hex(4)}'
        ta = test_utils.user.create_user(username=ta_username,
                                         password='password',
                                         role=int(engine.User.Role.TEACHER))

        # 2. Create a Course Owner (Teacher)
        owner_username = f'owner_{secrets.token_hex(4)}'
        test_utils.user.create_user(username=owner_username,
                                    password='password',
                                    role=int(engine.User.Role.TEACHER))

        # 3. Create Course
        course_name = f'Course_{secrets.token_hex(4)}'
        Course.add_course(course_name, owner_username)
        course = Course(course_name)

        # 4. Add the Global Teacher as a TA to the course
        course.add_user(ta)
        course.update(push__tas=ta.obj)
        course.reload()

        # 5. Login as the Global Teacher (TA)
        client_ta = forge_client(ta_username)

        # 6. Test Access to Course Code
        # Access: GET /course/<name>/code
        # Expectation: 200 (If they are TA, they should see it)
        print(
            f"\n[TEST] {ta_username} (Role: {ta.role}) accessing /course/{course_name}/code"
        )
        rv = client_ta.get(f'/course/{course_name}/code')
        try:
            assert rv.status_code == 200, f"Course Code Access Failed: {rv.status_code} {rv.get_json()}"
        except AssertionError as e:
            print(f"X Failed: {e}")
            raise

        # 7. Test Access to Login Records
        # Access: GET /course/<name>/login-records
        # Expectation: 200
        print(
            f"\n[TEST] {ta_username} accessing /course/{course_name}/login-records"
        )
        rv = client_ta.get(
            f'/course/{course_name}/login-records?offset=0&count=10')
        try:
            assert rv.status_code == 200, f"Login Records Access Failed: {rv.status_code} {rv.get_json()}"
        except AssertionError as e:
            print(f"X Failed: {e}")
            raise
