import os

LOG_DIR = 'logs'

LOG_LEVEL = os.getenv('LOG_LEVEL', 'DEBUG')
LOG_CONSOLE_LEVEL = os.getenv('LOG_CONSOLE_LEVEL', LOG_LEVEL)

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
        },
        # connectionpool is rather annoying too
        # though it is useful when debugging network issues
        'urllib3.connectionpool': {
            'level': 'INFO'
        },
        'flask.app': {
            'level': LOG_LEVEL,
            'handlers': ['console', 'file'],
            'propagate': False,
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default',
            'level': LOG_CONSOLE_LEVEL,
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
