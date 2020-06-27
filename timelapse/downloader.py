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
import threading
import streamlink
import magic
import mimetypes
from collections import OrderedDict
from typing import Optional

from .logger import logger

sl = streamlink.Streamlink({
    'hds-timeout': 20.0,
    'hls-timeout': 20.0,
    'http-timeout': 20.0,
    'http-stream-timeout': 20.0,
    'stream-timeout': 20.0,
    'rtmp-timeout': 20.0,
})

def _ytdl_signal_handler(signum, frame):
    last_func = None
    # hacking starts
    while frame is not None:
        func = inspect.getframeinfo(frame).function
        if last_func == 'wait' and func == '_call_downloader':
            raise KeyboardInterrupt
        last_func = func
        frame = frame.f_back

def _ytdl_signaled(url: str, dirpath: str, filename: Optional[str]):
    signal.signal(signal.SIGUSR1, _ytdl_signal_handler)
    if not filename:
        filename = '%(id)s'
    ydl_opts = {
        'writeinfojson': True,
        'outtmpl': os.path.join(dirpath, filename + '.%(ext)s'),
        'postprocessor_args': ['-loglevel', 'warning'],
        'external_downloader_args': ['-loglevel', 'warning'],
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

class YtdlDownloader:
    def __init__(self, url: str, dirpath: str, filename: Optional[str] = None):
        logger.info(f'Downloading {url} using youtube-dl')
        self.proc = multiprocessing.Process(
            target=_ytdl_signaled,
            args=(url, dirpath, filename),
        )
        self.proc.start()
    def interrupt(self):
        os.kill(self.proc.pid, signal.SIGUSR1)
    def is_running(self):
        return self.proc.is_alive()
    def wait(self, timeout: Optional[float] = None):
        self.proc.join(timeout)
    def kill(self):
        self.proc.kill()
    def finished(self):
        return self.proc.exitcode == 0


class YouGetDownloader:
    def __init__(self, url: str, dirpath: str, filename: Optional[str] = None):
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
        with open(infopath, 'wb') as f:
            f.write(infodata)
        logger.info('Download stream file')
        self.proc = subprocess.Popen(
            ('you-get', '-o', dirpath, '-O', filename, '--no-caption', '-f', url),
            stdout=sys.stdout,
            stderr=sys.stderr,
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
    def kill(self):
        self.proc.kill()
    def finished(self):
        if self.is_running():
            return False
        file_exists = f'{self.filename}.{self.extname}' in os.listdir(self.dirpath)
        # probably incorrect, anyway
        return file_exists


class StreamlinkDownloader:
    def __init__(
        self,
        url: str,
        dirpath: str,
        filename: Optional[str] = None,
        bufsize: int = 8192,
        stream_timeout: int = 300,
        resolv_retry_interval: int = 3,
        resolv_retry_count: int = 5,
    ):
        logger.info(f'Downloading {url} using streamlink')
        if not filename:
            filename = str(int(time.time()))
        self.url = url
        self.dirpath = dirpath
        self.filename = filename
        self.extname = None
        self.bufsize = bufsize
        self.stream_timeout = stream_timeout
        self.resolv_retry_interval = resolv_retry_interval
        self.resolv_retry_count = resolv_retry_count
        self._interrupted = False
        self._finished = False
        self.thread = threading.Thread(target=self._download)
        self.thread.start()
    def interrupt(self):
        self._interrupted = True
    def is_running(self):
        return self.thread.is_alive()
    def wait(self, timeout: Optional[float] = None):
        self.thread.join(timeout)
    def kill(self):
        self._interrupted = True
    def finished(self):
        return self._finished
    def _download(self):
        try:
            filename = self.filename
            for i in range(1, self.resolv_retry_count + 1):
                streams = sl.streams(self.url)
                if streams or i == self.resolv_retry_count:
                    break
                logger.debug(f'Failed to resolve {self.url}, retry #{i}')
                time.sleep(self.resolv_retry_interval)
            assert streams
            stream = streams['best']
            logger.debug(f'Streamlink stream: {stream}')
            written_bytes = 0
            with stream.open() as infile:
                buffer = infile.read(self.bufsize)
                assert buffer
                mime = magic.from_buffer(buffer, mime=True)
                if mime == 'video/MP2T':
                    self.extname = '.ts'
                else:
                    self.extname = mimetypes.guess_extension(mime, strict=False)
                logger.info(f'Guessed mimetype {mime}; extname {self.extname}')
                if self.extname:
                    filename += self.extname
                outfilename = os.path.join(self.dirpath, filename)
                logger.info(f'Download destination: {outfilename}')
                last_active = time.time()
                with open(outfilename, 'wb') as outfile:
                    while not self._interrupted:
                        if buffer:
                            outfile.write(buffer)
                            written_bytes += len(buffer)
                        try:
                            buffer = infile.read(self.bufsize)
                            if not buffer:
                                break
                            last_active = time.time()
                        except IOError as e:
                            if self._interrupted:
                                break
                            if hasattr(e, 'args') and e.args == ('Read timeout',):
                                if time.time() - last_active < self.stream_timeout:
                                    logger.debug('streamlink stream read retry')
                                    buffer = None
                                    continue
                            raise
                self._finished = True
        except:
            logger.exception(f'Failed to download {self.url}')
