"""Manages logging configuration for DVC repo."""

import logging
import logging.config
import logging.handlers
import sys

import colorama

from dvc.progress import Tqdm

FOOTER = (
    "\n{yellow}Having any troubles?{nc}"
    " Hit us up at {blue}https://dvc.org/support{nc},"
    " we are always happy to help!"
).format(
    blue=colorama.Fore.BLUE,
    nc=colorama.Fore.RESET,
    yellow=colorama.Fore.YELLOW,
)


def addLoggingLevel(levelName, levelNum, methodName=None):
    """
    Adds a new logging level to the `logging` module and the
    currently configured logging class.

    Uses the existing numeric levelNum if already defined.

    Based on https://stackoverflow.com/questions/2183233
    """
    if methodName is None:
        methodName = levelName.lower()

    # If the level name is already defined as a top-level `logging`
    # constant, then adopt the existing numeric level.
    if hasattr(logging, levelName):
        existingLevelNum = getattr(logging, levelName)
        assert isinstance(existingLevelNum, int)
        levelNum = existingLevelNum

    def logForLevel(self, message, *args, **kwargs):
        if self.isEnabledFor(levelNum):
            # pylint: disable=protected-access
            self._log(levelNum, message, args, **kwargs)

    def logToRoot(message, *args, **kwargs):
        logging.log(levelNum, message, *args, **kwargs)

    # getLevelName resolves the numeric log level if already defined,
    # otherwise returns a string
    if not isinstance(logging.getLevelName(levelName), int):
        logging.addLevelName(levelNum, levelName)

    if not hasattr(logging, levelName):
        setattr(logging, levelName, levelNum)

    if not hasattr(logging.getLoggerClass(), methodName):
        setattr(logging.getLoggerClass(), methodName, logForLevel)

    if not hasattr(logging, methodName):
        setattr(logging, methodName, logToRoot)


class LoggingException(Exception):
    def __init__(self, record):
        msg = f"failed to log {str(record)}"
        super().__init__(msg)


def exclude_filter(level: int):
    def filter_fn(record: "logging.LogRecord") -> bool:
        return record.levelno < level

    return filter_fn


class ColorFormatter(logging.Formatter):
    """Spit out colored text in supported terminals.

    colorama__ makes ANSI escape character sequences work under Windows.
    See the colorama documentation for details.

    __ https://pypi.python.org/pypi/colorama

    If record has an extra `tb_only` attribute, it will not show the
    exception cause, just the message and the traceback.
    """

    reset = colorama.Fore.RESET
    color_codes = {
        "TRACE": colorama.Fore.GREEN,
        "DEBUG": colorama.Fore.BLUE,
        "WARNING": colorama.Fore.YELLOW,
        "ERROR": colorama.Fore.RED,
        "CRITICAL": colorama.Fore.RED,
    }

    def __init__(self, log_colors: bool = True) -> None:
        super().__init__()
        self.log_colors = log_colors

    def format(self, record) -> str:  # noqa: A003, C901
        record.message = record.getMessage()
        msg = self.formatMessage(record)

        if record.levelno == logging.INFO:
            return msg

        ei = record.exc_info
        if ei:
            cause = ""
            if not getattr(record, "tb_only", False):
                cause = ": ".join(_iter_causes(ei[1]))
            sep = " - " if msg and cause else ""
            msg = msg + sep + cause

        asctime = ""
        if _is_verbose():
            asctime = self.formatTime(record, self.datefmt)
            if ei and not record.exc_text:
                record.exc_text = self.formatException(ei)
            if record.exc_text:
                if msg[-1:] != "\n":
                    msg = msg + "\n"
                msg = msg + record.exc_text + "\n"
            if record.stack_info:
                if msg[-1:] != "\n":
                    msg = msg + "\n"
                msg = msg + self.formatStack(record.stack_info) + "\n"

        level = record.levelname
        if self.log_colors:
            color = self.color_codes[level]
            if asctime:
                asctime = color + asctime + self.reset
            level = color + level + self.reset
        return asctime + (" " if asctime else "") + level + ": " + msg


class LoggerHandler(logging.StreamHandler):
    def handleError(self, record):
        super().handleError(record)
        raise LoggingException(record)

    def emit_pretty_exception(self, exc, verbose: bool = False):
        return exc.__pretty_exc__(verbose=verbose)

    def emit(self, record):
        """Write to Tqdm's stream so as to not break progress-bars"""
        try:
            if record.exc_info:
                _, exc, *_ = record.exc_info
                if hasattr(exc, "__pretty_exc__"):
                    try:
                        self.emit_pretty_exception(exc, verbose=_is_verbose())
                        if not _is_verbose():
                            return
                    except Exception:  # noqa, pylint: disable=broad-except
                        pass  # noqa: S110

            msg = self.format(record)
            Tqdm.write(msg, file=self.stream, end=getattr(self, "terminator", "\n"))
            self.flush()
        except (BrokenPipeError, RecursionError):
            raise
        except Exception:  # noqa, pylint: disable=broad-except
            self.handleError(record)


def _is_verbose():
    return (
        logging.NOTSET < logging.getLogger("dvc").getEffectiveLevel() <= logging.DEBUG
    )


def _iter_causes(exc):
    while exc:
        yield str(exc)
        exc = exc.__cause__


def disable_other_loggers():
    logging.captureWarnings(True)
    loggerDict = logging.root.manager.loggerDict  # pylint: disable=no-member
    for logger_name, logger in loggerDict.items():
        if logger_name != "dvc" and not logger_name.startswith("dvc."):
            logger.disabled = True  # type: ignore[union-attr]


def set_loggers_level(level: int = logging.INFO) -> None:
    for name in ["dvc", "dvc_objects", "dvc_data"]:
        logging.getLogger(name).setLevel(level)


def setup(level: int = logging.INFO, log_colors: bool = True) -> None:
    colorama.init()

    formatter = ColorFormatter(log_colors=log_colors and sys.stdout.isatty())

    console_info = LoggerHandler(sys.stdout)
    console_info.setLevel(logging.INFO)
    console_info.setFormatter(formatter)
    console_info.addFilter(exclude_filter(logging.WARNING))

    console_debug = LoggerHandler(sys.stdout)
    console_debug.setLevel(logging.DEBUG)
    console_debug.setFormatter(formatter)
    console_debug.addFilter(exclude_filter(logging.INFO))

    addLoggingLevel("TRACE", logging.DEBUG - 5)

    console_trace = LoggerHandler(sys.stdout)
    console_trace.setLevel(logging.TRACE)  # type: ignore[attr-defined]
    console_trace.setFormatter(formatter)
    console_trace.addFilter(exclude_filter(logging.DEBUG))

    err_formatter = ColorFormatter(log_colors=log_colors and sys.stderr.isatty())
    console_errors = LoggerHandler(sys.stderr)
    console_errors.setLevel(logging.WARNING)
    console_errors.setFormatter(err_formatter)

    for name in ["dvc", "dvc_objects", "dvc_data"]:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        for handler in [console_info, console_debug, console_trace, console_errors]:
            logger.addHandler(handler)

    if level >= logging.DEBUG:
        # Unclosed session errors for asyncio/aiohttp are only available
        # on the tracing mode for extensive debug purposes. They are really
        # noisy, and this is potentially somewhere in the client library
        # not managing their own session. Even though it is the best practice
        # for them to do so, we can be assured that these errors raised when
        # the object is getting deallocated, so no need to take any extensive
        # action.
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)
        logging.getLogger("aiohttp").setLevel(logging.CRITICAL)
