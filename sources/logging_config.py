import logging

from rich.logging import RichHandler

LEVELS = {
    'NOTSET': logging.NOTSET,
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
}


def get_logger(name: str, level: int | str = logging.INFO) -> logging.Logger:
    """Get a configured logger instance

        Args:
        name: Logger name (typically __name__)
        level: Logging level as int or string ('DEBUG', 'INFO', etc.)

    Returns:
        Configured logger instance

    Examples:
        logger = get_logger(__name__)  # INFO by default
        logger = get_logger(__name__, 'DEBUG')
        logger = get_logger(__name__, logging.WARNING)
    """
    if isinstance(level, str):
        level = LEVELS.get(level.upper(), logging.INFO)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        log_format = logging.Formatter('%(message)s')
        console_handler = RichHandler()
        console_handler.setFormatter(log_format)
        logger.addHandler(console_handler)

    return logger
