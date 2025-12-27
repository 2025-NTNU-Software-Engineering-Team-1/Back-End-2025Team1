import pytest
from tests.base_tester import BaseTester
from mongo.problem import Problem
from tests import utils
from mongo import Submission


class TestTrialModeParams(BaseTester):

    def test_create_problem_with_trial_mode_payload(self, client_admin):
        utils.course.create_course(teacher='admin', name='TrialCourse')

        request_json = {
            'courses': ['TrialCourse'],
            'status': 0,
            'type': 0,
            'problemName': 'Trial Mode Problem',
            'description': {
                'description': 'desc'
            },
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
            'Trial_Mode': {
                'trialMode': True,
                'maxNumberOfTrial': 5,
                'trialResultVisible': True,
                'trialResultDownloadable': True
            }
        }

        rv = client_admin.post('/problem/manage', json=request_json)
        assert rv.status_code == 200, rv.get_json()
        pid = rv.get_json()['data']['problemId']

        problem = Problem(pid)
        config = problem.obj.config
        assert config['trialMode'] is True
        assert config['maxNumberOfTrial'] == 5
        assert config['trialResultVisible'] is True
        assert config['trialResultDownloadable'] is True

    def test_manage_problem_with_trial_mode_payload_update(self, client_admin):
        utils.course.create_course(teacher='admin', name='TrialCourseManage')
        prob = utils.problem.create_problem(name='Trial Manage Prob',
                                            course='TrialCourseManage',
                                            owner='admin')

        # Get problem details first to check default
        rv = client_admin.get(f'/problem/manage/{prob.id}')
        assert rv.status_code == 200
        data = rv.get_json()['data']
        # Trial_Mode might be present with defaults or absent if logic says so, but we added logic to always add it to info['Trial_Mode'] if config exists?
        assert 'Trial_Mode' in data
        assert data['Trial_Mode']['trialMode'] is False

        request_json = {
            'problemName': 'Updated Trial Prob',
            'Trial_Mode': {
                'trialMode': True,
                'maxNumberOfTrial': 10,
                'trialResultVisible': False,
                'trialResultDownloadable': True
            }
        }

        rv = client_admin.put(f'/problem/manage/{prob.id}', json=request_json)
        assert rv.status_code == 200, rv.get_json()

        # Verify persistence
        problem = Problem(prob.id)
        config = problem.obj.config
        assert config['trialMode'] is True
        assert config['maxNumberOfTrial'] == 10
        assert config['trialResultVisible'] is False
        assert config['trialResultDownloadable'] is True

        # Verify get_problem_detailed response
        rv = client_admin.get(f'/problem/manage/{prob.id}')
        data = rv.get_json()['data']
        assert data['Trial_Mode']['trialMode'] is True
        assert data['Trial_Mode']['maxNumberOfTrial'] == 10
        assert data['Trial_Mode']['trialResultVisible'] is False
        assert data['Trial_Mode']['trialResultDownloadable'] is True

        assert data['Trial_Mode']['trialResultDownloadable'] is True
