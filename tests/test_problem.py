import io
import json
import hashlib
import zipfile
import pytest
from zipfile import ZipFile
from tests.base_tester import BaseTester, random_string
from mongo import *
from mongo.problem import Problem
from tests import utils
from mongo.utils import MinioClient


def get_file(file):
    with open("./tests/problem_test_case/" + file, 'rb') as f:
        return {'case': (io.BytesIO(f.read()), "test_case.zip")}


def description_dict():
    return {
        'description': 'Test description.',
        'input': '',
        'output': '',
        'hint': '',
        'sampleInput': [],
        'sampleOutput': []
    }


def assert_basic_problem_config(config):
    assert config['acceptedFormat'] == 'code'
    assert config['aiVTuber'] is False
    assert config['artifactCollection'] == []
    assert config['compilation'] is False
    assert config['customChecker'] is False
    assert config['executionMode'] == 'general'
    assert config['allowRead'] is False
    assert config['allowWrite'] is False
    assert config['scoringScript'] == {'custom': False}
    assert config['teacherFirst'] is False
    assert config['trialMode'] is False
    assert config['maxNumberOfTrial'] == 0
    assert config.get('staticAnalys', {}).get('custom') is False


def advanced_config_payload():
    return {
        'trialMode': True,
        'aiVTuber': True,
        'aiVTuberMaxToken': 3,
        'aiVTuberMode': 'guided',
        'acceptedFormat': 'code',
        'maxStudentZipSizeMB': 50,
        'networkAccessRestriction': {
            'enabled': True,
            'firewallExtranet': {
                'enabled': True,
                'whitelist': ['192.168.1.1'],
                'blacklist': [],
            },
            'connectWithLocal': {
                'enabled': True,
                'whitelist': ['192.168.2.2'],
                'blacklist': [],
                'localServiceZip': None,
            },
        },
        'artifactCollection': ['zip', 'compiledBinary'],
    }


def advanced_pipeline_payload():
    return {
        'allowRead': True,
        'allowWrite': True,
        'executionMode': 'general',
        'customChecker': False,
        'teacherFirst': False,
        'staticAnalysis': {
            'libraryRestrictions': {
                'enabled': True,
                'whitelist': {
                    'syntax': ['recursive'],
                    'imports': [],
                    'headers': [],
                    'functions': [],
                },
                'blacklist': {
                    'syntax': [],
                    'imports': [],
                    'headers': [],
                    'functions': [],
                },
            },
        },
        'scoringScript': {
            'custom': False,
        },
    }


@pytest.mark.usefixtures("setup_minio")
class TestProblem(BaseTester):
    # add a problem which status value is invalid (POST /problem/manage)
    def test_add_with_invalid_value(self, client_admin):
        # create courses
        utils.course.create_course(teacher='admin', name='math')
        utils.course.create_course(teacher='admin', name='English')
        client_admin.put(
            '/course/math',
            json={
                'TAs': ['admin'],
                'studentNicknames': {
                    'student': 'noobs'
                }
            },
        )

        request_json_with_invalid_json = {
            'courses': ['math'],
            'status': 2,  # Invalid value
            'type': 0,
            'problemName': 'Test problem name',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.post(
            '/problem/manage',
            json=request_json_with_invalid_json,
        )
        json = rv.get_json()
        assert rv.status_code == 400
        assert json['status'] == 'err'
        assert json['message'] == 'Invalid or missing arguments.'

    # add a problem which problem name is misssing (POST /problem/manage)
    def test_add_with_missing_argument(self, client_admin):
        try:
            utils.course.create_course(name='math', teacher=User('admin'))
        except engine.NotUniqueError:
            pass
        request_json_with_missing_argument = {
            'courses': ['math'],
            'status': 1,
            'type': 0,
            #  'problem_name': 'Test problem name',	# missing argument
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.post('/problem/manage',
                               json=request_json_with_missing_argument)
        json = rv.get_json()
        assert json['message'] == 'Invalid or missing arguments.'
        assert rv.status_code == 400
        assert json['status'] == 'err'

    # add a offline problem
    def test_add_offline_problem(self, client_admin):
        # Create course first
        utils.course.create_course(teacher='admin', name='English')

        request_json = {
            'courses': ['English'],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        json = rv.get_json()
        id = json['data']['problemId']

        rv = client_admin.put(
            f'/problem/manage/{id}',
            data=get_file('default/test_case.zip'),
        )
        json = rv.get_json()
        assert rv.status_code == 200, json
        assert json['status'] == 'ok'
        assert json['message'] == 'Success.'

    # add a online problem
    def test_add_online_problem(self, client_admin):
        # Create course first
        utils.course.create_course(teacher='admin', name='math')

        request_json = {
            'courses': ['math'],
            'status': 0,
            'type': 0,
            'problemName': 'Online problem',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        json = rv.get_json()
        id = json['data']['problemId']

        rv = client_admin.put(
            f'/problem/manage/{id}',
            data=get_file('default/test_case.zip'),
        )
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Success.'

    def test_add_problem_with_extended_config_schema(self, client_admin):
        utils.course.create_course(teacher='admin', name='Public')
        request_json = {
            'problemName': 'schema-test',
            'description': description_dict(),
            'courses': ['Public'],
            'tags': [],
            'allowedLanguage': 7,
            'quota': 5,
            'type': 0,
            'status': 0,
            'testCaseInfo': {
                'language':
                0,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 50,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }, {
                    'caseCount': 1,
                    'taskScore': 50,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            },
            'canViewStdout': False,
            'defaultCode': '',
            'config': advanced_config_payload(),
            'pipeline': advanced_pipeline_payload(),
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        assert rv.status_code == 200, rv.get_json()
        pid = rv.get_json()['data']['problemId']
        problem = Problem(pid)
        config = problem.obj.config
        assert config['aiVTuber'] is True
        assert config['aiVTuberMaxToken'] == 3
        assert config['artifactCollection'] == ['zip', 'compiledBinary']
        assert config['allowRead'] is True
        assert config['allowWrite'] is True
        assert config['executionMode'] == 'general'
        assert config['staticAnalysis']['networkAccessRestriction'][
            'firewallExtranet']['whitelist'] == ['192.168.1.1']
        assert config['staticAnalysis']['networkAccessRestriction'][
            'connectWithLocal']['whitelist'] == ['192.168.2.2']
        assert config['scoringScript'] == {'custom': False}
        assert config['trialMode'] is True

    def test_add_problem_with_empty_course_list(self, client_admin):
        request_json = {
            'courses': [],
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'No course provided'

    def test_add_problem_with_course_does_not_exist(self, client_admin):
        request_json = {
            'courses': ['CourseDoesNotExist'],
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'Course not found'

    def test_get_problem_list_with_nan_offest(self, client_admin):
        rv = client_admin.get('/problem?offset=BadOffset')
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'offset and count must be integer!'

    def test_get_problem_list_with_negtive_offest(self, client_admin):
        rv = client_admin.get('/problem?offset=-1')
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'invalid offset'

    # admin get problem list (GET /problem)
    def test_admin_get_problem_list(self, client_admin):
        utils.course.create_course(teacher='admin', name='AdminListCourse')
        offline_prob = utils.problem.create_problem(
            name='admin-list-offline',
            course='AdminListCourse',
            owner='admin',
            status=1,
            type=0,
        )
        online_prob = utils.problem.create_problem(
            name='admin-list-online',
            course='AdminListCourse',
            owner='admin',
            status=0,
            type=0,
        )
        rv = client_admin.get('/problem?offset=0&count=5')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Success.'
        data_map = {item['problemId']: item for item in json['data']}
        offline = data_map.get(offline_prob.id)
        online = data_map.get(online_prob.id)
        assert offline is not None
        assert online is not None
        assert offline['status'] == 1
        assert online['status'] == 0
        assert offline['quota'] == -1
        assert online['quota'] == -1

    # admin get problem list with a filter (GET /problem)
    def test_admin_get_problem_list_with_filter(self, client_admin):
        utils.course.create_course(teacher='admin', name='English')
        prob = utils.problem.create_problem(
            name='admin-filter-offline',
            course='English',
            owner='admin',
            status=1,
            type=0,
        )
        rv = client_admin.get('/problem?offset=0&count=5&course=English')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Success.'
        assert any(item['problemId'] == prob.id for item in json['data'])

    def test_admin_get_problem_list_with_unexist_params(self, client_admin):
        # unexisted course
        rv, rv_json, rv_data = self.request(
            client_admin,
            'get',
            '/problem?offset=0&count=-1&course=Programming',
        )
        assert rv.status_code == 200
        assert len(rv_data) == 0
        # unexisted tags
        rv, rv_json, rv_data = self.request(
            client_admin,
            'get',
            '/problem?offset=0&count=-1&tags=yo',
        )
        assert rv.status_code == 200
        assert len(rv_data) == 0

    # student get problem list (GET /problem)
    def test_student_get_problem_list(self, client_student):
        utils.course.create_course(teacher='admin',
                                   name='StudentListCourse',
                                   students=['student'])
        prob = utils.problem.create_problem(
            name='student-list-online',
            course='StudentListCourse',
            owner='admin',
            status=0,
            type=0,
        )
        rv = client_student.get('/problem?offset=0&count=5')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Success.'
        online = next(
            (item for item in json['data'] if item['problemId'] == prob.id),
            None)
        assert online is not None
        assert online['status'] == 0

    def test_view_problem_from_invalid_ip(self, client_student, monkeypatch):
        from model.problem import Problem
        utils.course.create_course(teacher='admin',
                                   name='math',
                                   students=['student'])
        prob = utils.problem.create_problem(course='math', owner='admin')

        monkeypatch.setattr(Problem, 'is_valid_ip', lambda *_: False)
        rv = client_student.get(f'/problem/{prob.id}')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Invalid IP address.'

    def test_view_template_problem(self, client_admin):
        utils.course.create_course(teacher='admin', name='math')
        request_json = {
            'courses': ['math'],
            'status': 0,
            'type': 1,
            'problemName': 'Template problem',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                'This is a fill in template.',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.post('/problem/manage', json=request_json)
        assert rv.status_code == 200
        pid = rv.get_json()['data']['problemId']
        rv = client_admin.get(f'/problem/{pid}')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json(
        )['data']['fillInTemplate'] == 'This is a fill in template.'


def test_asset_checksum_ok(client_admin, problem_ids):
    pid = problem_ids('teacher', 1, False)[0]
    problem = Problem(pid)
    mc = MinioClient()
    data = b'hello-checker'
    object_name = f'problem/{pid}/checker/custom_checker.py'
    mc.upload_file_object(io.BytesIO(data),
                          object_name=object_name,
                          length=len(data))
    problem.update(config={'assetPaths': {'checker': object_name}})
    token = Submission.config().sandbox_instances[0].token
    rv = client_admin.get(
        f'/problem/{pid}/asset-checksum',
        query_string={
            'token': token,
            'assetType': 'checker',
        },
    )
    assert rv.status_code == 200
    checksum = rv.get_json()['data']['checksum']
    assert checksum == hashlib.md5(data).hexdigest()


def test_asset_checksum_missing_asset_returns_none(client_admin, problem_ids):
    pid = problem_ids('teacher', 1, False)[0]
    token = Submission.config().sandbox_instances[0].token
    rv = client_admin.get(
        f'/problem/{pid}/asset-checksum',
        query_string={
            'token': token,
            'assetType': 'checker',
        },
    )
    assert rv.status_code == 200
    assert rv.get_json()['data']['checksum'] is None


def test_asset_checksum_invalid_type(client_admin, problem_ids):
    pid = problem_ids('teacher', 1, False)[0]
    token = Submission.config().sandbox_instances[0].token
    rv = client_admin.get(
        f'/problem/{pid}/asset-checksum',
        query_string={
            'token': token,
            'assetType': 'unknown_type',
        },
    )
    assert rv.status_code == 400

    # admin view offline problem (GET /problem/<problem_id>)
    def test_admin_view_offline_problem(self, client_admin):
        utils.course.create_course(teacher='admin', name='English')
        test_case_info = utils.problem.create_test_case_info(
            language=1,
            task_len=1,
            case_count_range=(1, 1),
            memory_limit_range=(1000, 1000),
            time_limit_range=(1000, 1000),
        )
        problem = utils.problem.create_problem(
            name='Offline problem',
            course='English',
            owner='admin',
            status=1,
            type=0,
            test_case_info=test_case_info,
        )
        rv = client_admin.get(f'/problem/{problem.id}')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Problem can view.'
        data = json['data']
        assert data['problemName'] == 'Offline problem'
        assert data['status'] == 1
        assert data['courses'] == ['English']
        assert data['allowedLanguage'] == 7
        assert data['canViewStdout'] is False
        assert data['testCase'][0]['taskScore'] == 100
        assert data['quota'] == -1
        assert data['submitter'] == 0
        assert data['defaultCode'] == ''
        assert_basic_problem_config(data['config'])

    # student view offline problem (GET /problem/<problem_id>)
    def test_student_view_offline_problem(self, client_student):
        rv = client_student.get('/problem/3')
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'

    # student view online problem (GET /problem/<problem_id>)
    def test_student_view_online_problem(self, client_student):
        utils.course.create_course(teacher='admin',
                                   name='math',
                                   students=['student'])
        test_case_info = utils.problem.create_test_case_info(
            language=1,
            task_len=1,
            case_count_range=(1, 1),
            memory_limit_range=(1000, 1000),
            time_limit_range=(1000, 1000),
        )
        problem = utils.problem.create_problem(
            name='Online problem',
            course='math',
            owner='admin',
            status=0,
            type=0,
            test_case_info=test_case_info,
        )
        rv = client_student.get(f'/problem/{problem.id}')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert json['message'] == 'Problem can view.'
        data = json['data']
        assert data['problemName'] == 'Online problem'
        assert data['status'] == 0
        assert data['courses'] == ['math']
        assert data['allowedLanguage'] == 7
        assert data['testCase'][0]['taskScore'] == 100
        assert data['quota'] == -1
        assert data['submitter'] == 0
        assert_basic_problem_config(data['config'])

    # student view problem not exist (GET /problem/<problem_id>)
    def test_student_view_problem_not_exist(self, client_student):
        rv = client_student.get('/problem/0')
        json = rv.get_json()
        assert rv.status_code == 404
        assert json['status'] == 'err'

    # student change the name of a problem (PUT /problem/manage/<problem_id>)
    def test_student_edit_problem(self, client_student):
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem (edit)',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            },
        }
        rv = client_student.put('/problem/manage/1', json=request_json)
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'
        assert json['message'] == 'Insufficient Permissions'

    # non-owner teacher change the name of a problem (PUT /problem/manage/<problem_id>)
    def test_teacher_not_owner_edit_problem(self, client_teacher):
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem (edit)',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        prob = utils.problem.create_problem()
        rv = client_teacher.put(
            f'/problem/manage/{prob.id}',
            json=request_json,
        )
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'

    # admin change the name of a problem (PUT /problem/manage/<problem_id>)
    def test_admin_edit_problem_with_non_exist_course(self, client_admin):
        request_json = {
            'courses': ['PE'],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem (edit)',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.put('/problem/manage/1', json=request_json)
        json = rv.get_json()
        print(json)
        assert rv.status_code == 404

    def test_edit_problem_with_course_does_not_exist(self, client_admin):
        prob = utils.problem.create_problem()
        request_json = {
            'courses': ['CourseDoesNotExist'],
            'status': 1,
            'type': 0,
            'problemName': 'Problem with course does not exist',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'Course not found.'

    def test_edit_problem_with_name_is_too_long(self, client_admin):
        prob = utils.problem.create_problem()
        oo = 'o' * 64
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': f'Problem name is t{oo} long!',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'Invalid or missing arguments.'

    # admin change the name of a problem (PUT /problem/manage/<problem_id>)
    def test_admin_edit_problem(self, client_admin):
        prob = utils.problem.create_problem()
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem (edit)',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        json = rv.get_json()
        print(json)
        assert rv.status_code == 200
        assert json['status'] == 'ok'

    def test_edit_problem_with_extended_config_schema(self, client_admin):
        prob = utils.problem.create_problem()
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': 'Updated schema problem',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                0,
                'fillInTemplate':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 50,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }, {
                    'caseCount': 1,
                    'taskScore': 50,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            },
            'config': advanced_config_payload(),
            'pipeline': advanced_pipeline_payload(),
        }
        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        assert rv.status_code == 200, rv.get_json()
        problem = Problem(prob.id)
        config = problem.obj.config
        assert config['aiVTuber'] is True
        assert config['artifactCollection'] == ['zip', 'compiledBinary']
        assert config['staticAnalysis']['networkAccessRestriction'][
            'firewallExtranet']['whitelist'] == ['192.168.1.1']
        assert config['scoringScript'] == {'custom': False}

    # admin get information of a problem (GET /problem/manage/<problem_id>)
    def test_admin_manage_problem(self, client_admin):
        # Create course first
        utils.course.create_course(teacher='admin', name='English')

        prob = utils.problem.create_problem(
            name='Offline problem',
            course='English',
            owner='admin',
            status=1,
            type=0,
        )
        # First edit it
        request_json = {
            'courses': [],
            'status': 1,
            'type': 0,
            'problemName': 'Offline problem (edit)',
            'description': description_dict(),
            'tags': [],
            'testCaseInfo': {
                'language':
                1,
                'fillInTemplate':
                '',
                'submissionMode':
                0,
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 1000,
                    'timeLimit': 1000
                }]
            }
        }
        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        assert rv.status_code == 200

        # Then get it
        rv = client_admin.get(f'/problem/manage/{prob.id}')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        data = json['data']
        assert data['problemName'] == 'Offline problem (edit)'
        assert data['status'] == 1
        assert data['testCase']['tasks'][0]['taskScore'] == 100
        assert data['allowedLanguage'] == 7
        assert data['canViewStdout'] == Problem(prob.id).can_view_stdout
        assert data['quota'] == -1
        assert_basic_problem_config(data['config'])

    def test_update_problem_test_case_with_non_zip_file(self, client_admin):
        rv = client_admin.put('/problem/manage/3',
                              data=get_file('bogay/0000.in'))
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'File is not a zip file'

    def test_update_problem_test_case_with_ambiguous_test_case(
            self, client_admin, monkeypatch):
        from mongo.problem.problem import SimpleIO, ContextIO
        monkeypatch.setattr(SimpleIO, 'validate', lambda *_: None)
        monkeypatch.setattr(ContextIO, 'validate', lambda *_: None)
        rv = client_admin.put('/problem/manage/3',
                              data=get_file('bogay/test_case.zip'))
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'ambiguous test case format'

    def test_update_problem_test_case_raise_does_not_exist_error(
            self, client_admin, monkeypatch):

        def mock_update_test_case(*_):
            raise DoesNotExist('Error from mock update_test_case.')

        from mongo.problem import Problem
        monkeypatch.setattr(Problem, 'update_test_case', mock_update_test_case)
        rv = client_admin.put('/problem/manage/3',
                              data=get_file('bogay/test_case.zip'))
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'Error from mock update_test_case.'

    def test_update_problem_test_case_with_unknown_content_type(
            self, client_admin):
        rv = client_admin.put('/problem/manage/3',
                              headers={'Content-type': 'unknown/content-type'})
        assert rv.status_code == 400, rv.get_json()
        assert rv.get_json()['message'] == 'Unknown content type'
        assert rv.get_json()['data']['contentType'] == 'unknown/content-type'

    def test_student_cannot_get_test_case(self, client_student):
        rv = client_student.get('/problem/3/testcase')
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Not enough permission'

    def test_teacher_can_download_problem_test_case(self, client_teacher,
                                                    monkeypatch):
        course = utils.course.create_course(teacher='teacher')
        problem = utils.problem.create_problem(course=course, owner='admin')
        monkeypatch.setattr(
            Problem, 'get_test_case',
            lambda *_: get_file('bogay/test_case.zip')['case'][0])
        rv = client_teacher.get(f'/problem/{problem.id}/testcase')
        assert rv.status_code == 200
        with ZipFile(io.BytesIO(rv.data)) as zf:
            ns = sorted(zf.namelist())
            in_ns = ns[::2]
            out_ns = ns[1::2]
            ns = zip(in_ns, out_ns)
            _io = [(
                zf.read(in_n),
                zf.read(out_n),
            ) for in_n, out_n in ns]
        assert _io == [(b'I AM A TEAPOT\n', b'I AM A TEAPOT\n')]

    def test_admin_update_problem_test_case(self, client_admin, monkeypatch):
        # FIXME: it should be impl in mock
        monkeypatch.setattr(
            Problem, 'get_test_case',
            lambda *_: get_file('bogay/test_case.zip')['case'][0])

        # update test case
        rv, rv_json, rv_data = BaseTester.request(
            client_admin,
            'put',
            '/problem/manage/3',
            data=get_file('bogay/test_case.zip'),
        )
        assert rv.status_code == 200, rv_json
        assert Problem(3).test_case.case_zip_minio_path is not None
        # check content
        rv, rv_json, rv_data = BaseTester.request(
            client_admin,
            'get',
            '/problem/3/testcase',
        )
        assert rv.status_code == 200
        with ZipFile(io.BytesIO(rv.data)) as zf:
            ns = sorted(zf.namelist())
            in_ns = ns[::2]
            out_ns = ns[1::2]
            ns = zip(in_ns, out_ns)
            _io = [(
                zf.read(in_n),
                zf.read(out_n),
            ) for in_n, out_n in ns]
        assert _io == [(b'I AM A TEAPOT\n', b'I AM A TEAPOT\n')], rv_data

    def test_get_testdata_with_invalid_token(self, client):
        rv = client.get('/problem/3/testdata?token=InvalidToken8787')
        assert rv.status_code == 401, rv.get_json()
        assert rv.get_json()['message'] == 'Invalid sandbox token'

    def test_get_testdata(self, client, monkeypatch):
        # FIXME: it should be impl in mock
        monkeypatch.setattr(
            Problem, 'get_test_case',
            lambda *_: get_file('bogay/test_case.zip')['case'][0])
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get('/problem/3/testdata?token=ValidToken')
        assert rv.status_code == 200
        with ZipFile(io.BytesIO(rv.data)) as zf:
            ns = sorted(zf.namelist())
            in_ns = ns[::2]
            out_ns = ns[1::2]
            ns = zip(in_ns, out_ns)
            _io = [(
                zf.read(in_n),
                zf.read(out_n),
            ) for in_n, out_n in ns]
        assert _io == [(b'I AM A TEAPOT\n', b'I AM A TEAPOT\n')]

    def test_get_checksum_with_invalid_token(self, client):
        rv = client.get('/problem/3/checksum?token=InvalidToken8787')
        assert rv.status_code == 401, rv.get_json()
        assert rv.get_json()['message'] == 'Invalid sandbox token'

    def test_get_checksum_with_problem_does_not_exist(self, client,
                                                      monkeypatch):
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get('/problem/878787/checksum?token=SandboxToken')
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'problem [878787] not found'

    def test_get_checksum(self, client, monkeypatch):
        # FIXME: it should be impl in mock
        monkeypatch.setattr(
            Problem, 'get_test_case',
            lambda *_: get_file('bogay/test_case.zip')['case'][0])
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get('/problem/3/checksum?token=SandboxToken')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['data'] == '710051d7e636d7c57add4ceb4a3138b3'

    def test_get_meta_with_invalid_token(self, client):
        rv = client.get('/problem/3/meta?token=InvalidToken8787')
        assert rv.status_code == 401, rv.get_json()
        assert rv.get_json()['message'] == 'Invalid sandbox token'

    def test_get_meta_with_problem_does_not_exist(self, client, monkeypatch):
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get('/problem/878787/meta?token=SandboxToken')
        assert rv.status_code == 404, rv.get_json()
        assert rv.get_json()['message'] == 'problem [878787] not found'

    def test_get_meta(self, client, monkeypatch):

        class MockSandbox:
            token = 'SandboxToken'

        class MockConfig:
            sandbox_instances = [MockSandbox()]

        from mongo.sandbox import Submission
        monkeypatch.setattr(Submission, 'config', MockConfig)
        rv = client.get('/problem/3/meta?token=SandboxToken')
        assert rv.status_code == 200, rv.get_json()
        payload = rv.get_json()['data']
        assert payload['submissionMode'] == 0
        assert payload['tasks'] == [{
            'caseCount': 1,
            'memoryLimit': 1000,
            'taskScore': 100,
            'timeLimit': 1000
        }]
        assert payload['executionMode'] == 'general'
        assert payload['assetPaths'] == {}
        assert payload['teacherFirst'] is False
        assert payload['buildStrategy'] == 'compile'
        assert payload.get('customChecker') is False

    def test_get_meta_with_custom_checker(self, client_admin, monkeypatch):

        class MockSandbox:
            token = 'SandboxToken'

        class MockConfig:
            sandbox_instances = [MockSandbox()]

        from mongo.sandbox import Submission
        monkeypatch.setattr(Submission, 'config', MockConfig)
        prob = utils.problem.create_problem()
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as zf:
            zf.writestr('Makefile', 'all:\n\t@touch a.out\n')
        buf.seek(0)
        data = {
            'meta': json.dumps({
                'pipeline': {
                    'customChecker': True,
                },
            }),
            'custom_checker.py':
            (io.BytesIO(b'print("ok")'), 'custom_checker.py'),
        }
        rv = client_admin.put(
            f'/problem/{prob.problem_id}/assets',
            data=data,
            content_type='multipart/form-data',
        )
        assert rv.status_code == 200
        rv = client_admin.get(f'/problem/{prob.problem_id}/meta',
                              query_string={'token': 'SandboxToken'})
        assert rv.status_code == 200, rv.get_json()
        payload = rv.get_json()['data']
        assert payload['customChecker'] is True
        assert payload['checkerAsset']

    def test_get_meta_build_strategy_variants(self, client_admin, client,
                                              monkeypatch):

        class MockSandbox:
            token = 'SandboxToken'

        class MockConfig:
            sandbox_instances = [MockSandbox()]

        from mongo.sandbox import Submission
        monkeypatch.setattr(Submission, 'config', MockConfig)

        # general zip -> makeNormal
        prob = utils.problem.create_problem()
        prob.update(test_case__submission_mode=1)
        prob.reload('test_case')
        rv = client.get(f'/problem/{prob.problem_id}/meta?token=SandboxToken')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['data']['buildStrategy'] == 'makeNormal'

        # functionOnly -> makeFunctionOnly
        Problem.edit_problem(
            user=User('admin'),
            problem_id=prob.problem_id,
            pipeline={'executionMode': 'functionOnly'},
        )
        prob.update(test_case__submission_mode=0)
        prob.reload('test_case')
        rv = client.get(f'/problem/{prob.problem_id}/meta?token=SandboxToken')
        assert rv.status_code == 200
        assert rv.get_json()['data']['buildStrategy'] == 'makeFunctionOnly'

        # interactive zip -> makeInteractive
        Problem.edit_problem(
            user=User('admin'),
            problem_id=prob.problem_id,
            pipeline={'executionMode': 'interactive'},
        )
        prob.update(test_case__submission_mode=1)
        prob.reload('test_case')
        rv = client.get(f'/problem/{prob.problem_id}/meta?token=SandboxToken')
        assert rv.status_code == 200
        assert rv.get_json()['data']['buildStrategy'] == 'makeInteractive'

    def test_get_static_analysis_rules_not_configured(self, client,
                                                      monkeypatch):
        prob = utils.problem.create_problem()
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get(f'/problem/{prob.problem_id}/rules?token=SandboxToken')
        assert rv.status_code == 404, rv.get_json()

    def test_get_static_analysis_rules(self, client_admin, client,
                                       monkeypatch):
        prob = utils.problem.create_problem()
        Problem.edit_problem(
            user=User('admin'),
            problem_id=prob.problem_id,
            pipeline={
                'staticAnalysis': {
                    'libraryRestrictions': {
                        'enabled': True,
                        'whitelist': {
                            'syntax': ['while'],
                            'imports': ['os'],
                            'headers': ['stdio.h'],
                            'functions': ['printf'],
                        },
                        'blacklist': {
                            'syntax': [],
                            'imports': [],
                            'headers': [],
                            'functions': [],
                        },
                    },
                },
            },
        )
        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get(f'/problem/{prob.problem_id}/rules?token=SandboxToken')
        assert rv.status_code == 200, rv.get_json()
        assert rv.get_json()['data'] == {
            'model': 'white',
            'syntax': ['while'],
            'imports': ['os'],
            'headers': ['stdio.h'],
            'functions': ['printf'],
        }

    def test_upload_problem_assets_accepts_meta(self, client_admin):
        prob = utils.problem.create_problem()
        meta_payload = {
            'config': {
                'networkAccessRestriction': {
                    'enabled': True,
                    'firewallExtranet': {
                        'enabled': True,
                        'whitelist': ['1.1.1.1'],
                        'blacklist': [],
                    },
                },
            },
            'pipeline': {
                'executionMode': 'functionOnly',
                'teacherFirst': True,
            },
        }
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as zf:
            zf.writestr('Makefile', 'all:\n\t@touch a.out\n')
            zf.writestr('function.h', '// template')
        buf.seek(0)
        data = {
            'meta': json.dumps(meta_payload),
            'custom_checker.py':
            (io.BytesIO(b'print("ok")'), 'custom_checker.py'),
            'makefile.zip': (buf, 'makefile.zip'),
        }
        rv = client_admin.put(
            f'/problem/{prob.problem_id}/assets',
            data=data,
            content_type='multipart/form-data',
        )
        assert rv.status_code == 200, rv.get_json()
        updated = Problem(prob.problem_id)
        assert updated.config['executionMode'] == 'functionOnly'
        assert updated.config['teacherFirst'] is True
        assert updated.config['networkAccessRestriction']['enabled'] is True
        assert 'checker' in updated.config.get('assetPaths', {})

    def test_upload_function_only_requires_makefile(self, client_admin):
        prob = utils.problem.create_problem()
        meta_payload = {
            'pipeline': {
                'executionMode': 'functionOnly',
            },
        }
        data = {
            'meta': json.dumps(meta_payload),
        }
        rv = client_admin.put(
            f'/problem/{prob.problem_id}/assets',
            data=data,
            content_type='multipart/form-data',
        )
        assert rv.status_code == 400, rv.get_json()

    def test_upload_function_only_with_makefile(self, client_admin):
        prob = utils.problem.create_problem()
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as zf:
            zf.writestr('Makefile', 'all:\n\t@touch a.out\n')
            zf.writestr('function.h', '// template')
        buf.seek(0)
        meta_payload = {
            'pipeline': {
                'executionMode': 'functionOnly',
            },
        }
        data = {
            'meta': json.dumps(meta_payload),
            'makefile.zip': (buf, 'makefile.zip'),
        }
        rv = client_admin.put(
            f'/problem/{prob.problem_id}/assets',
            data=data,
            content_type='multipart/form-data',
        )
        assert rv.status_code == 200, rv.get_json()
        updated = Problem(prob.problem_id)
        assert updated.config['executionMode'] == 'functionOnly'
        assert 'makefile' in updated.config.get('assetPaths', {})

    def test_get_problem_asset(self, client, client_admin, monkeypatch):
        prob = utils.problem.create_problem()
        buf = io.BytesIO()
        with ZipFile(buf, 'w') as zf:
            zf.writestr('Makefile', 'all:\n\t@touch a.out\n')
            zf.writestr('function.h', '// template')
        buf.seek(0)
        data = {
            'meta': json.dumps({'pipeline': {
                'executionMode': 'functionOnly'
            }}),
            'makefile.zip': (buf, 'makefile.zip'),
        }
        rv = client_admin.put(
            f'/problem/{prob.problem_id}/assets',
            data=data,
            content_type='multipart/form-data',
        )
        assert rv.status_code == 200

        from model.problem import sandbox
        monkeypatch.setattr(sandbox, 'find_by_token', lambda *_: True)
        rv = client.get(
            f'/problem/{prob.problem_id}/asset/makefile?token=SandboxToken')
        assert rv.status_code == 200, rv.get_json()
        assert rv.data[:2] == b'PK'

    def test_view_problem_returns_pipeline_and_network(self, client_admin):
        prob = utils.problem.create_problem()
        network_config = {
            'enabled': True,
            'firewallExtranet': {
                'enabled': True,
                'whitelist': ['2.2.2.2'],
                'blacklist': [],
            },
        }
        Problem.edit_problem(
            user=User('admin'),
            problem_id=prob.problem_id,
            config={'networkAccessRestriction': network_config},
            pipeline={
                'executionMode': 'interactive',
                'teacherFirst': True
            },
        )
        rv = client_admin.get(f'/problem/view/{prob.problem_id}')
        assert rv.status_code == 200, rv.get_json()
        data = rv.get_json()['data']
        assert data['pipeline']['executionMode'] == 'interactive'
        assert data['pipeline']['teacherFirst'] is True
        assert data['config']['networkAccessRestriction']['enabled'] is True

    def test_admin_update_problem_test_case_with_invalid_data(
        self,
        client_admin,
    ):
        prob = utils.problem.create_problem()
        # upload a test case with invalid data
        rv, rv_json, rv_data = BaseTester.request(
            client_admin,
            'put',
            f'/problem/manage/{prob.id}',
            data=get_file('task-exceed/test_case.zip'),
        )
        assert rv.status_code == 400

    # non-owner teacher get information of a problem (GET /problem/manage/<problem_id>)
    def test_teacher_not_owner_manage_problem(self, client_teacher):
        prob = utils.problem.create_problem()
        rv = client_teacher.get(f'/problem/manage/{prob.id}')
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'

    # student get information of a problem (GET /problem/manage/<problem_id>)
    def test_student_manage_problem(self, client_student):
        prob = utils.problem.create_problem()
        rv = client_student.get(f'/problem/manage/{prob.id}')
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'

    # student delete problem (DELETE /problem/manage/<problem_id>)
    def test_student_delete_problem(self, client_student):
        rv = client_student.delete('/problem/manage/1')
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'
        assert json['message'] == 'Insufficient Permissions'

    # non-owner teacher delete problem (DELETE /problem/manage/<problem_id>)
    def test_teacher_not_owner_delete_problem(self, client_teacher):
        prob = utils.problem.create_problem()
        rv = client_teacher.delete(f'/problem/manage/{prob.id}')
        json = rv.get_json()
        assert rv.status_code == 403
        assert json['status'] == 'err'

    # admin delete problem (DELETE /problem/manage/<problem_id>)
    def test_admin_delete_problem(self, client_admin):
        prob = utils.problem.create_problem()
        rv = client_admin.delete(f'/problem/manage/{prob.id}')
        json = rv.get_json()
        assert rv.status_code == 200
        assert json['status'] == 'ok'
        assert not Problem(prob.id)

    def test_student_cannot_copy_problem(self, forge_client):
        student = utils.user.create_user()
        course = student.courses[-1]
        problem = utils.problem.create_problem(course=course)
        client = forge_client(student.username)
        rv = client.post(
            '/problem/copy',
            json={
                'problemId': problem.problem_id,
            },
        )
        assert rv.status_code == 403

    def test_teacher_cannot_copy_problem_from_other_course(
            self, forge_client, make_course):
        c_data = make_course('teacher-2')
        client_teacher = forge_client('teacher-2')
        rv = client_teacher.post('/problem/copy',
                                 json={
                                     'problemId': 3,
                                     'target': c_data.name
                                 })
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Problem can not view.'

    def test_admin_can_copy_problem_from_other_course(self, forge_client):
        admin = utils.user.create_user(role=User.engine.Role.ADMIN)
        course = admin.courses[-1]
        original_problem = utils.problem.create_problem(course=course)
        new_course = utils.course.create_course()
        client_admin = forge_client(admin.username)
        rv, rv_json, rv_data = self.request(
            client_admin,
            'post',
            '/problem/copy',
            json={
                'problemId': original_problem.problem_id,
                'target': new_course.course_name,
            },
        )
        assert rv.status_code == 200, rv_json
        new_problem = Problem(rv_data['problemId'])
        utils.problem.cmp_copied_problem(original_problem, new_problem)

    def test_override_copied_problem_status(self, forge_client):
        admin = utils.user.create_user(role=User.engine.Role.ADMIN)
        original_problem = utils.problem.create_problem(
            status=Problem.engine.Visibility.SHOW)
        client = forge_client(admin.username)
        rv, rv_json, rv_data = self.request(
            client,
            'post',
            '/problem/copy',
            json={
                'problemId': original_problem.problem_id,
                'status': Problem.engine.Visibility.HIDDEN,
            },
        )
        assert rv.status_code == 200, rv_json
        another_problem = Problem(rv_data['problemId'])
        utils.problem.cmp_copied_problem(original_problem, another_problem)

        assert original_problem.problem_status != Problem.engine.Visibility.HIDDEN
        assert another_problem.problem_status == Problem.engine.Visibility.HIDDEN

    def test_student_cannot_copy_problem(self, forge_client):
        student = utils.user.create_user()
        course = student.courses[-1]
        problem = utils.problem.create_problem(course=course)
        client = forge_client(student.username)
        rv = client.post(
            '/problem/copy',
            json={
                'problemId': problem.problem_id,
            },
        )
        assert rv.status_code == 403

    def test_admin_can_copy_problem_from_other_course(self, forge_client):
        admin = utils.user.create_user(role=User.engine.Role.ADMIN)
        course = admin.courses[-1]
        original_problem = utils.problem.create_problem(course=course)
        new_course = utils.course.create_course()
        client_admin = forge_client(admin.username)
        rv, rv_json, rv_data = self.request(
            client_admin,
            'post',
            '/problem/copy',
            json={
                'problemId': original_problem.problem_id,
                'target': new_course.course_name,
            },
        )
        assert rv.status_code == 200, rv_json
        new_problem = Problem(rv_data['problemId'])
        utils.problem.cmp_copied_problem(original_problem, new_problem)

    def test_override_copied_problem_status(self, forge_client):
        admin = utils.user.create_user(role=User.engine.Role.ADMIN)
        original_problem = utils.problem.create_problem(
            status=Problem.engine.Visibility.SHOW)
        client = forge_client(admin.username)
        rv, rv_json, rv_data = self.request(
            client,
            'post',
            '/problem/copy',
            json={
                'problemId': original_problem.problem_id,
                'status': Problem.engine.Visibility.HIDDEN,
            },
        )
        assert rv.status_code == 200, rv_json
        another_problem = Problem(rv_data['problemId'])
        utils.problem.cmp_copied_problem(original_problem, another_problem)

        assert original_problem.problem_status != Problem.engine.Visibility.HIDDEN
        assert another_problem.problem_status == Problem.engine.Visibility.HIDDEN

    def test_publish_without_perm(self, forge_client):
        client_teacher = forge_client('teacher-2')
        rv = client_teacher.post('/problem/publish', json={'problemId': 3})
        assert rv.status_code == 403, rv.get_json()
        assert rv.get_json()['message'] == 'Not the owner.'

    def test_publish(self, client_admin):
        rv = client_admin.post('/problem/publish', json={'problemId': 3})
        assert rv.status_code == 200


class TestTrialSubmissionAPI(BaseTester):
    """
    Test cases for trial submission API endpoints
    """

    @pytest.fixture(scope='function')
    def setup_problem_with_testcases(self):
        """Create a problem with public test cases"""
        # Create a teacher and problem with unique course name
        teacher = User('teacher')
        course_name = f'TestCourse_{random_string()}'
        Course.add_course(course_name, teacher.username)
        course = Course(course_name)  # Get the course object after creation

        # Add student to the course
        student = User('student')
        course.obj.student_nicknames[student.username] = student.username
        course.obj.save()
        course.reload()

        # Create problem (Problem.add returns problem_id)
        problem_name = f'TestProblem_{random_string()}'
        problem_id = Problem.add(
            user=teacher,
            courses=[course_name],
            problem_name=problem_name,
            test_case_info={
                'language':
                2,
                'fill_in_template':
                '',
                'tasks': [{
                    'caseCount': 1,
                    'taskScore': 100,
                    'memoryLimit': 256000,  # KB
                    'timeLimit': 1000,  # ms
                }]
            },
        )
        problem = Problem(problem_id)
        problem.reload()

        # Enable trial mode + allow all languages (bitmask 7 = C/C++/Python)
        # Also make sure problem is visible
        try:
            problem.obj.problem_status = 0  # Visible
            problem.obj.trial_mode_enabled = True
            # Update config for new trial mode logic
            if not problem.obj.config:
                problem.obj.config = {}
            problem.obj.config['trialMode'] = True
        except Exception:
            pass
        try:
            problem.obj.allowed_language = 7
        except Exception:
            pass
        try:
            problem.obj.save()
            problem.reload()
        except Exception:
            pass

        # Upload public testcases zip to MinIO
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('case1.in', '1 2\n')
            zf.writestr('case1.out', '3\n')
            zf.writestr('case2.in', '5 7\n')
            zf.writestr('case2.out', '12\n')
        zip_buffer.seek(0)

        from mongo.utils import MinioClient
        minio = MinioClient()
        path = f'test/public_cases_{random_string()}.zip'
        minio.client.put_object(minio.bucket,
                                path,
                                zip_buffer,
                                length=len(zip_buffer.getvalue()))

        # Update problem with minio path
        problem.update(public_cases_zip_minio_path=path)
        problem.reload()

        yield problem, course

        # Cleanup
        try:
            minio.client.remove_object(minio.bucket, path)
        except:
            pass
        try:
            problem.obj.delete()
        except:
            pass
        try:
            course.obj.delete()
        except:
            pass

    def test_get_public_testcases_success(self, forge_client,
                                          setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 200

        data = rv.get_json()
        assert data['status'] == 'ok'
        assert 'Trial_Cases' in data['data']

        cases = data['data']['Trial_Cases']
        assert len(cases) == 2
        assert cases[0]['File_Name'] == 'case1'
        assert cases[0]['Input_Content'] == '1 2\n'
        assert cases[0]['Output_Content'] == '3\n'
        assert cases[0]['Memory_Limit'] == 256000
        assert cases[0]['Time_Limit'] == 1000
        assert cases[1]['File_Name'] == 'case2'
        assert cases[1]['Input_Content'] == '5 7\n'
        assert cases[1]['Output_Content'] == '12\n'

    def test_get_public_testcases_problem_not_found(self, forge_client):
        client = forge_client('student')
        rv = client.get('/problem/999999/public-testcases')
        assert rv.status_code == 404
        assert 'Problem not found' in rv.get_json()['message']

    def test_get_public_testcases_trial_mode_disabled(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        if hasattr(problem.obj, "trial_mode_enabled"):
            problem.obj.trial_mode_enabled = False
            problem.obj.save()
            problem.reload()

        client = forge_client('student')
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 403
        json = rv.get_json()
        assert json['status'] == 'err'
        assert 'Trial mode disabled' in json['message']

    def test_get_public_testcases_no_zip_uploaded(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        problem.update(public_cases_zip_minio_path=None)
        problem.reload()

        client = forge_client('student')
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 404
        assert 'No public testcases' in rv.get_json()['message']

    def test_get_public_testcases_unauthorized(self, forge_client,
                                               setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client(None)  # No user
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 403  # login_required returns 403

    def test_get_public_testcases_missing_out_file(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases

        # Create zip with only .in files
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('case1.in', '1 2\n')
        zip_buffer.seek(0)

        from mongo.utils import MinioClient
        minio = MinioClient()
        path = f'test/incomplete_{random_string()}.zip'
        minio.client.put_object(minio.bucket,
                                path,
                                zip_buffer,
                                length=len(zip_buffer.getvalue()))

        problem.update(public_cases_zip_minio_path=path)
        problem.reload()

        client = forge_client('student')
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 200

        data = rv.get_json()
        cases = data['data']['Trial_Cases']
        assert len(cases) == 1
        assert cases[0]['Input_Content'] == '1 2\n'
        assert cases[0]['Output_Content'] == ''

        try:
            minio.client.remove_object(minio.bucket, path)
        except:
            pass

    def test_get_public_testcases_invalid_zip(self, forge_client,
                                              setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases

        from mongo.utils import MinioClient
        minio = MinioClient()
        path = f'test/invalid_{random_string()}.zip'
        invalid_content = io.BytesIO(b'This is not a zip file')
        minio.client.put_object(minio.bucket,
                                path,
                                invalid_content,
                                length=len(invalid_content.getvalue()))

        problem.update(public_cases_zip_minio_path=path)
        problem.reload()

        client = forge_client('student')
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 500
        assert 'Invalid ZIP content' in rv.get_json()['message']

        try:
            minio.client.remove_object(minio.bucket, path)
        except:
            pass

    def test_get_public_testcases_no_test_case_limits(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        problem.update(test_case=None)
        problem.reload()

        client = forge_client('student')
        rv = client.get(f'/problem/{problem.problem_id}/public-testcases')
        assert rv.status_code == 200

        data = rv.get_json()
        cases = data['data']['Trial_Cases']
        assert cases[0]['Memory_Limit'] is None
        assert cases[0]['Time_Limit'] is None

    def test_request_trial_submission_success(self, forge_client,
                                              setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(
            f'/problem/{problem.problem_id}/trial/request',
            json={
                'languageType': 2,  # Python
                'use_default_test_cases': True
            })
        assert rv.status_code == 200
        data = rv.get_json()
        assert data['status'] == 'ok'
        assert 'trial_submission_id' in data['data']
        trial_id = data['data']['trial_submission_id']

        # Verify record
        from mongo.submission import TrialSubmission
        ts = TrialSubmission(trial_id)
        assert ts is not None
        assert ts.problem_id == problem.problem_id
        assert ts.language == 2
        assert ts.use_default_case is True

    def test_request_trial_submission_invalid_language(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(
            f'/problem/{problem.problem_id}/trial/request',
            json={
                'languageType': 3,  # Not allowed for trial
                'use_default_test_cases': True
            })
        assert rv.status_code == 400
        assert 'Invalid language type' in rv.get_json()['message']

    def test_request_trial_submission_problem_not_found(self, forge_client):
        client = forge_client('student')
        rv = client.post('/problem/999999/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        assert rv.status_code == 404
        assert 'Problem not found' in rv.get_json()['message']

    def test_request_trial_submission_no_permission(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client(None)  # Unauthenticated
        rv = client.post(f'/problem/{problem.problem_id}/trial/request',
                         json={
                             'languageType': 2,
                             'use_default_test_cases': True
                         })
        assert rv.status_code == 403

    def test_request_trial_submission_with_custom_cases(
            self, forge_client, setup_problem_with_testcases):
        problem, _ = setup_problem_with_testcases
        client = forge_client('student')

        rv = client.post(
            f'/problem/{problem.problem_id}/trial/request',
            json={
                'languageType': 0,  # C
                'use_default_test_cases': False
            })
        assert rv.status_code == 200
        from mongo.submission import TrialSubmission
        ts = TrialSubmission(rv.get_json()['data']['trial_submission_id'])
        assert ts.use_default_case is False
