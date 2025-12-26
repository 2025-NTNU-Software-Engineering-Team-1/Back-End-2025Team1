import os


# ============================================
# Security Configuration
# ============================================

# CORS allowed origins (comma-separated, supports wildcards like http://localhost:*)
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in
    os.getenv('CORS_ALLOWED_ORIGINS', 'http://localhost:*,http://127.0.0.1:*').split(',')
    if o.strip()
]

# Force Secure flag on cookies (for HTTPS production)
FORCE_SECURE_COOKIES = os.getenv('FORCE_SECURE_COOKIES', 'false').lower() == 'true'

# CSRF strict mode - enforce Origin/Referer validation even without SERVER_NAME
CSRF_STRICT_MODE = os.getenv('CSRF_STRICT_MODE', 'false').lower() == 'true'

# Rate limiting configuration
RATE_LIMIT_ENABLED = os.getenv('RATE_LIMIT_ENABLED', 'true').lower() == 'true'
RATE_LIMIT_MAX_ATTEMPTS = int(os.getenv('RATE_LIMIT_MAX_ATTEMPTS', '5'))
RATE_LIMIT_LOCKOUT_SECONDS = int(os.getenv('RATE_LIMIT_LOCKOUT_SECONDS', '900'))

# ============================================
# Logging Configuration
# ============================================

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
