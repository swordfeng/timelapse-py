#!/usr/bin/python3

from timelapse import *

webhook = YoutubeWebhook(
    ('127.0.0.1', 18001),
    'https://<webhook-url>',
)

channels = (
    ('UCp6993wxpyDPHUpavwDFqgg', 'videos/sora'),
    ('UCDqI2jOz0weumE8s7paEk6g', 'videos/roboco'),
    ('UC-hM6YJuNYVAmUWxeIr9FeA', 'videos/miko'),
    ('UC5CwaMl1eIgY8h02uZw7u8A', 'videos/suisei'),

    ('UC0TXe_LYZ4scaW2XMyi5_kw', 'videos/azki'),

    ('UCD8HOxPs4Xvsm8H0ZxXGiBw', 'videos/mel'),
    ('UCQ0UDLQCjY0rmuxCDE38FGg', 'videos/matsuri'),
    ('UC1CfXB_kRs3C-zaeTG3oGyg', 'videos/haato'),
    ('UCHj_mh57PVMXhAUDphUQDFA', 'videos/haato'),
    ('UCFTLzh12_nrtzqBPsTCqenA', 'videos/akirose'),
    ('UCLbtM3JZfRTg8v2KGag-RMw', 'videos/akirose'),
    ('UCdn5BQ06XqgXoAxIhbqw5Rg', 'videos/fubuki'),

    ('UC1opHUrw8rvnsadT-iGp7Cg', 'videos/aqua'),
    ('UCXTpFs_3PqI41qX2d9tL2Rw', 'videos/shion'),
    ('UC7fk0CB07ly8oSl0aqKkqFg', 'videos/ayame'),
    ('UC1suqwovbL1kzsoaZgFZLKg', 'videos/choco'),
    ('UCp3tgHXw_HI0QMk1K8qh3gQ', 'videos/choco'),
    ('UCvzGlP9oQwU--Y0r9id_jnA', 'videos/subaru'),

    ('UCp-5t9SrOQwXMU7iIjQfARg', 'videos/mio'),
    ('UCvaTdHTWBGv3MKj3KVqJVCw', 'videos/okayu'),
    ('UChAnqc_AY5_I3Px5dig3X1Q', 'videos/korone'),

    ('UC1DCedRgGHBdm81E1llLhOQ', 'videos/pekora'),
    ('UCl_gCybOJRIgOXw6Qb4qJzQ', 'videos/rushia'),
    ('UCvInZx9h3jC2JzsIzoOebWg', 'videos/flare'),
    ('UCdyqAaZDKHXg4Ahi7VENThQ', 'videos/noel'),
    ('UCCzUftO8KOVkV4wQG1vkUvg', 'videos/marine'),

    ('UCZlDXzGoo7d44bwdNObFacg', 'videos/kanata'),
    ('UCS9uQI-jC3DE0L4IpXyvr6w', 'videos/coco'),
    ('UCqm3BQLlJfvkTsX_hvm0UmA', 'videos/watame'),
    ('UC1uv2Oq6kNxgATlCiez59hw', 'videos/towa'),
    ('UCa9Y57gfeY0Zro_noHRVrnw', 'videos/luna'),
)

for channel_id, path in channels:
    YoutubeChannelWatcher(channel_id, path, webhook=webhook)

check_status()
