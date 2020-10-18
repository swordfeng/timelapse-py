#!/usr/bin/python3
import threading
import time
import random
import os
from tzcron import Schedule

from .logger import logger
from .downloader import StreamlinkDownloader
from .status import status_add_watch

class StreamUrlWatcher:
    def __init__(
        self,
        url: str,
        download_path: str,
        schedule: Schedule,
        duration: int,
        *,
        scheduler_interval: int = 15,
        started_download = None,
        post_download = None,
    ):
        logger.info(f'Monitoring URL {url}')
        self.url = url
        self.download_path = download_path
        self.duration = duration
        self.schedule = schedule
        self.scheduler_interval = scheduler_interval
        self.started_download = started_download
        self.post_download = post_download
        self.dl_handle = None
        self.finished = False
        self.next_run = None
        self.thread = threading.Thread(target=self.mainloop)
        self.thread.start()
        status_add_watch(self)
    def mainloop(self):
        for next_run in self.schedule:
            self.next_run = next_run
            try:
                sec = next_run.timestamp() - time.time()
                while sec > -self.duration:
                    if sec > 3600:
                        time.sleep(3600 - 120 + random.randrange(60))
                    elif sec > self.scheduler_interval + 1:
                        time.sleep(self.scheduler_interval)
                    elif sec > 0:
                        time.sleep(sec)
                    else:
                        logger.info(f'URL stream started: {self.url}')
                        dirpath = os.path.join(self.download_path, next_run.strftime('%Y%m%d_%H%M%S_%Z'))
                        os.makedirs(dirpath, exist_ok=True)
                        self.finished = False
                        self.dl_handle = StreamlinkDownloader(
                            self.url,
                            dirpath=dirpath,
                        )
                        if self.started_download:
                            try:
                                self.started_download(self.url, dirpath)
                            except:
                                logger.exception(f'Started download hook error')
                        self.dl_handle.wait(self.duration + sec)
                        if self.dl_handle.is_running():
                            logger.info(f'Stopping downloader {self.url}')
                            self.dl_handle.interrupt()
                            self.finished = True
                            break
                        else:
                            logger.warn(f'Downloader aborted: {self.url}')
                        self.dl_handle.wait()
                    sec = next_run.timestamp() - time.time()
            except:
                logger.exception(f'Unknown error')
            finally:
                if self.dl_handle:
                    self.dl_handle.kill()
                    self.dl_handle = None
                    if self.post_download:
                        try:
                            self.post_download(self.url, dirpath, self.finished)
                        except:
                            logger.exception('Post download hook error')
    def status(self):
        return [
            f'URL Stream {self.url} scheduled at {self.next_run} '
            + ('[recording]' if self.dl_handle else '[idle]')
        ]