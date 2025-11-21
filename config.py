import os

LOG_DIR = 'logs'

LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')

LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format':
            '[%(asctime)s] %(levelname)s in %(module)s:%(lineno)d: %(message)s',
        }
    },
    'loggers': {
        # MongoDB is too verbose, set it to WARNING
        'pymongo': {
            'level': 'WARNING'
        },
        'mongoengine': {
            'level': 'WARNING'
        }
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': LOG_DIR + '/oj-debug.log',
            'formatter': 'default',
            'encoding': 'utf-8'
        }
    },
    'root': {
        'level': LOG_LEVEL,
        'handlers': ['console', 'file']
    }
}
