#!/usr/bin/python3
import os
import subprocess
import sys
import time
import youtube_dl
import multiprocessing
import signal
import inspect
from typing import Optional

from .logger import logger

def download_ytdl(url: str, dirpath: str):
    logger.info(f'Downloading {url} using youtube-dl')
    ydl_opts = {
        'writeinfojson': True,
        'outtmpl': os.path.join(dirpath, '%(id)s.%(ext)s'),
        'postprocessor_args': ['-loglevel', 'warning'],
        'external_downloader_args': ['-loglevel', 'warning'],
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def download_youget(url: str, dirpath: str, filename: str = None):
    logger.info(f'Downloading {url} using youget')
    if not filename:
        filename = str(int(time.time()))
    # download meta info
    infopath = os.path.join(dirpath, filename + '.info.json')
    logger.info(f'Downloading info to {infopath}')
    with open(infopath, 'w') as f:
        subprocess.run(
            ('you-get', '--json', url),
            stdout=f,
            stderr=sys.stderr,
            check=True,
        )
    logger.info('Download stream file')
    subprocess.run(
        ('you-get', '-o', dirpath, '-O', filename, '--no-caption', '-f', url),
        stdout=sys.stdout,
        stderr=sys.stderr,
        check=True,
    )

def _signal_handler(signum, frame):
    last_func = None
    while frame is not None:
        func = inspect.getframeinfo(frame).function
        print(func)
        if last_func == 'wait' and func == '_call_downloader':
            raise KeyboardInterrupt
        last_func = func
        frame = frame.f_back

def _download_ytdl_signaled(url: str, dirpath: str):
    signal.signal(signal.SIGUSR1, _signal_handler)
    download_ytdl(url, dirpath)

class download_ytdl_interruptable:
    def __init__(self, url: str, dirpath: str):
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
        return self.proc.join(timeout)
    def kill(self):
        self.proc.kill()
    def finished(self):
        return self.proc.exitcode == 0
