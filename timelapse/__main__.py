#!/usr/bin/python3
import youtube_dl
import time
import subprocess
import os
import sys
import logging

logger = logging.getLogger('timelapse')
loghandler = logging.StreamHandler()
loghandler.setFormatter(logging.Formatter('[%(name)s][%(levelname)s] %(message)s'))
logger.addHandler(loghandler)
logger.setLevel(logging.INFO)

def download_ytdl(url, dirpath):
    ydl_opts = {
        'writeinfojson': True,
        'outtmpl': os.path.join(dirpath, '%(id)s.%(ext)s'),
        'postprocessor_args': ['-loglevel', 'panic'],
    }
    with youtube_dl.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

def download_youget(url, dirpath):
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