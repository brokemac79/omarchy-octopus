#!/usr/bin/env python3
"""Generate synthetic screenshot data. Never reads configuration or calls Octopus."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from octopus import Adapter, iso

now = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)
a = Adapter.__new__(Adapter)
a.now = now
a.zone = ZoneInfo('Europe/London')
a.cfg = {'home_mini': True}
a.creds = {'demo': True}
a.errors = []
a.refresh = False
start = now.replace(hour=0) - timedelta(hours=1)
rates = []
for i in range(-48, 96):
    t = start + timedelta(minutes=30*i)
    slot = i % 48
    price = -6.5 if 2 <= slot < 10 else 32.4 if 32 <= slot < 39 else 14.2
    rates.append({'start': iso(t), 'end': iso(t+timedelta(minutes=30)), 'price': price})
usage = []
for i in range(48):
    t = now-timedelta(hours=24)+timedelta(minutes=30*i)
    cheap = 1 <= t.astimezone(a.zone).hour < 5
    usage.append({'start': iso(t), 'value': 2.8 if cheap else 0.18+(i%5)*0.04})
data = {'rates': rates, 'live': {'at': iso(now-timedelta(seconds=10)), 'watts': 642},
        'usage': {'rows': usage, 'start': iso(now-timedelta(hours=24)), 'end': iso(now)}}
a.cache = {k: {'data': v, 'fetched': now.timestamp()} for k,v in data.items()}
a.section = lambda name, ttl, loader: data[name]
with patch('octopus.private_write'):
    print(json.dumps(a.snapshot(), indent=2))
