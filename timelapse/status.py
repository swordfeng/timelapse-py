#!/usr/bin/python3
import time
from .logger import logger

_status_watch = []

def status_add_watch(target):
    _status_watch.append(target)

def status_remove_watch(target):
    _status_watch = list(filter(lambda o: o != target, _status_watch))

def status_print():
    status = [line for o in _status_watch for line in o.status()]
    logger.info(' ===== STATUS REPORT =====')
    _print_status_lines(status)
    logger.info(' ===== END STATUS REPORT =====')

def _print_status_lines(status, padding = 0):
    for line in status:
        if isinstance(line, list):
            _print_status_lines(line, padding + 1)
        else:
            logger.info(f'[status] {"  " * padding}{line}')

def check_status(interval=5):
    while True:
        try:
            while True:
                time.sleep(interval)
                status_print()
        except KeyboardInterrupt:
            pass
        status_print()
        time.sleep(0.5)
