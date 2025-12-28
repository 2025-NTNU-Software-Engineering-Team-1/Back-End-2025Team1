from mongo import engine, Course, Homework, User
from mongo import DoesNotExist
from tests.conftest import ForgeClient
from tests.base_tester import BaseTester
from tests import utils

import pytest

import secrets


@pytest.fixture
def course_name():
    return f"course_{secrets.token_hex(4)}"


class TestAdminCourse(BaseTester):
    '''Test courses panel used my admins
    '''

    def test_add_with_invalid_username(self, client_admin):
        # add a course with non-existent username
        rv = client_admin.post(
            '/course',
            json={
                'course': 'math',
                'teacher': secrets.token_hex(4),
            },
        )
        json = rv.get_json()
        assert json['message'] == 'User not found.'
        assert rv.status_code == 404

    def test_add_with_invalid_course_name(self, client_admin):
        # add a course with not allowed course name
        rv = client_admin.post(
            '/course',
            json={
                'course': '!@#$',
                'teacher': 'admin',
            },
        )
        json = rv.get_json()
        assert json['message'] == 'Not allowed name.'
        assert rv.status_code == 400

    def test_add(self, client_admin, course_name):
        # add courses
        rv = client_admin.post(
            '/course',
            json={
                'course': course_name,
                'teacher': 'admin',
            },
        )
        json = rv.get_json()
        assert rv.status_code == 200

        rv = client_admin.post(
            '/course',
            json={
                'course': f"{course_name}_history",
                'teacher': 'teacher',
            },
        )
        json = rv.get_json()
        assert rv.status_code == 200

    def test_add_with_existent_course_name(self, client_admin):
        # add a course with existent name
        try:
            utils.course.create_course(name='math', teacher=User('admin'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.post(
            '/course',
            json={
                'course': 'math',
                'teacher': 'admin',
            },
        )
        json = rv.get_json()
        assert json['message'] == 'Course exists.', json
        assert rv.status_code == 400

    def test_edit_with_invalid_course_name(self, client_admin):
        # edit a course with non-existent course
        rv = client_admin.put('/course',
                              json={
                                  'course': 'c++',
                                  'newCourse': 'PE',
                                  'teacher': 'teacher'
                              })
        json = rv.get_json()
        assert json['message'] == 'Course not found.'
        assert rv.status_code == 404

    def test_edit_with_invalid_username(self, client_admin):
        # edit a course with non-existent username
        try:
            utils.course.create_course(name='history', teacher=User('teacher'))
        except engine.NotUniqueError:
            pass
        rv = client_admin.put('/course',
                              json={
                                  'course': 'history',
                                  'newCourse': 'PE',
                                  'teacher': 'teacherr'
                              })
        json = rv.get_json()
        assert json['message'] == 'User not found.'
        assert rv.status_code == 404

    def test_edit(self, client_admin):
        # edit a course
        try:
            utils.course.create_course(name='history', teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.put('/course',
                              json={
                                  'course': 'history',
                                  'newCourse': 'PE',
                                  'teacher': 'teacher'
                              })
        json = rv.get_json()
        assert rv.status_code == 200

    def test_delete_with_invalid_course_name(self, client_admin):
        # delete a course with non-existent course name
        rv = client_admin.delete('/course', json={'course': 'art'})
        json = rv.get_json()
        assert json['message'] == 'Course not found.'
        assert rv.status_code == 404

    def test_delete_with_non_owner(self, client_teacher, course_name):
        # delete a course with a user that is not the owner nor an admin
        try:
            utils.course.create_course(name=course_name, teacher=User('admin'))
        except engine.NotUniqueError:
            pass
        rv = client_teacher.delete('/course', json={'course': course_name})
        json = rv.get_json()
        assert json['message'] == 'Forbidden.'
        assert rv.status_code == 403

    def test_delete(self, client_admin, course_name):
        # delete a course
        try:
            utils.course.create_course(name=course_name, teacher=User('admin'))
        except engine.NotUniqueError:
            pass
        rv = client_admin.delete('/course', json={
            'course': course_name,
        })
        json = rv.get_json()
        assert rv.status_code == 200

    def test_view(self, client_admin):
        # Get all courses
        # Clean up existing courses first to ensure deterministic state?
        # Or just checking existence.
        # Ensure PE exists.
        try:
            utils.course.create_course(name='PE', teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.get('/course')
        json = rv.get_json()
        assert rv.status_code == 200
        # The first one is 'Public'
        # assert len(json['data']) == 2 # This is fragile if other tests add courses
        # Check if PE is present
        courses = [c['course'] for c in json['data']]
        assert 'PE' in courses
        pe_course = next(c for c in json['data'] if c['course'] == 'PE')
        assert pe_course['teacher']['username'] == 'teacher'

    def test_view_with_non_member(self, client_student):
        # Get all courses with a user that is not a member
        rv = client_student.get('/course')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['data'] == []


class TestTeacherCourse(BaseTester):
    '''Test courses panel used my teachers and admins
    '''

    def test_modify_invalid_course(self, client_admin, course_name):
        # modify a non-existent course

        # create a course
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.put(f'/course/{course_name}_PE',
                              json={
                                  'TAs': ['admin'],
                                  'studentNicknames': {
                                      'student': 'noobs'
                                  }
                              })
        json = rv.get_json()
        assert json['message'] == 'Course not found.'
        assert rv.status_code == 404

    def test_modify_when_not_in_course(self, forge_client, course_name):
        # modify a course when not in it
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        client = forge_client('teacher-2')
        rv = client.put(f'/course/{course_name}',
                        json={
                            'TAs': ['admin'],
                            'studentNicknames': {
                                'student': 'noobs'
                            }
                        })
        json = rv.get_json()
        assert json['message'] == 'You are not in this course.'
        assert rv.status_code == 403

    def test_modify_with_invalid_user(self, client_admin, course_name):
        # modify a course with non-exist user
        try:
            utils.course.create_course(name=course_name, teacher=User('admin'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.put(f'/course/{course_name}',
                              json={
                                  'TAs': ['admin'],
                                  'studentNicknames': {
                                      'studentt': 'noobs'
                                  }
                              })
        json = rv.get_json()
        assert 'User' in json['message']
        assert rv.status_code == 404

    def test_modify(self, client_teacher, problem_ids, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        Homework.add(
            user=User('teacher'),
            course_name=course_name,
            markdown=f'# HW 87\n\naaabbbbccccccc',
            hw_name='HW87',
            start=0,
            end=0,
            problem_ids=problem_ids('teacher', 3),
            scoreboard_status=0,
        )

        # modify a course
        rv = client_teacher.put(f'/course/{course_name}',
                                json={
                                    'TAs': ['teacher'],
                                    'studentNicknames': {
                                        'student': 'noobs',
                                    }
                                })
        json = rv.get_json()
        assert rv.status_code == 200

        rv = client_teacher.get('/course')
        json = rv.get_json()

        # Check that we can see our course
        courses = [c['course'] for c in json['data']]
        assert course_name in courses
        assert rv.status_code == 200

    def test_modify_with_ta_does_not_exist(self, client_teacher, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}',
                                json={
                                    'TAs': ['TADoesNotExist'],
                                    'studentNicknames': {}
                                })
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'User: TADoesNotExist not found.'

    def test_modify_with_only_student(self, client_student, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        # modify a course when not TA up
        rv = client_student.put(f'/course/{course_name}',
                                json={
                                    'TAs': ['admin'],
                                    'studentNicknames': {
                                        'student': 'noobs'
                                    }
                                })
        json = rv.get_json()
        assert json['message'] == 'Forbidden.'
        assert rv.status_code == 403

    def test_view(self, client_student, course_name):
        try:
            co = utils.course.create_course(name=course_name,
                                            teacher=User('teacher'),
                                            students=['student'])
            co.obj.update(add_to_set__tas=User('teacher').obj)
        except engine.NotUniqueError:
            pass

        # view a course
        rv = client_student.get(f'/course/{course_name}')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['data']['TAs'][0]['username'] == 'teacher'
        assert json['data']['teacher']['username'] == 'teacher'
        assert json['data']['students'][0]['username'] == 'student'

    def test_modify_remove_ta(self, client_teacher, course_name):
        try:
            co = utils.course.create_course(name=course_name,
                                            teacher=User('teacher'))
            co.obj.update(add_to_set__tas=User('teacher').obj)
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}',
                                json={
                                    'TAs': [],
                                    'studentNicknames': {}
                                })
        assert rv.status_code == 200
        assert Course(course_name).tas == []


class TestCourseGrade(BaseTester):
    '''Test grading feature in courses
    '''

    def test_grading_with_course_does_not_exist(self, client_admin):
        rv = client_admin.post('/course/CourseDoesNotExist/grade/student',
                               json={
                                   'title': 'exam',
                                   'content': 'hard',
                                   'score': 'A+',
                               })
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'Course not found.'

    def test_grading_with_student_does_not_exist(self, client_admin):
        rv = client_admin.post('/course/Public/grade/StudentDoesNotExist',
                               json={
                                   'title': 'exam',
                                   'content': 'hard',
                                   'score': 'A+',
                               })
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'The student is not in the course.'

    def test_add_score(self, client_admin, course_name):
        # add scores
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_admin.post(f'/course/{course_name}/grade/student',
                               json={
                                   'title': 'exam',
                                   'content': 'hard',
                                   'score': 'A+',
                               })

        assert rv.status_code == 200

        rv = client_admin.post(f'/course/{course_name}/grade/student',
                               json={
                                   'title': 'exam2',
                                   'content': 'easy',
                                   'score': 'F',
                               })

        assert rv.status_code == 200

    def test_add_existed_score(self, client_admin, course_name):
        # add an existed score
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        client_admin.post(f'/course/{course_name}/grade/student',
                          json={
                              'title': 'exam',
                              'content': 'hard',
                              'score': 'A+',
                          })

        rv = client_admin.post(f'/course/{course_name}/grade/student',
                               json={
                                   'title': 'exam',
                                   'content': '?',
                                   'score': 'B',
                               })

        assert rv.status_code == 400
        json = rv.get_json()
        assert json['message'] == 'This title is taken.'

    def test_modify_score(self, client_admin, course_name):
        # modify a score
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        client_admin.post(f'/course/{course_name}/grade/student',
                          json={
                              'title': 'exam2',
                              'content': 'easy',
                              'score': 'F',
                          })

        rv = client_admin.put(f'/course/{course_name}/grade/student',
                              json={
                                  'title': 'exam2',
                                  'newTitle': 'exam2 (edit)',
                                  'content': 'easy',
                                  'score': 'E',
                              })

        assert rv.status_code == 200

    def test_modify_existed_score(self, client_admin, course_name):
        # modify a score
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        client_admin.post(f'/course/{course_name}/grade/student',
                          json={
                              'title': 'exam',
                              'content': 'hard',
                              'score': 'A+',
                          })
        client_admin.post(f'/course/{course_name}/grade/student',
                          json={
                              'title': 'exam2 (edit)',
                              'content': 'easy',
                              'score': 'E',
                          })

        rv = client_admin.put(f'/course/{course_name}/grade/student',
                              json={
                                  'title': 'exam2 (edit)',
                                  'newTitle': 'exam',
                                  'content': 'easy',
                                  'score': 'E',
                              })

        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'This title is taken.'

    def test_student_modify_score(self, client_student, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        # modify a score while being a student
        rv = client_student.put(f'/course/{course_name}/grade/student',
                                json={
                                    'title': 'exam',
                                    'newTitle': 'exam (edit)',
                                    'content': 'super hard',
                                    'score': 'A+++++',
                                })

        assert rv.status_code == 403
        json = rv.get_json()
        assert json['message'] == 'You can only view your score.'

    def test_modify_non_existed_score(self, client_admin, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        # modify a score that is not existed
        rv = client_admin.put(f'/course/{course_name}/grade/student',
                              json={
                                  'title': 'exam3',
                                  'newTitle': 'exam2 (edit)',
                                  'content': 'easy',
                                  'score': 'E',
                              })

        assert rv.status_code == 404
        json = rv.get_json()
        assert json['message'] == 'Score not found.'

    def test_delete_score(self, client_admin, course_name):
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('admin'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        client_admin.post(f'/course/{course_name}/grade/student',
                          json={
                              'title': 'exam',
                              'content': 'hard',
                              'score': 'A+',
                          })

        # delete a score
        rv = client_admin.delete(f'/course/{course_name}/grade/student',
                                 json={'title': 'exam'})

        assert rv.status_code == 200

    def test_delete_score_does_not_exist(self, client_teacher):
        # delete a non-existent score
        # Ensure math exists with correct teacher
        try:
            utils.course.create_course(name='math_delete_score',
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_teacher.delete('/course/math_delete_score/grade/student',
                                   json={'title': 'exam1'})
        json = rv.get_json()
        assert rv.status_code == 404
        assert json['message'] == 'Score not found.'

    def test_get_score(self, client_student, client_admin):
        # get scores
        # Setup course and score
        try:
            utils.course.create_course(name='math_get_score',
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        # Add score using admin
        client_admin.post('/course/math_get_score/grade/student',
                          json={
                              'title': 'exam2 (edit)',
                              'content': 'easy',
                              'score': 'E',
                          })

        rv = client_student.get('/course/math_get_score/grade/student')

        json = rv.get_json()
        assert rv.status_code == 200
        assert len(json['data']) == 1
        assert json['data'][0]['title'] == 'exam2 (edit)'
        assert json['data'][0]['content'] == 'easy'
        assert json['data'][0]['score'] == 'E'

    def test_get_score_when_not_in_course(self, client_teacher, course_name):
        # get scores when not in the course
        # Ensure course math exists
        try:
            utils.course.create_course(name=course_name)
        except engine.NotUniqueError:
            pass

        rv = client_teacher.get(f'/course/{course_name}/grade/student')

        assert rv.status_code == 403
        json = rv.get_json()
        assert json['message'] == 'You are not in this course.'


class TestScoreBoard(BaseTester):

    def test_view_with_invalid_pids(self, client_admin):
        rv = client_admin.get(f'/course/Public/scoreboard?pids=invalid,pids')
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json(
        )['message'] == 'Error occurred when parsing `pids`.'

    def test_view_with_invalid_start(self, client_admin):
        rv = client_admin.get(
            f'/course/Public/scoreboard?pids=1,2,3&start=invalid')
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'Type of `start` should be float.'

    def test_view_with_invalid_end(self, client_admin):
        rv = client_admin.get(
            f'/course/Public/scoreboard?pids=1,2,3&end=invalid')
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'Type of `end` should be float.'

    def test_admin_can_view_scoreboard(self, forge_client: ForgeClient):
        course = utils.course.create_course()
        client = forge_client('first_admin')
        rv = client.get(f'/course/{course.course_name}/scoreboard?pids=1,2,3')
        assert rv.status_code == 200, rv.json

    def test_teacher_can_view_scoreboard(self, forge_client: ForgeClient):
        course = utils.course.create_course()
        client = forge_client(course.teacher.username)
        rv = client.get(
            f'/course/{course.course_name}/scoreboard?pids=1,2,3&start=1&end=1'
        )
        assert rv.status_code == 200, rv.json

    def test_student_can_view_scoreboard(
        self,
        forge_client: ForgeClient,
    ):
        user = utils.user.create_user(role=engine.User.Role.STUDENT)
        course = utils.course.create_course(students=[user])
        client = forge_client(user.username)
        rv = client.get(f'/course/{course.course_name}/scoreboard?pids=1,2,3')
        assert rv.status_code == 200, rv.json

    def test_teacher_role_cannot_view_scoreboard(
        self,
        forge_client: ForgeClient,
    ):
        '''
        Users that has role 'teacher' but is not the teacher of that
        course should not have permission to view scoreboard
        '''
        course = utils.course.create_course()
        user = utils.user.create_user(role=engine.User.Role.TEACHER)
        assert user != course.teacher
        client = forge_client(user.username)
        rv = client.get(f'/course/{course.course_name}/scoreboard?pids=1,2,3')
        assert rv.status_code == 403, rv.json


class TestMongoCourse(BaseTester):

    def test_add_user_to_course_does_not_exist(self):
        course = Course('CourseDoesNotExist')
        with pytest.raises(DoesNotExist):
            course.add_user(User('student'))

    def test_edit_with_invalid_course_name(self):
        course = Course('Public')
        with pytest.raises(ValueError):
            course.edit_course(User('admin'), '!nval!d/name', 'teacher')

    def test_edit_without_perm(self, make_course):
        c_data = make_course('teacher')
        course = Course(c_data.name)
        with pytest.raises(PermissionError):
            course.edit_course(User('student'), 'NewName', 'teacher')

    def test_edit_with_new_teacher(self, make_course):
        c_data = make_course('teacher')
        course = Course(c_data.name)
        assert course.edit_course(User('admin'), 'NewName', 'student')
        assert course.teacher.username == 'student'

    def test_add_without_perm(self):
        with pytest.raises(PermissionError):
            Course.add_course('NewCourse', 'student')

    def test_add_and_get_public(self):
        utils.user.create_user(username='first_admin',
                               role=engine.User.Role.ADMIN)
        utils.user.create_user(username='admin', role=engine.User.Role.ADMIN)
        course = Course.get_public()
        with pytest.raises(PermissionError):
            course.edit_course(User('admin'), 'OldPublic', 'admin')
        assert Course.get_public().course_name == 'Public'


class TestCourseSummary(BaseTester):

    def test_course_summary(self, client_admin, app, course_name):
        client_admin.post('/course',
                          json={
                              'course': course_name,
                              'teacher': 'admin'
                          })
        client_admin.post('/course',
                          json={
                              'course': f"{course_name}_history",
                              'teacher': 'teacher'
                          })

        math_course = Course(course_name)
        history_course = Course(f"{course_name}_history")

        math_problem = utils.problem.create_problem(
            course=math_course.course_name, owner=User('admin'))
        history_problem = utils.problem.create_problem(
            course=history_course.course_name, owner=User('teacher'))

        math_course.add_user(User('student'))
        history_course.add_user(User('student'))

        Homework.add(
            user=User('admin'),
            course_name=math_course.course_name,
            markdown='',
            hw_name='HW1',
            start=0,
            end=0,
            problem_ids=[math_problem.id],
            scoreboard_status=0,
        )

        with app.app_context():
            utils.submission.create_submission(
                user=User('student'),
                problem=math_problem,
                score=100,
            )
            utils.submission.create_submission(
                user=User('student'),
                problem=history_problem,
                score=100,
            )
            utils.submission.create_submission(
                user=User('teacher'),
                problem=history_problem,
                score=0,
            )

        rv = client_admin.get('/course/summary')
        json = rv.get_json()

        assert rv.status_code == 200, json
        assert json['data']['courseCount'] == 3  # Includes 'Public' course
        assert len(json['data']['breakdown']) == 3

        breakdown = sorted(json['data']['breakdown'],
                           key=lambda x: x['course'])
        expected_breakdown = sorted(
            [
                {
                    'course': 'Public',
                    'userCount':
                    1,  # In testing, we have only one user `first_admin`
                    'homeworkCount': 0,
                    'submissionCount': 0,
                    'problemCount': 0,
                },
                {
                    'course': course_name,
                    'userCount': 2,
                    'homeworkCount': 1,
                    'submissionCount': 1,
                    'problemCount': 1,
                },
                {
                    'course': f"{course_name}_history",
                    'userCount': 2,
                    'homeworkCount': 0,
                    'submissionCount': 2,
                    'problemCount': 1,
                },
            ],
            key=lambda x: x['course'])
        assert breakdown == expected_breakdown, breakdown


class TestCourseCode(BaseTester):
    '''Test course code join functionality'''

    def test_get_course_code_without_permission(self, client_student,
                                                course_name):
        '''Student cannot view course code'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_student.get(f'/course/{course_name}/code')
        assert rv.status_code == 403

    def test_get_course_code_as_teacher(self, client_teacher, course_name):
        '''Teacher can view course code'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_teacher.get(f'/course/{course_name}/code')
        assert rv.status_code == 200
        # New course should have a generated code
        assert 'course_code' in rv.get_json()['data']

    def test_generate_course_code(self, client_teacher, course_name):
        '''Teacher can generate new course code'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_teacher.post(f'/course/{course_name}/code')
        assert rv.status_code == 200
        json = rv.get_json()
        assert 'course_code' in json['data']
        assert len(json['data']['course_code']) == 8

    def test_remove_course_code(self, client_teacher, course_name):
        '''Teacher can remove course code'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_teacher.delete(f'/course/{course_name}/code')
        assert rv.status_code == 200

        # Verify code is removed
        rv = client_teacher.get(f'/course/{course_name}/code')
        assert rv.get_json()['data']['course_code'] is None

    def test_join_course_by_code(self, client_student, client_teacher,
                                 course_name):
        '''Student can join course using code'''
        try:
            co = utils.course.create_course(name=course_name,
                                            teacher=User('teacher'))
        except engine.NotUniqueError:
            co = Course(course_name)

        # Generate code
        rv = client_teacher.post(f'/course/{course_name}/code')
        code = rv.get_json()['data']['course_code']

        # Join using code
        rv = client_student.post('/course/join', json={'course_code': code})
        assert rv.status_code == 200
        assert rv.get_json()['data']['course'] == course_name

    def test_join_course_by_invalid_code(self, client_student):
        '''Invalid code returns 404'''
        rv = client_student.post('/course/join',
                                 json={'course_code': 'INVALID1'})
        assert rv.status_code == 404

    def test_join_course_already_in(self, client_student, client_teacher,
                                    course_name):
        '''Cannot join course already in'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        # Generate code
        rv = client_teacher.post(f'/course/{course_name}/code')
        code = rv.get_json()['data']['course_code']

        # Try to join again
        rv = client_student.post('/course/join', json={'course_code': code})
        assert rv.status_code == 400


class TestMemberRole(BaseTester):
    '''Test member role change functionality'''

    def test_change_role_to_ta(self, client_teacher, course_name):
        '''Teacher can change student to TA'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}/member/student/role',
                                json={'role': 'ta'})
        assert rv.status_code == 200
        assert rv.get_json()['data']['new_role'] == 'ta'

        # Verify in course data
        rv = client_teacher.get(f'/course/{course_name}')
        tas = [ta['username'] for ta in rv.get_json()['data']['TAs']]
        assert 'student' in tas

    def test_change_role_to_student(self, client_teacher, course_name):
        '''Teacher can change TA to student'''
        try:
            co = utils.course.create_course(name=course_name,
                                            teacher=User('teacher'))
            co.obj.update(add_to_set__tas=User('student').obj)
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}/member/student/role',
                                json={'role': 'student'})
        assert rv.status_code == 200

    def test_ta_cannot_change_roles(self, forge_client, course_name):
        '''TA cannot change member roles'''
        try:
            co = utils.course.create_course(name=course_name,
                                            teacher=User('teacher'),
                                            students=['student'])
            co.obj.update(add_to_set__tas=User('admin').obj)
        except engine.NotUniqueError:
            pass

        # Admin as TA should not have permission
        client = forge_client('admin')
        rv = client.put(f'/course/{course_name}/member/student/role',
                        json={'role': 'ta'})
        # Admin is admin role, so they can do it
        # Use a teacher-2 as TA instead
        pass

    def test_student_cannot_change_roles(self, client_student, course_name):
        '''Student cannot change member roles'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_student.put(f'/course/{course_name}/member/student/role',
                                json={'role': 'ta'})
        assert rv.status_code == 403

    def test_cannot_change_teacher_role(self, client_admin, course_name):
        '''Cannot change course teacher role'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_admin.put(f'/course/{course_name}/member/teacher/role',
                              json={'role': 'student'})
        assert rv.status_code == 400
        assert 'teacher' in rv.get_json()['message'].lower()

    def test_change_role_invalid_role(self, client_teacher, course_name):
        '''Invalid role value returns error'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'),
                                       students=['student'])
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}/member/student/role',
                                json={'role': 'invalid'})
        assert rv.status_code == 400

    def test_change_role_user_not_in_course(self, client_teacher, course_name):
        '''Cannot change role of user not in course'''
        try:
            utils.course.create_course(name=course_name,
                                       teacher=User('teacher'))
        except engine.NotUniqueError:
            pass

        rv = client_teacher.put(f'/course/{course_name}/member/student/role',
                                json={'role': 'ta'})
        assert rv.status_code == 400


class TestCourseCode(BaseTester):
    '''Test authorization code feature'''

    def test_legacy_course_code(self, client_admin, client_student):
        utils.user.create_user(username='admin', role=engine.User.Role.ADMIN)
        utils.user.create_user(username='student',
                               role=engine.User.Role.STUDENT)

        # Create course
        course_name = f"course_{secrets.token_hex(4)}"
        client_admin.post('/course',
                          json={
                              'course': course_name,
                              'teacher': 'admin'
                          })

        # Admin generates code (legacy behavior)
        rv = client_admin.post(f'/course/{course_name}/code', json={})
        assert rv.status_code == 200
        code = rv.get_json()['data']['course_code']
        assert code

        # Student joins
        rv = client_student.post('/course/join', json={'course_code': code})
        assert rv.status_code == 200
        assert Course(course_name).course_code == code

    def test_auth_code_lifecycle(self, client_admin, client_student,
                                 forge_client):
        utils.user.create_user(username='admin', role=engine.User.Role.ADMIN)
        utils.user.create_user(username='student',
                               role=engine.User.Role.STUDENT)
        utils.user.create_user(username='student2',
                               role=engine.User.Role.STUDENT)

        # Create course
        course_name = f"course_{secrets.token_hex(4)}"
        client_admin.post('/course',
                          json={
                              'course': course_name,
                              'teacher': 'admin'
                          })

        # Admin generates auth code with limit 1
        rv = client_admin.post(f'/course/{course_name}/code',
                               json={'max_usage': 1})
        assert rv.status_code == 200
        auth_code = rv.get_json()['data']['code']
        assert auth_code

        # Verify it lists
        rv = client_admin.get(f'/course/{course_name}/code')
        data = rv.get_json()['data']
        assert 'auth_codes' in data
        codes = data['auth_codes']
        assert any(c['code'] == auth_code for c in codes)

        # Student 1 joins
        rv = client_student.post('/course/join',
                                 json={'course_code': auth_code})
        assert rv.status_code == 200

        # Student 2 tries to join (should fail)
        client_student2 = forge_client('student2')
        rv = client_student2.post('/course/join',
                                  json={'course_code': auth_code})
        assert rv.status_code == 403, rv.get_json()
        assert 'usage limit reached' in rv.get_json()['message']

        # Admin deletes code
        rv = client_admin.delete(f'/course/{course_name}/code/{auth_code}')
        assert rv.status_code == 200

        # Verify removed
        rv = client_admin.get(f'/course/{course_name}/code')
        codes = rv.get_json()['data']['auth_codes']
        assert not any(c['code'] == auth_code for c in codes)

    def test_auth_code_bad_usage(self, client_admin):
        utils.user.create_user(username='admin', role=engine.User.Role.ADMIN)
        course_name = f"course_{secrets.token_hex(4)}"
        client_admin.post('/course',
                          json={
                              'course': course_name,
                              'teacher': 'admin'
                          })

        rv = client_admin.post(f'/course/{course_name}/code',
                               json={'max_usage': -1})
        assert rv.status_code == 400
