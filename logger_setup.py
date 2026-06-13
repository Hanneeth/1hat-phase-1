import logging
import sys
import config

def setup_logging() -> logging.Logger:
    """
    Configures the root logger using settings from config.py.
    This function is idempotent and safe to call multiple times.
    It cleans up any existing handlers on the root logger before
    attaching a console (stdout) handler to prevent duplicate logs.

    Returns:
        logging.Logger: The configured root logger.
    """
    root_logger = logging.getLogger()
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Make it idempotent: remove any existing handlers to prevent duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Set up console handler writing to stdout
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(config.LOG_FORMAT)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    return root_logger
