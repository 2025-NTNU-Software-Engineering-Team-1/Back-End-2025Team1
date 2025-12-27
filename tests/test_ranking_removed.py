import pytest
from tests.base_tester import BaseTester


class TestRankingRemoved(BaseTester):

    def test_ranking_is_gone(self, client):
        rv = client.get('/ranking/')
        assert rv.status_code == 404
        rv = client.get('/ranking')
        assert rv.status_code == 404
