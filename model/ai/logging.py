"""
AI Module logging configuration.

Provides a dedicated logger that writes to logs/ai.log.
"""

import logging
import os
import tempfile
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
LOGS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
try:
    os.makedirs(LOGS_DIR, exist_ok=True)
except PermissionError:
    LOGS_DIR = tempfile.gettempdir()

AI_LOG_FILE = os.path.join(LOGS_DIR, 'ai.log')

# Create the AI logger
_ai_logger = None


def _select_log_file() -> str:
    env_path = os.getenv('AI_LOG_FILE') or os.getenv('AI_LOG_PATH')
    if env_path:
        return env_path

    default = AI_LOG_FILE
    if os.path.exists(default) and not os.access(default, os.W_OK):
        suffix = str(os.getuid()) if hasattr(os, 'getuid') else str(
            os.getpid())
        if os.access(LOGS_DIR, os.W_OK):
            return os.path.join(LOGS_DIR, f'ai.{suffix}.log')
        return os.path.join(tempfile.gettempdir(), f'noj-ai.{suffix}.log')

    if os.access(LOGS_DIR, os.W_OK):
        return default
    return os.path.join(tempfile.gettempdir(), 'noj-ai.log')


def get_ai_logger() -> logging.Logger:
    """
    Get the AI module logger.
    
    Returns a logger that writes to logs/ai.log with rotation.
    """
    global _ai_logger

    if _ai_logger is None:
        _ai_logger = logging.getLogger('ai_module')
        _ai_logger.setLevel(logging.DEBUG)

        # Prevent propagation to root logger (avoid console output)
        _ai_logger.propagate = False

        # Only add handler if not already added
        if not _ai_logger.handlers:
            # File handler with rotation (10MB max, keep 5 backups)
            log_file = _select_log_file()
            try:
                file_handler = RotatingFileHandler(
                    log_file,
                    maxBytes=10 * 1024 * 1024,  # 10MB
                    backupCount=5,
                    encoding='utf-8')
                file_handler.setLevel(logging.DEBUG)
            except OSError:
                file_handler = logging.NullHandler()

            # Formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S')
            file_handler.setFormatter(formatter)

            _ai_logger.addHandler(file_handler)

    return _ai_logger


# Convenience function for getting child loggers
def get_logger(name: str) -> logging.Logger:
    """
    Get a child logger for a specific module.
    
    Args:
        name: Module name (e.g., 'service', 'retriever')
        
    Returns:
        Logger instance that writes to ai.log
    """
    parent = get_ai_logger()
    return parent.getChild(name)
