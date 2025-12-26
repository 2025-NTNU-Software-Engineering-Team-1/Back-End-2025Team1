import os
import logging
from logging.config import dictConfig
from flask import Flask
from model import *
from mongo import *
from mongo.ai import migrate_ai_data
from config import LOGGING_CONFIG, LOG_DIR


def app():
    # Setup logging
    os.makedirs(LOG_DIR, exist_ok=True)
    dictConfig(LOGGING_CONFIG)

    # Create a flask app
    app = Flask(__name__)
    app.config['PREFERRED_URL_SCHEME'] = os.environ.get(
        'PREFERRED_URL_SCHEME', 'http')
    app.url_map.strict_slashes = False
    setup_smtp(app)

    # Apply security configurations (CSRF, Headers, Error Handlers)
    from model.utils.security import setup_security
    setup_security(app)

    # Register flask blueprint
    api2prefix = [
        (auth_api, '/auth'),
        (profile_api, '/profile'),
        (problem_api, '/problem'),
        (submission_api, '/submission'),
        (course_api, '/course'),
        (homework_api, '/homework'),
        (test_api, '/test'),
        (ann_api, '/ann'),
        (ranking_api, '/ranking'),
        (post_api, '/post'),
        (discussion_api, '/discussion'),
        (copycat_api, '/copycat'),
        (health_api, '/health'),
        (user_api, '/user'),
        (pat_api, '/pat'),
        (trial_submission_api, '/trial-submission'),
        (ai_api, '/ai'),
        (skin_api, '/ai'),  # Skin API under /ai prefix
        (login_records_api, ''),
    ]
    for api, prefix in api2prefix:
        app.register_blueprint(api, url_prefix=prefix)

    if not User('first_admin'):
        ADMIN = {
            'username': 'first_admin',
            'password': 'firstpasswordforadmin',
            'email': 'i.am.first.admin@noj.tw'
        }
        PROFILE = {
            'displayed_name': 'the first admin',
            'bio': 'I am super good!!!!!'
        }
        admin = User.signup(**ADMIN)
        # TODO: use a single method to active.
        #       we won't call `activate` here because it required the
        #       course 'Public' should exist, but create a course
        #       also need a teacher.
        #       but at least make it can work now...
        # admin.activate(PROFILE)
        admin.update(
            active=True,
            role=0,
            profile=PROFILE,
        )
    if not Course('Public'):
        Course.add_course('Public', 'first_admin')

    # Initialize AI Models and Data
    try:
        AiModel.initialize_default_models()
        migrate_ai_data()
    except Exception as e:
        app.logger.warning(f"AI initialization failed: {e}")

    if __name__ != '__main__':
        logger = logging.getLogger('gunicorn.error')
        # Avoid mixing Flask default logger and Gunicorn logger
        # So I quoted the following line
        # app.logger.handlers = logger.handlers
        app.logger.setLevel(logger.level)

    return app


def setup_smtp(app: Flask):
    if os.getenv('SMTP_SERVER') is None:
        app.logger.info(
            "'SMTP_SERVER' is not set. email-related function will be disabled"
        )
        return

    # Check for required SMTP settings with fallback
    smtp_noreply = os.getenv('SMTP_NOREPLY')
    server_name = os.getenv('SERVER_NAME')

    if smtp_noreply is None:
        app.logger.warning(
            "'SMTP_SERVER' is set but 'SMTP_NOREPLY' is missing. "
            "Email functionality will be disabled.")
        return

    if server_name is None:
        app.logger.warning(
            "'SMTP_SERVER' is set but 'SERVER_NAME' is missing. "
            "Email functionality will be disabled.")
        return

    if os.getenv('SMTP_NOREPLY_PASSWORD') is None:
        app.logger.info("'SMTP_NOREPLY' set but 'SMTP_NOREPLY_PASSWORD' not")

    # All required settings present, configure SMTP
    app.config['SERVER_NAME'] = server_name
    if (application_root := os.getenv('APPLICATION_ROOT')) is not None:
        app.config['APPLICATION_ROOT'] = application_root

    app.logger.info(f"SMTP configured: server={os.getenv('SMTP_SERVER')}")
