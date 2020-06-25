#!/usr/bin/python3
import logging

logger = logging.getLogger('timelapse')
loghandler = logging.StreamHandler()
loghandler.setFormatter(logging.Formatter('[%(name)s][%(levelname)s] %(message)s'))
logger.addHandler(loghandler)
logger.setLevel(logging.INFO)
