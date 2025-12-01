import io
import pytest
from mongo.problem import Problem
from tests import utils


def test_update_assets_custom_checker_syntax_error(app):
    with app.app_context():
        prob = utils.problem.create_problem(status=0)
        prob.obj.update(problem_status=0)
        prob.reload()
        bad_checker = io.BytesIO(b"def bad(:\n    pass\n")
        with pytest.raises(ValueError) as excinfo:
            prob.update_assets(
                user=prob.owner,
                files_data={'custom_checker.py': bad_checker},
                meta={'pipeline': {
                    'customChecker': True
                }},
            )
        assert "invalid custom checker syntax" in str(excinfo.value)
