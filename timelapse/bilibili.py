#!/usr/bin/python3
import re
import requests
import select
import socket
import json
import struct
import threading
import time
import errno
import zlib
import os
from typing import Optional

from .logger import logger
from .downloader import StreamlinkDownloader
from .status import status_add_watch

BILI_SOCK_HOST = 'broadcastlv.chat.bilibili.com'
BILI_SOCK_PORT = 2243
BILI_ROOM_URL = 'https://live.bilibili.com/{room_id}'
BILI_ROOM_INFO_URL = 'https://api.live.bilibili.com/xlive/web-room/v1/index/getInfoByRoom?room_id={room_id}'

class BilibiliLiveRoomWatcher:
    def __init__(
        self,
        room_id: int,
        download_path: str,
        title_filter: Optional[str] = None,
        *,
        heartbeat_interval: int = 30,
        error_recover_wait: int = 5,
        downloader = StreamlinkDownloader,
        started_download = None,
        post_download = None,
    ):
        logger.info(f'Monitoring room {room_id}')
        self.room_id = room_id
        self.download_path = download_path
        self.title_filter = title_filter and re.compile(title_filter)
        self.heartbeat_interval = heartbeat_interval
        self.heartbeat_received = time.time()
        self.error_recover_wait = error_recover_wait
        self.downloader = downloader
        self.started_download = started_download
        self.post_download = post_download
        self.conn: socket.socket = None
        self.dl_handle = None
        self.need_poll = False
        self.live_start_time = 0
        self.has_finished = False
        self.username = '<loading>'
        self.title = '<loading>'
        status_add_watch(self)
        self.reset()  # setup connection
        self.poll()
        self.thread = threading.Thread(target=self.mainloop)
        self.thread.start()
    def reset(self):
        try:
            logger.info(f'Reconnecting to room {self.room_id}')
            if self.conn:
                self.conn.close()
            self.buffer = b''
            self.conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.conn.connect((BILI_SOCK_HOST, BILI_SOCK_PORT))
            self.conn.setblocking(0)
            # join
            self.conn.sendall(bili_encode_packet(7, {  # join
                'uid': 0,
                'roomid': self.room_id,
                'protover': 2,
                'platform': 'web',
                'clientver': '1.10.6',
                'type': 2,
            }))
            self.next_heartbeat = time.time() + self.heartbeat_interval
            self.heartbeat_received = time.time()
        except:
            logger.exception('Failed to reconnect')
    def mainloop(self):
        while True:
            try:
                now = time.time()
                if now - self.heartbeat_received > self.heartbeat_interval * 3:
                    logger.info('No activity on room connection')
                    self.reset()
                    continue
                r, w, x = select.select([self.conn], [], [self.conn], self.next_heartbeat - now)
                if x:
                    self.reset()
                    continue
                if r:
                    while True:
                        try:
                            buf = self.conn.recv(8192)
                            if not buf:
                                break
                            self.buffer += buf
                        except socket.error as e:
                            err = e.args[0]
                            if err == errno.EAGAIN or err == errno.EWOULDBLOCK:
                                break
                            else:
                                self.reset()
                                break
                        self.handle_packets()
                    if not buf:  # disconnected
                        self.reset()
                        continue
                if self.dl_handle and not self.dl_handle.is_running():
                    self.need_poll = True
                if self.need_poll:
                    self.poll()
                if not r or time.time() + 0.5 > self.next_heartbeat:
                    self.heartbeat()
            except:
                logger.exception(f'Caught exception in main loop')
                time.sleep(self.error_recover_wait)
                self.reset()
    def poll(self):
        try:
            info = requests.get(BILI_ROOM_INFO_URL.format(room_id=self.room_id)).json()
            room_info = info['data']['room_info']
            self.username = info['data']['anchor_info']['base_info']['uname']
            self.title = room_info['title']
            if room_info['live_status'] == 1:  # living
                title = room_info['title']
                if self.live_start_time != room_info['live_start_time']:  # new stream
                    self.end_recording()
                    self.live_start_time = room_info['live_start_time']
                if not self.dl_handle:
                    if not self.title_filter or self.title_filter.search(title):
                        # start recording
                        logger.info(f'Room {self.room_id} started stream: {title}')
                        dirpath = os.path.join(self.download_path, str(self.live_start_time))
                        os.makedirs(dirpath, exist_ok=True)
                        self.dl_handle = self.downloader(
                            BILI_ROOM_URL.format(room_id=self.room_id),
                            dirpath=dirpath,
                        )
                        if self.started_download:
                            try:
                                self.started_download(self.room_id, dirpath)
                            except:
                                logger.exception(f'Started download hook error')
                    else:
                        logger.debug(f'Filtering out in room {self.room_id}: {title}')
                elif not self.dl_handle.is_running():  # dl_handle dead
                    if self.dl_handle.finished():
                        self.has_finished = True
                    logger.info(f'Downloader for room {self.room_id} dead, restarting (stream may be ended)')
                    self.dl_handle = self.downloader(
                        BILI_ROOM_URL.format(room_id=self.room_id),
                        dirpath=os.path.join(self.download_path, str(self.live_start_time)),
                    )
            else:
                self.end_recording()
            self.need_poll = False
        except:
            logger.exception(f'Failed to poll {self.room_id}')
    def end_recording(self):
        if self.dl_handle:
            dirpath = os.path.join(self.download_path, str(self.live_start_time))
            threading.Thread(
                target=self.finish_download,
                args=(self.dl_handle, dirpath, self.has_finished)
            ).start()
            self.dl_handle = None
        self.live_start_time = 0
        self.has_finished = False
    def heartbeat(self):
        self.conn.sendall(bili_encode_packet(2, b''))  # heartbeat
        self.next_heartbeat = time.time() + self.heartbeat_interval
    def handle_packets(self):
        while True:
            if len(self.buffer) < 16:
                return
            packet_len, = struct.unpack('>I', self.buffer[:4])
            if len(self.buffer) < packet_len:
                return
            self.heartbeat_received = time.time()
            packet_buf = self.buffer[:packet_len]
            self.buffer = self.buffer[packet_len:]
            proto, op, data = bili_decode_packet(packet_buf)
            logger.debug(f'{proto} {op} {data}')
            if proto == 2:
                self.buffer = data + self.buffer
                continue
            if op == 8:  # welcome
                self.need_poll = True
                self.heartbeat()
            elif op == 3:
                pass  # heartbeat reply?
            elif op == 5:
                if data['cmd'] in ['LIVE', 'ROUND', 'CLOSE', 'PREPARING', 'END', 'ROOM_CHANGE']:
                    self.need_poll = True
    def finish_download(self, dl_handle, dirpath, has_finished):
        finished = False
        try:
            logger.info(f'Waiting downloader to finish for room {self.room_id}')
            dl_handle.wait(45)
            if dl_handle.is_running():
                logger.info(f'Stopping downloader {self.room_id}')
                dl_handle.interrupt()
            dl_handle.wait()
            finished = has_finished or dl_handle.finished()
            if finished:
                logger.info(f'Finished downloading {self.room_id}')
        except:
            logger.exception(f'Failed to download {self.room_id}')
        finally:
            dl_handle.kill()
            if self.post_download:
                try:
                    self.post_download(self.room_id, dirpath, finished)
                except:
                    logger.exception('Post download hook error')
    def status(self):
        return [
            f'Bilibili Live Room {self.title} by {self.username} '
            + ('[live]' if self.live_start_time else '[offline]')
            + ('[recording]' if self.dl_handle else '')
        ]

def bili_encode_packet(type: int, data):
    if isinstance(data, bytearray):
        data = bytes(data)
    if isinstance(data, str):
        data = data.encode('utf8')
    if not isinstance(data, bytes):
        data = json.dumps(data).encode('utf8')
    head = struct.pack('>IHHII', len(data) + 16, 16, 1, type, 1)
    return head + data

def bili_decode_packet(buf):
    packet_len, unk1, protocol, operation, unk2 = struct.unpack('>IHHII', buf[:16])
    data = buf[16:]
    if protocol == 0:
        data = json.loads(data)
    elif protocol == 1 and len(data) == 4:
        data = struct.unpack('>I', data)
    elif protocol == 2:
        data = zlib.decompress(data)
    return protocol, operation, data