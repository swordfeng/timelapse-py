#!/usr/bin/python3
import youtube_dl
import time
import subprocess
import os
import objectpath
import requests
import sys
import logging
import itertools
import threading
import json

logger = logging.getLogger('timelapse')
loghandler = logging.StreamHandler()
loghandler.setFormatter(logging.Formatter('[%(name)s][%(levelname)s] %(message)s'))
logger.addHandler(loghandler)
logger.setLevel(logging.DEBUG)

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

# download_ytdl('https://www.bilibili.com/video/BV1vJ411w7Qb', '/tmp')
# download_youget('https://www.bilibili.com/video/BV1vJ411w7Qb', '/tmp')

YOUTUBE_CLIENT_VERSION = '2.20200623.04.00'
YOUTUBE_KEY = 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
YOUTUBE_COMMON_HEADERS = {
    'x-youtube-client-name': '1',
    'x-youtube-client-version': YOUTUBE_CLIENT_VERSION,
}
YOUTUBE_CHANNEL_DATA = 'https://www.youtube.com/channel/{channel_id}?pbj=1'
YOUTUBE_LIVE_HEARTBEAT = f'https://www.youtube.com/youtubei/v1/player/heartbeat?alt=json&key={YOUTUBE_KEY}'
YOUTUBE_VIDEO_URL = 'https://www.youtube.com/watch?v={video_id}'

class YoutubeChannelWatcher:
    def __init__(
        self,
        channel_id: str,
        download_path: str,
        upcoming_heartbeat_interval: int = 15,
        upcoming_max_countdown_time: int = 300,
        poll_mode: bool = False,
        poll_interval: int = 900,
    ):
        self.channel_id = channel_id
        self.upcoming_heartbeat_interval = upcoming_heartbeat_interval
        self.upcoming_max_countdown_time = upcoming_max_countdown_time
        self.download_path = download_path
        self.tracking = set()
        self.lock = threading.RLock()
        # initial poll
        self.poll()
        # repeated poll in polling mode
        if poll_mode:
            self.poll_thread = threading.Thread(target=self.run_poll, args=(poll_interval,))
            self.poll_thread.start()

    def watch_video(self, video_id: str):
        self.tracking.add(video_id)
        YoutubeLivestreamWatcher(
            video_id=video_id,
            channel_watcher=self,
            download_path=self.download_path,
            heartbeat_interval=self.upcoming_heartbeat_interval
        )

    def poll(self):
        logger.debug(f'Polling channel {self.channel_id}')
        channel_data = requests.get(
            YOUTUBE_CHANNEL_DATA.format(channel_id=self.channel_id),
            headers=YOUTUBE_COMMON_HEADERS
        ).json()
        optree = objectpath.Tree(channel_data)
        upcoming_max_time = int(time.time()) + self.upcoming_max_countdown_time
        pollres = set()
        with self.lock:
            for video_data in itertools.chain(
                optree.execute(f'$..*[int(@.upcomingEventData.startTime) < {upcoming_max_time}]'),
                optree.execute('$..*["BADGE_STYLE_TYPE_LIVE_NOW" in @.badges..style]'),
            ):
                video_id = video_data['videoId']
                if video_id in self.tracking or video_id in pollres:
                    # already tracked, pass
                    continue
                pollres.add(video_id)
                logger.debug(f'Polling found {video_id}: {video_data["title"]["simpleText"]}')
        for video_id in pollres:
            self.watch_video(video_id)

    def run_poll(self, interval: int):
        while True:
            try:
                time.sleep(interval)
                self.poll()
            except:
                logger.exception('Polling error')


class YoutubeLivestreamWatcher:
    def __init__(
        self,
        video_id: str,
        channel_watcher: YoutubeChannelWatcher,
        download_path: str,
        heartbeat_interval: int,
    ):
        self.video_id = video_id
        self.channel_watcher = channel_watcher
        self.heartbeat_interval = heartbeat_interval
        self.download_path = os.path.join(download_path, video_id)
        self.watch_thread = threading.Thread(target=self.run_watch)
        self.watch_thread.start()
    def run_watch(self):
        try:
            while True:
                try:
                    status_data = requests.post(
                        YOUTUBE_LIVE_HEARTBEAT,
                        headers=YOUTUBE_COMMON_HEADERS,
                        json={
                            "videoId": self.video_id,
                            "context": {
                                "client": {
                                    "clientName": "WEB",
                                    "clientVersion": YOUTUBE_CLIENT_VERSION
                                }
                            },
                            "heartbeatRequestParams": {
                                "heartbeatChecks": [
                                    "HEARTBEAT_CHECK_TYPE_LIVE_STREAM_STATUS"
                                ]
                            }
                        },
                    ).json()
                    if 'error' in status_data:
                        logger.error('Server error: ' + status_data['error']['message'])
                        return
                    status = status_data['playabilityStatus']['status']
                    if status == 'LIVE_STREAM_OFFLINE':
                        pass
                    elif status == 'OK':
                        break
                    else:
                        logger.error(f'Video {self.video_id} unknown status: {status}')
                        return
                except:
                    logger.exception('Failed checking video status')
                time.sleep(self.heartbeat_interval)
            os.makedirs(self.download_path, exist_ok=True)
            download_ytdl(
                YOUTUBE_VIDEO_URL.format(video_id=self.video_id),
                self.download_path,
            )
            # todo: after-download hook
        except:
            logger.exception('Failed to download video stream')
        finally:
            with self.channel_watcher.lock:
                self.channel_watcher.tracking.discard(self.video_id)

# YoutubeChannelWatcher('UC5CwaMl1eIgY8h02uZw7u8A').poll()
# YoutubeChannelWatcher('UCIG9rDtgR45VCZmYnd-4DUw', 'videos')