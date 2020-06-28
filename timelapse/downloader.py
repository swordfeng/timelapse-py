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
import streamlink.stream
import magic
import mimetypes
import you_get.common
import requests
from collections import OrderedDict
from typing import Optional

from .logger import logger

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


def _youget(url: str, dirpath: str, filename: str):
    try:
        # override bilibili live quality
        from you_get.extractors import Bilibili
        @staticmethod
        def bilibili_live_api(cid):
            return f'https://api.live.bilibili.com/room/v1/Room/playUrl?cid={cid}&quality=4&platform=web'
        Bilibili.bilibili_live_api = bilibili_live_api
        you_get.common.output_filename = filename
        you_get.common.force = True
        you_get.common.download_main(
            you_get.common.any_download,
            you_get.common.any_download_playlist,
            [url],
            playlist=False,
            output_dir=dirpath,
            caption=False,
            merge=True,
            info_only=False,
            json_output=False,
        )
    except KeyboardInterrupt:
        pass

class YouGetDownloader:
    def __init__(self, url: str, dirpath: str, filename: Optional[str] = None):
        logger.info(f'Downloading {url} using youget')
        if not filename:
            filename = str(int(time.time()))
        self.proc = multiprocessing.Process(
            target=_youget,
            args=(url, dirpath, filename),
        )
        self.proc.start()
    def interrupt(self):
        os.kill(self.proc.pid, signal.SIGINT)
    def is_running(self):
        return self.proc.is_alive()
    def wait(self, timeout: Optional[float] = None):
        self.proc.join(timeout)
    def kill(self):
        self.proc.kill()
    def finished(self):
        return self.proc.exitcode == 0


_streamlink = streamlink.Streamlink({
    'hds-timeout': 20.0,
    'hls-timeout': 20.0,
    'http-timeout': 20.0,
    'http-stream-timeout': 20.0,
    'stream-timeout': 20.0,
    'rtmp-timeout': 20.0,
})

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
            infile = None
            streams = None
            for i in range(1, self.resolv_retry_count + 1):
                try:
                    streams = _streamlink.streams(self.url)
                except:
                    pass
                if streams or i == self.resolv_retry_count:
                    break
                logger.debug(f'Failed to resolve {self.url}, retry #{i}')
                time.sleep(self.resolv_retry_interval)
            if not streams:
                logger.error(f'Failed to resolve {self.url}')
                return
            stream = streams['best']
            logger.debug(f'Streamlink stream: {stream}')
            written_bytes = 0
            infile = stream.open()
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
                            if type(stream) is streamlink.stream.HTTPStream:
                                logger.info(f'Streamlink reconnecting to stream {self.url}')
                                infile.close()
                                infile = stream.open()
                            else:
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
                    except streamlink.StreamError as e:
                        if self._interrupted:
                            break
                        if hasattr(e, 'err'):
                            if (
                                type(e.err) is requests.Timeout
                                and time.time() - last_active < self.stream_timeout
                            ):
                                logger.debug('streamlink stream read retry')
                                buffer = None
                                continue
                            elif type(e.err) is requests.HTTPError:
                                break
                        raise
            self._finished = True
        except Exception as e:
            logger.error(f'Failed to download {self.url}: {e}')
        finally:
            if infile:
                infile.close()
