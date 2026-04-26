"""
Centralised logging configuration.

Usage
-----
In main.py (called once at startup):
    from fetcher.utils.logger import setup

    setup()          # stdout + file
    setup(level=logging.DEBUG, log_file="debug.log")

In every other module (zero setup needed — just get the shared logger):
    import logging
    log = logging.getLogger("TDS")
"""

import os
import sys
import logging
from pathlib import Path

_LOGGER_NAME = "TDS"
_DEFAULT_FMT = "%(asctime)s  %(levelname)-8s  %(message)s"
_DEFAULT_DT  = "%H:%M:%S"


def setup(
    level:    int  = logging.INFO,
    log_file: str  = None,
    to_file:  bool = True,
) -> logging.Logger:
    """
    Configure the root TDS logger with a stdout StreamHandler and an
    optional FileHandler.  Safe to call multiple times — handlers are
    only added once.
    """
    logger = logging.getLogger(_LOGGER_NAME)

    if logger.handlers:
        return logger   # already configured; do nothing

    logger.setLevel(level)
    fmt = logging.Formatter(_DEFAULT_FMT, datefmt=_DEFAULT_DT)

    # stdout
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # file (resolve relative to the caller's cwd or DATA_DIR)
    if to_file:
        if log_file is None:
            # Default to DATA_DIR/tds_fetcher.log
            data_dir = os.environ.get("DATA_DIR", ".")
            log_path = Path(data_dir) / "tds_fetcher.log"
        else:
            log_path = Path(log_file)

        try:
            fh = logging.FileHandler(
                log_path.resolve(), encoding="utf-8"
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)
        except OSError as exc:
            logger.warning("Could not open log file %s: %s", log_file, exc)

    return logger


def get() -> logging.Logger:
    """Return the shared TDS logger (already configured if setup() was called)."""
    return logging.getLogger(_LOGGER_NAME)
