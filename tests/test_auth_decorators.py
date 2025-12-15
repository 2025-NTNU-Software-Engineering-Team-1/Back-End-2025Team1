import pytest
from model import auth
from model.utils import HTTPResponse
from mongo import engine, User
from mongo.pat import PAT
from tests.base_tester import BaseTester


class TestAuthDecorators(BaseTester):

    @pytest.fixture(autouse=True)
    def setup_routes(self, app):
        # Register Routes on app fixture
        @app.route('/protected/session')
        @auth.login_required
        def protected_session(user):
            return HTTPResponse(data={'msg': 'ok', 'user': user.username})

        @app.route('/protected/pat')
        @auth.login_required(pat_scope=['read:user'])
        def protected_pat(user):
            return HTTPResponse(data={'msg': 'ok', 'user': user.username})

        @app.route('/protected/pat/multi')
        @auth.login_required(pat_scope=['read:user', 'write:user'])
        def protected_pat_multi(user):
            return HTTPResponse(data={'msg': 'ok', 'user': user.username})

    def setup_method(self):
        # Clear users and pats
        User.engine.objects.delete()
        PAT.objects.delete()

        # Create a test user
        self.user = User.signup('authuser', 'authpass', 'auth@test.com')
        self.user.activate()

        # Create a PAT for the user
        self.pat_read_only_token, self.pat_read = PAT.generate(
            name='read_only',
            owner=self.user.username,
            scope=['read:user'],
            due_time=None)

        self.pat_full_token, self.pat_full = PAT.generate(
            name='full_access',
            owner=self.user.username,
            scope=['read:user', 'write:user'],
            due_time=None)

    def login(self, username, password, client):
        """Helper to simulate login by setting cookie"""
        user = User(username)
        client.set_cookie('piann', user.secret, domain='test.test')

    def test_login_required_session_only(self, client):
        """Test @login_required without scopes (Session ONLY)"""
        # 1. No auth -> 403
        res = client.get('/protected/session')
        assert res.status_code == 403

        # 2. Login -> 200
        self.login('authuser', 'authpass', client)
        res = client.get('/protected/session')
        assert res.status_code == 200
        assert res.json['data']['user'] == 'authuser'

        # 3. PAT -> 403 (Should be rejected as arguments are empty)
        # Auth header causes validate_pat_request to NOT be called here because pat_scopes is None.
        # Fallback to session check, which fails.
        # Clear cookie first
        client.delete_cookie('piann', domain='test.test')
        res = client.get(
            '/protected/session',
            headers={'Authorization': f'Bearer {self.pat_read_only_token}'})
        assert res.status_code == 403

    def test_login_required_with_pat_scope_pat_access(self, client):
        """Test @login_required(pat_scope=...) with PAT access"""
        # 1. Valid PAT -> 200
        res = client.get(
            '/protected/pat',
            headers={'Authorization': f'Bearer {self.pat_read_only_token}'})
        assert res.status_code == 200
        assert res.json['data']['user'] == 'authuser'

        # 2. Token invalid -> 401
        res = client.get('/protected/pat',
                         headers={'Authorization': 'Bearer invalid_token'})
        assert res.status_code == 401

        # 3. Valid PAT but insufficient scope -> 403
        # Create token with only 'write:user'
        other_token, _ = PAT.generate(name='other_scope',
                                      owner=self.user.username,
                                      scope=['write:user'],
                                      due_time=None)
        res = client.get('/protected/pat',
                         headers={'Authorization': f'Bearer {other_token}'})
        assert res.status_code == 403  # Insufficient Scope

    def test_login_required_with_pat_scope_session_fallback(self, client):
        """Test @login_required(pat_scope=...) with Session Fallback"""
        # 1. No header, No session -> 403
        res = client.get('/protected/pat')
        assert res.status_code == 403

        # 2. Login -> 200 (Fallback to session)
        self.login('authuser', 'authpass', client)
        res = client.get('/protected/pat')
        assert res.status_code == 200
        assert res.json['data']['user'] == 'authuser'

    def test_login_required_multi_scope(self, client):
        # 1. Token with subset scope -> 403
        res = client.get(
            '/protected/pat/multi',
            headers={'Authorization': f'Bearer {self.pat_read_only_token}'})
        assert res.status_code == 403

        # 2. Token with superset/exact scope -> 200
        res = client.get(
            '/protected/pat/multi',
            headers={'Authorization': f'Bearer {self.pat_full_token}'})
        assert res.status_code == 200
