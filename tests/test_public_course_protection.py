from tests.base_tester import BaseTester
from mongo import Course, User
from mongo import engine
import pytest


class TestPublicCourseProtection(BaseTester):

    def test_rename_public_course(self, client_admin):
        # Ensure Public course exists
        if not Course('Public'):
            Course.add_course('Public', 'admin')

        # Try to rename Public to something else
        rv = client_admin.put('/course',
                              json={
                                  'course': 'Public',
                                  'newCourse': 'NotPublic',
                                  'teacher': 'admin'
                              })

        # Should fail with 400 or 403
        # Currently it might succeed (200), so we expect this to fail initially if we were asserting success of the protection.
        # But for TDD, we assert the expected behavior (Failure).
        # So this test will FAIL until we implement the fix.
        assert rv.status_code in [400, 403]

    def test_rename_to_public_course(self, client_admin):
        # Create a normal course
        course_name = 'MathProtectionTest'
        try:
            Course.add_course(course_name, 'admin')
        except:
            pass

        # Try to rename it to Public
        rv = client_admin.put('/course',
                              json={
                                  'course': course_name,
                                  'newCourse': 'Public',
                                  'teacher': 'admin'
                              })

        # Should fail
        assert rv.status_code in [400, 403]
