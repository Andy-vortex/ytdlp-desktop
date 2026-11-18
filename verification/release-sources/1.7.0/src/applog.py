"""Logging set-up (1.6.0): one rotating download.log, with tracebacks. Imports paths only.

Loggers are 'YtDlpDesktop' and its children ('YtDlpDesktop.engine', '.collector', '.ui', ...).
setup() is called once per GUI start (app.py); modules only call get(child).
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import sys
import threading

import paths

NAME = 'YtDlpDesktop'
FORMAT = '%(asctime)s %(levelname).1s %(name)s %(message)s'
_lock = threading.Lock()
_previous_hooks = None


def setup() -> logging.Logger:
    """Attach the log file to the 'YtDlpDesktop' logger. Idempotent; never raises.

    The state folder is created here: no paths function creates anything, the file handler
    cannot open a file in a missing folder, and the 1.5.1 window skips its own handler when
    one is attached already.
    """
    logger = logging.getLogger(NAME)
    with _lock:
        if logger.handlers:
            return logger
        try:
            logger.setLevel(logging.INFO)
            paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(paths.log_file(), maxBytes=3 * 1024 * 1024, backupCount=2,
                                          encoding='utf-8')
            handler.setFormatter(logging.Formatter(FORMAT))
        except Exception:
            handler = logging.NullHandler()
        try:
            logger.addHandler(handler)
        except Exception:
            pass
    return logger


def get(child='') -> logging.Logger:
    """The logger 'YtDlpDesktop' or 'YtDlpDesktop.<child>'."""
    return logging.getLogger(f'{NAME}.{child}' if child else NAME)


def shutdown() -> None:
    """Close and remove the handlers and give the exception hooks back (tests)."""
    global _previous_hooks
    logger = logging.getLogger(NAME)
    with _lock:
        for handler in list(logger.handlers):
            try:
                logger.removeHandler(handler)
                handler.close()
            except Exception:
                pass
        if _previous_hooks is not None:
            sys.excepthook, threading.excepthook = _previous_hooks
            _previous_hooks = None


def install_hooks() -> None:
    """Send uncaught exceptions of the main thread and of worker threads to the log."""
    global _previous_hooks
    with _lock:
        if _previous_hooks is not None:
            return
        previous_main, previous_thread = sys.excepthook, threading.excepthook

        def on_exception(kind, value, trace):
            try:
                if not issubclass(kind, KeyboardInterrupt):
                    get().error('unhandled exception', exc_info=(kind, value, trace))
            except Exception:
                pass
            try:
                previous_main(kind, value, trace)
            except Exception:
                pass

        def on_thread_exception(arguments):
            try:
                if arguments.exc_type is not SystemExit:
                    name = arguments.thread.name if arguments.thread is not None else '?'
                    get().error('unhandled exception in thread %s', name,
                                exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback))
            except Exception:
                pass
            try:
                previous_thread(arguments)
            except Exception:
                pass

        _previous_hooks = (previous_main, previous_thread)
        sys.excepthook, threading.excepthook = on_exception, on_thread_exception
