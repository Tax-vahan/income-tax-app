import logging
import sys
from typing import Optional

_loggers = {}

def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """Get or create a logger with consistent formatting."""
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)

    if level:
        logger.setLevel(level)
    else:
        logger.setLevel(logging.INFO)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    _loggers[name] = logger
    return logger


def configure_logging(level: str = "INFO") -> None:
    """Configure logging for the application."""
    level_map = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL
    }

    numeric_level = level_map.get(level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
