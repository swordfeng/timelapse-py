#!/usr/bin/python3
import os
import subprocess
import sys
import time
import youtube_dl
import multiprocessing
import signal
import inspect
import json
from collections import OrderedDict
from typing import Optional

from .logger import logger

def _signal_handler(signum, frame):
    last_func = None
    while frame is not None:
        func = inspect.getframeinfo(frame).function
        if last_func == 'wait' and func == '_call_downloader':
            raise KeyboardInterrupt
        last_func = func
        frame = frame.f_back

def _download_ytdl_signaled(url: str, dirpath: str):
    signal.signal(signal.SIGUSR1, _signal_handler)
    ydl_opts = {
        'writeinfojson': True,
        'outtmpl': os.path.join(dirpath, '%(id)s.%(ext)s'),
        'postprocessor_args': ['-loglevel', 'warning'],
        'external_downloader_args': ['-loglevel', 'warning'],
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

class YtdlDownloader:
    def __init__(self, url: str, dirpath: str):
        logger.info(f'Downloading {url} using youtube-dl')
        self.proc = multiprocessing.Process(
            target=_download_ytdl_signaled,
            args=(url, dirpath),
        )
        self.proc.start()
    def interrupt(self):
        os.kill(self.proc.pid, signal.SIGUSR1)
    def is_running(self):
        return self.proc.is_alive()
    def wait(self, timeout: Optional[float] = None):
        self.proc.join(timeout)
        return self.proc.exitcode
    def kill(self):
        self.proc.kill()
    def finished(self):
        return self.proc.exitcode == 0

class YouGetDownloader:
    def __init__(self, url: str, dirpath: str, filename: str = None):
        logger.info(f'Downloading {url} using youget')
        if not filename:
            filename = str(int(time.time()))
        self.filename = filename
        self.dirpath = dirpath
        self._interrupted = False
        # download meta info
        infopath = os.path.join(dirpath, filename + '.info.json')
        logger.info(f'Downloading info to {infopath}')
        infodata = subprocess.run(
            ('you-get', '--json', url),
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            check=True,
        ).stdout
        infojson = json.loads(infodata, object_pairs_hook=OrderedDict)
        self.extname = next(iter(infojson['streams'].values()))['container']
        with open(infopath, 'w') as f:
            f.write(infodata)
        logger.info('Download stream file')
        self.proc = subprocess.Popen(
            ('you-get', '-o', dirpath, '-O', filename, '--no-caption', '-f', url),
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=True,
        )
    def interrupt(self):
        if self.is_running():
            self._interrupted = True
            os.kill(self.proc.pid, signal.SIGINT)
    def is_running(self):
        return self.proc.poll() is None
    def wait(self, timeout: Optional[float] = None):
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
        return self.proc.returncode
    def kill(self):
        self.proc.kill()
    def finished(self):
        if self.is_running():
            return False
        file_exists = f'{self.filename}.{self.extname}' in os.listdir(self.dirpath)
        # probably incorrect, anyway
        return file_exists