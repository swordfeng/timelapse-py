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
import http
import urllib.parse
import xml.etree.ElementTree as ET
import multiprocessing
import signal
from datetime import datetime
from typing import Tuple

from .logger import logger
from .downloader import download_ytdl

YOUTUBE_CLIENT_VERSION = '2.20200623.04.00'
YOUTUBE_COMMON_HEADERS = {
    'x-youtube-client-name': '1',
    'x-youtube-client-version': YOUTUBE_CLIENT_VERSION,
}
YOUTUBE_CHANNEL_DATA = 'https://www.youtube.com/channel/{channel_id}?pbj=1'
YOUTUBE_LIVE_HEARTBEAT = 'https://www.youtube.com/youtubei/v1/player/heartbeat?alt=json&key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'
YOUTUBE_VIDEO_URL = 'https://www.youtube.com/watch?v={video_id}'
YOUTUBE_FEED_HUB = 'http://pubsubhubbub.appspot.com'
YOUTUBE_CHANNEL_FEED_URL = 'https://www.youtube.com/xml/feeds/videos.xml?channel_id={channel_id}'

class YoutubeChannelWatcher:
    def __init__(
        self,
        channel_id: str,
        download_path: str,
        *,
        heartbeat_interval: int = 15,
        upcoming_poll_start: int = 300,
        poll_mode: bool = False,
        poll_interval: int = 900,
        webhook = None,
        post_download = None,
    ):
        self.channel_id = channel_id
        self.heartbeat_interval = heartbeat_interval
        self.upcoming_poll_start = upcoming_poll_start
        self.download_path = download_path
        self.post_download = post_download
        self.tracking = {}
        self.lock = threading.RLock()
        # repeated poll in polling mode
        if poll_mode:
            logger.info(f'Monitoring channel {channel_id} using polling')
            self.poll_thread = threading.Thread(target=self.run_poll, args=(poll_interval,))
            self.poll_thread.start()
        else:
            assert webhook
        if webhook:
            logger.info(f'Monitoring channel {channel_id} using webhook')
            webhook.subscribe(channel_id, self)
        # initial poll
        try:
            self.poll()
        except:
            logger.exception('Polling error')

    def watch_video(self, video_id: str):
        with self.lock:
            if video_id in self.tracking:
                self.tracking[video_id].force_refresh = True
            else:
                self.tracking[video_id] = YoutubeLivestreamWatcher(
                    video_id=video_id,
                    download_path=self.download_path,
                    heartbeat_interval=self.heartbeat_interval,
                    upcoming_poll_start=self.upcoming_poll_start,
                    channel_watcher=self,
                    post_download=self.post_download,
                )

    def finish_tracking(self, video_id: str):
        with self.lock:
            del self.tracking[self.video_id]

    def poll(self):
        logger.debug(f'Polling channel {self.channel_id}')
        channel_data = requests.get(
            YOUTUBE_CHANNEL_DATA.format(channel_id=self.channel_id),
            headers=YOUTUBE_COMMON_HEADERS
        ).json()
        logger.debug(channel_data)
        optree = objectpath.Tree(channel_data)
        pollres = set()
        with self.lock:
            for video_data in itertools.chain(
                optree.execute(f'$..*[int(@.upcomingEventData.startTime) > 0]'),
                optree.execute('$..*["BADGE_STYLE_TYPE_LIVE_NOW" in @.badges..style]'),
            ):
                video_id = video_data['videoId']
                if video_id in self.tracking or video_id in pollres:
                    # already tracked, pass
                    continue
                pollres.add(video_id)
                logger.info(f'Polling found {video_id}: {video_data["title"]["simpleText"]}')
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
        download_path: str,
        heartbeat_interval: int,
        upcoming_poll_start: int,
        channel_watcher: YoutubeChannelWatcher = None,
        post_download = None,
    ):
        logger.info(f'Tracking video {video_id}')
        self.video_id = video_id
        self.channel_watcher = channel_watcher
        self.heartbeat_interval = heartbeat_interval
        self.download_path = os.path.join(download_path, video_id)
        self.upcoming_poll_start = upcoming_poll_start
        self.post_download = post_download
        self.scheduled_time = 0
        self.last_poll = 0
        self.force_refresh = True
        self.finished = False
        self.watch_thread = threading.Thread(target=self.run_watch)
        self.watch_thread.start()

    def poll_heartbeat(self):
        logger.debug(f'Polling stream {self.video_id}')
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
        logger.debug(status_data)
        return status_data

    def run_watch(self):
        download_proc = None
        try:
            while True:
                now = time.time()
                if (
                    not self.force_refresh
                    and self.scheduled_time - now > self.upcoming_poll_start
                    and now - self.last_poll < (
                        1200 if self.scheduled_time - now < 86400
                        else 12 * 3600
                    )
                ):
                    time.sleep(self.heartbeat_interval)
                    continue
                self.force_refresh = False
                self.last_poll = now
                try:
                    status_data = self.poll_heartbeat()
                    if 'error' in status_data:
                        logger.error('Server error: ' + status_data['error']['message'])
                        return
                    status = status_data['playabilityStatus']['status']
                    if status == 'LIVE_STREAM_OFFLINE':
                        renderer = status_data['playabilityStatus']['liveStreamability']['liveStreamabilityRenderer']
                        if 'displayEndscreen' in renderer and renderer['displayEndscreen']:
                            # old recorded live video
                            return
                        scheduled_time = int(renderer['offlineSlate']['liveStreamOfflineSlateRenderer']['scheduledStartTime'])
                        if self.scheduled_time != scheduled_time:
                            self.scheduled_time = scheduled_time
                            logger.info(f'Video {self.video_id} scheduled at {datetime.fromtimestamp(scheduled_time)}')
                    elif status == 'OK':
                        if 'liveStreamability' not in status_data['playabilityStatus']:
                            # uploaded video, not live
                            return
                        # start download now
                        break
                    else:
                        logger.error(f'Video {self.video_id} unknown status: {status}')
                        return
                except:
                    logger.exception('Failed checking video status')
                time.sleep(self.heartbeat_interval)
            logger.info(f'Start downloading {self.video_id}')
            os.makedirs(self.download_path, exist_ok=True)
            download_proc = multiprocessing.Process(
                target=download_ytdl,
                args=(
                    YOUTUBE_VIDEO_URL.format(video_id=self.video_id),
                    self.download_path,
                ),
            )
            download_proc.start()
            # continue heartbeat
            while download_proc.is_alive():
                time.sleep(self.heartbeat_interval)
                try:
                    status_data = self.poll_heartbeat()
                    status = status_data['playabilityStatus']['status']
                    if status == 'LIVE_STREAM_OFFLINE':
                        renderer = status_data['playabilityStatus']['liveStreamability']['liveStreamabilityRenderer']
                        if 'displayEndscreen' in renderer and renderer['displayEndscreen']:
                            # streaming ended
                            break
                except:
                    logger.exception('Failed checking video status')
            if download_proc.is_alive():
                logger.info(f'Waiting downloader to finish {self.video_id}')
                download_proc.join(45)
            if download_proc.is_alive():
                logger.info(f'Send SIGINT to downloader {self.video_id}')
                os.kill(download_proc.pid, signal.SIGINT)
                download_proc.join(15)
            if download_proc.is_alive():
                logger.info(f'Kill downloader {self.video_id}')
                download_proc.kill()
            download_proc.join()
            if download_proc.exitcode != 0:
                logger.error(f'Downloader exited with code {download_proc.exitcode}')
                return
            logger.info(f'Finished downloading {self.video_id}')
            self.finished = True
        except:
            logger.exception('Failed to download video stream')
        finally:
            if self.channel_watcher:
                self.channel_watcher.finish_tracking(self.video_id)
            if download_proc and download_proc.is_alive():
                download_proc.kill()
            if self.post_download:
                try:
                    self.post_download(self.finished, self.download_path)
                except:
                    logger.exception('Post download hook error')


class YoutubeWebhook:
    def __init__(
        self,
        server_addr: Tuple[str, int],
        webhook_url: str,
    ):
        self.webhook_url = webhook_url
        self.watchers = {}
        self.lock = threading.RLock()
        self.server = http.server.HTTPServer(server_addr, self.get_webhook_handler())
        self.server_thread = threading.Thread(target=self.server.serve_forever)
        self.server_thread.start()
        self.keep_alive = threading.Thread(target=self.subscribe_keep_alive)
        self.keep_alive.start()
        logger.info('Started serving youtube webhook')

    def subscribe(self, channel_id: str, watcher):
        resp = requests.post(
            YOUTUBE_FEED_HUB,
            data={
                'hub.callback': self.webhook_url, 
                'hub.mode': 'subscribe',
                'hub.verify': 'sync',
                'hub.topic': YOUTUBE_CHANNEL_FEED_URL.format(channel_id=channel_id),
                'hub.lease_seconds': 86400 * 5,
            },
        )
        resp.raise_for_status()
        with self.lock:
            self.watchers[channel_id] = watcher
        logger.info(f'Subscribed to channel {channel_id}')

    def subscribe_keep_alive(self):
        while True:
            time.sleep(86400)
            with self.lock:
                cids = list(self.watchers.keys())
            for channel_id in cids:
                try:
                    with self.lock:
                        self.subscribe(channel_id, self.watchers[channel_id])
                except:
                    logger.exception('Re-subscribing error')
                finally:
                    time.sleep(5)

    def get_webhook_handler(self):
        webhook = self
        class YoutubeWebhookHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                url = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(url.query)
                if 'hub.challenge' in qs:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(qs['hub.challenge'][0].encode('utf8'))
                    return
                self.send_response(400)
                self.end_headers()
            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length'])).decode('utf8')
                logger.debug(data)
                xmldata = ET.fromstring(data)
                for entry in xmldata.iter('{http://www.w3.org/2005/Atom}entry'):
                    video_id = entry.find('{http://www.youtube.com/xml/schemas/2015}videoId').text
                    channel_id = entry.find('{http://www.youtube.com/xml/schemas/2015}channelId').text
                    title = entry.find('{http://www.w3.org/2005/Atom}title').text
                    logger.info(f'Push notification {video_id}: {title}')
                    with webhook.lock:
                        if channel_id in webhook.watchers:
                            webhook.watchers[channel_id].watch_video(video_id)
                        else:
                            pass
                self.send_response(200)
                self.end_headers()
        return YoutubeWebhookHandler