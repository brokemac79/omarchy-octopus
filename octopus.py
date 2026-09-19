#!/usr/bin/env python3
"""Standalone read-only Octopus adapter. Stdlib only; JSON stdout, no secrets."""
import argparse
import base64
from contextlib import contextmanager
import fcntl
import getpass
import http.client
import json
import math
import os
from pathlib import Path
import re
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API = 'https://api.octopus.energy'
# Per REST page or GraphQL response; read one extra byte to detect overflow.
MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MiB, before JSON parsing
REFRESH_TIMEOUT_SECONDS = 60  # Whole worker, including waiting for the cache lock.
UTC = timezone.utc
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'omarchy-octopus'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home()/'.cache')))/'omarchy-octopus'

class SafeError(Exception): pass
class RefreshTimeout(BaseException):
    """Must escape ordinary network/section fallback and release the worker lock."""


@contextmanager
def deadline(seconds=REFRESH_TIMEOUT_SECONDS):
    """Linux main-thread wall-clock budget; unlike socket timeouts, progress cannot reset it."""
    def expired(_signum, _frame):
        raise RefreshTimeout()
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextmanager
def cache_lock():
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(CACHE/'lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SafeError('Unexpected API redirect refused')


def read_json(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, RecursionError): return {}


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(data, f, allow_nan=False)
    os.replace(tmp, path)


def stamp(s):
    try:
        if not isinstance(s, str): raise ValueError()
        value = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError()
        return value.astimezone(UTC)
    except (ValueError, TypeError, OverflowError):
        raise SafeError('Invalid Octopus timestamp') from None


def iso(d): return d.astimezone(UTC).isoformat()


def number(value):
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)): raise ValueError()
        value = float(value)
        if not math.isfinite(value): raise ValueError()
        return value
    except (ValueError, TypeError, OverflowError):
        raise SafeError('Invalid Octopus numeric value') from None


def mapping(value):
    if not isinstance(value, dict): raise SafeError('Unexpected Octopus response structure')
    return value


def records(value):
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise SafeError('Unexpected Octopus response structure')
    return value


def normalized_rows(rows, rates=False):
    """Canonical UTC instants give consistent ordering/deduplication across DST offsets."""
    unique = {}
    for raw in records(rows):
        start = stamp(raw['start'])
        if start.second or start.microsecond or start.minute % 30:
            raise SafeError('Invalid Octopus half-hour interval')
        row = {'start': iso(start)}
        if rates:
            end = stamp(raw['end'])
            if end - start != timedelta(minutes=30):
                raise SafeError('Invalid Octopus half-hour interval')
            row.update(end=iso(end), price=number(raw['price']))
        else:
            row['value'] = number(raw['value'])
        if row['start'] in unique and unique[row['start']] != row:
            raise SafeError('Conflicting Octopus intervals')
        unique[row['start']] = row
    return sorted(unique.values(), key=lambda row: stamp(row['start']))


def normalized_section(name, value):
    if name == 'rates': return normalized_rows(value, rates=True)
    value = mapping(value)
    if name == 'live':
        return {'at': iso(stamp(value['at'])), 'watts': number(value['watts'])}
    if name == 'usage':
        start, end = stamp(value['start']), stamp(value['end'])
        if end <= start or (end-start).total_seconds() % 1800:
            raise SafeError('Invalid Octopus usage range')
        return {'rows': normalized_rows(value['rows']), 'start': iso(start), 'end': iso(end)}
    raise SafeError('Unknown Octopus section')


def covers_day(rows, start, end):
    expected = int((end-start).total_seconds()/1800)
    return len(rows) == expected and all(
        stamp(row['start']) == start+timedelta(minutes=30*i)
        and stamp(row['end']) == start+timedelta(minutes=30*(i+1))
        for i, row in enumerate(rows))


def request(url, payload=None, token=None, key=None):
    if urllib.parse.urlsplit(url).netloc != 'api.octopus.energy' or not url.startswith(API+'/'):
        raise SafeError('Unexpected API host refused')
    headers = {'User-Agent':'omarchy-octopus/0.1'}
    if token: headers['Authorization'] = 'JWT '+token
    if key: headers['Authorization'] = 'Basic '+base64.b64encode((key+':').encode()).decode()
    if payload is not None: headers['Content-Type'] = 'application/json'
    try:
        req=urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as r:
            body = r.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise SafeError('Octopus response exceeds 1 MiB limit; cached readings retained')
        data = mapping(json.loads(body))
        if data.get('errors'): raise SafeError('Octopus rejected the query; check account access')
        return data
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise SafeError('Octopus authentication failed') from None
        if e.code == 429: raise SafeError('Octopus rate limit; retrying later') from None
        raise SafeError('Octopus service error') from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ValueError, OSError, RecursionError):
        raise SafeError('Octopus unavailable; cached readings retained') from None


def pages(url, key=None):
    rows=[]
    for _ in range(10):
        d=request(url,key=key); rows.extend(records(d['results'])); url=d.get('next')
        if url is not None and not isinstance(url, str): raise SafeError('Invalid Octopus pagination')
        if not url: return rows
    raise SafeError('Incomplete API pagination')


def windows(rates, now, hours):
    count=hours*2; candidates=[]
    future=[r for r in normalized_rows(rates, rates=True) if stamp(r['start'])>=now]
    for i in range(len(future)-count+1):
        chunk=future[i:i+count]
        if any((stamp(r['end'])-stamp(r['start'])).total_seconds()!=1800 for r in chunk): continue
        if any(stamp(a['end'])!=stamp(b['start']) for a,b in zip(chunk,chunk[1:])): continue
        candidates.append({'hours':hours,'start':chunk[0]['start'],'end':chunk[-1]['end'],'price':sum(r['price'] for r in chunk)/count})
    return min(candidates,key=lambda r:r['price']) if candidates else None


def usage_summary(rows, rates, start, end):
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    rows=[r for r in normalized_rows(rows) if start<=stamp(r['start'])<end]
    price_by_time={stamp(r['start']):r['price'] for r in normalized_rows(rates, rates=True)}
    for row in rows:
        rate=price_by_time.get(stamp(row['start']))
        row['cost']=None if rate is None else row['value']*rate/100
    priced=[r for r in rows if stamp(r['start']) in price_by_time]
    expected=int((end-start).total_seconds()/1800)
    complete=len(rows)==expected and all(stamp(r['start'])==start+timedelta(minutes=30*i) for i,r in enumerate(rows))
    return {'rows':rows,'start':iso(start),'end':iso(end),'kwh':sum(r['value'] for r in rows),
            'cost':sum(r['value']*price_by_time[stamp(r['start'])]/100 for r in priced),
            'complete':complete,'costComplete':complete and len(priced)==len(rows),'expected':expected}


class Adapter:
    def __init__(self, refresh=False):
        self.cfg=read_json(CONFIG/'config.json'); self.creds=read_json(CONFIG/'credentials.json')
        cached=read_json(CACHE/'readings.json')
        self.cache={name:cached[name] for name in ('rates','live','usage') if name in cached}
        self.auth=read_json(CACHE/'auth.json')
        self.now=datetime.now(UTC)
        self.zone=ZoneInfo(self.cfg.get('timezone','Europe/London'))
        self.errors=[]
        self.refresh=refresh
    def graphql(self,q, authenticated=True):
        token=self.token() if authenticated else None
        return mapping(request(API+'/v1/graphql/',{'query':q},token=token)['data'])
    def token(self):
        try:
            if number(self.auth.get('expires',0))>time.time()+120 and isinstance(self.auth.get('token'),str):
                return self.auth['token']
        except SafeError:
            self.auth = {}
        key=self.creds.get('api_key')
        if not key: raise SafeError('Set up your Octopus API key for usage')
        data=self.graphql('mutation { obtainKrakenToken(input:{APIKey:'+json.dumps(key)+'}) { token } }',False)
        token=mapping(data['obtainKrakenToken'])['token']
        try:
            if not isinstance(token, str): raise ValueError()
            payload=token.split('.')[1]; claims=mapping(json.loads(base64.urlsafe_b64decode(payload+'='*((-len(payload))%4))))
            expires = number(claims['exp'])
            if expires <= time.time(): raise ValueError()
        except (ValueError, IndexError, KeyError):
            raise SafeError('Invalid Octopus authentication response') from None
        self.auth={'token':token,'expires':expires}
        private_write(CACHE/'auth.json',self.auth)
        return token
    def device(self):
        if isinstance(self.auth.get('device'),str) and self.auth['device']: return self.auth['device']
        account=self.creds.get('account_number','')
        q='query { account(accountNumber:'+json.dumps(account)+') { electricityAgreements(active:true) { meterPoint { mpan meters(includeInactive:false) { smartImportElectricityMeter { deviceId } } } } } }'
        a=self.graphql(q)
        ids=[]
        for agreement in records(mapping(a['account'])['electricityAgreements']):
            p=mapping(agreement['meterPoint'])
            if self.creds.get('mpan') and str(p['mpan'])!=str(self.creds['mpan']): continue
            for meter in records(p['meters']):
                smart = meter.get('smartImportElectricityMeter')
                if smart is None: continue
                device = mapping(smart)['deviceId']
                if not isinstance(device,str) or not device: raise SafeError('Invalid Octopus meter device')
                ids.append(device)
        ids=list(set(ids))
        if len(ids)!=1: raise SafeError('Select one smart electricity meter in account settings')
        self.auth['device']=ids[0]; private_write(CACHE/'auth.json',self.auth)
        return ids[0]
    def telemetry(self,start,end,group):
        q='query { smartMeterTelemetry(deviceId:'+json.dumps(self.device())+', grouping:'+group+', start:'+json.dumps(iso(start))+', end:'+json.dumps(iso(end))+') { readAt consumptionDelta demand } }'
        raw = self.graphql(q)['smartMeterTelemetry']
        return [] if raw is None else records(raw)
    def rates(self):
        tariff=self.cfg.get('tariff',''); product=self.cfg.get('product','')
        if not re.fullmatch(r'[A-Z0-9-]+',tariff) or not re.fullmatch(r'[A-Z0-9-]+',product): raise SafeError('Configure your Agile product and tariff')
        local=self.now.astimezone(self.zone); midnight=local.replace(hour=0,minute=0,second=0,microsecond=0)
        # Standard-meter readings can lag by days. Cover the entire three-day
        # consumption query below so available historical readings remain priced.
        params=urllib.parse.urlencode({'period_from':iso(midnight-timedelta(days=3)),'period_to':iso(midnight+timedelta(days=2)),'page_size':200})
        raw=pages(f'{API}/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/?{params}')
        return normalized_rows([{'start':r['valid_from'],'end':r['valid_to'],'price':r['value_inc_vat']} for r in records(raw)], rates=True)
    def live(self):
        raw=self.telemetry(self.now-timedelta(minutes=5),self.now,'TEN_SECONDS')
        raw=[normalized_section('live', {'at':r['readAt'], 'watts':r['demand']}) for r in records(raw) if r.get('demand') is not None]
        if not raw: raise SafeError('No recent Home Mini readings')
        return max(raw,key=lambda r:stamp(r['at']))
    def usage(self):
        end=self.now.replace(minute=(self.now.minute//30)*30,second=0,microsecond=0); start=end-timedelta(hours=24)
        if self.cfg.get('home_mini',True):
            raw=self.telemetry(start,end,'HALF_HOURLY')
            rows=[{'start':r['readAt'],'value':number(r['consumptionDelta'])/1000} for r in records(raw) if r.get('consumptionDelta') is not None]
        else:
            mpan=self.creds.get('mpan',''); serial=self.creds.get('electricity_serial','')
            if not re.fullmatch(r'[0-9]+',mpan) or not re.fullmatch(r'[A-Za-z0-9]+',serial): raise SafeError('Configure your meter for usage')
            params=urllib.parse.urlencode({'period_from':iso(start-timedelta(days=2)),'period_to':iso(end),'page_size':200})
            raw=pages(f'{API}/v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/?{params}',key=self.creds.get('api_key'))
            rows=[{'start':r['interval_start'],'value':number(r['consumption'])} for r in records(raw)]
            if rows:
                end=min(end,max(stamp(r['start']) for r in rows)+timedelta(minutes=30));start=end-timedelta(hours=24)
        return normalized_section('usage', {'rows':rows,'start':iso(start),'end':iso(end)})
    def section(self,name,ttl,loader):
        entry=self.cache.get(name,{})
        try:
            entry = dict(mapping(entry))
            if 'data' in entry: entry['data'] = normalized_section(name, entry['data'])
            entry['fetched'] = number(entry.get('fetched',0))
            entry['retryAfter'] = number(entry.get('retryAfter',0))
            if entry['fetched'] < 0 or entry['fetched'] > time.time()+60 or entry['retryAfter'] > time.time()+60:
                raise SafeError('Invalid cache freshness')
            if 'error' in entry and not isinstance(entry['error'],str): raise SafeError('Invalid cache error')
        except (SafeError, KeyError, TypeError, ValueError, OverflowError):
            # A previously poisoned section must not prevent other sections or recovery.
            entry = {}
        self.cache[name] = entry
        if (time.time()-entry.get('fetched',0)>=ttl or (self.refresh and time.time()-entry.get('fetched',0)>=30)) and time.time()>=entry.get('retryAfter',0):
            try:
                entry={'data':normalized_section(name, loader()),'fetched':time.time()}
            except (SafeError,KeyError,TypeError,ValueError,OverflowError,RecursionError) as e:
                msg=str(e) if isinstance(e,SafeError) else 'Unexpected Octopus response'
                entry={**entry,'error':msg,'retryAfter':time.time()+60}
                if 'authentication' in msg.lower() or 'rejected' in msg.lower():
                    self.auth={};private_write(CACHE/'auth.json',{})
            self.cache[name]=entry
        if entry.get('error'): self.errors.append(name+': '+entry['error'])
        return entry.get('data')
    def snapshot(self):
        if not self.creds: self.cache.pop('usage',None)
        if not self.creds or not self.cfg.get('home_mini',True): self.cache.pop('live',None)
        rates=self.section('rates',900,self.rates) or []
        live=self.section('live',15,self.live) if self.creds and self.cfg.get('home_mini',True) else None
        usage=self.section('usage',300,self.usage) if self.creds else None
        current=next((r for r in rates if stamp(r['start'])<=self.now<stamp(r['end'])),None)
        today=self.now.astimezone(self.zone).date(); tomorrow=today+timedelta(days=1)
        days={}
        for label,day in [('today',today),('tomorrow',tomorrow)]:
            rows=[r for r in rates if stamp(r['start']).astimezone(self.zone).date()==day]
            midnight=datetime.combine(day,datetime.min.time(),self.zone)
            expected=int(((midnight+timedelta(days=1)).astimezone(UTC)-midnight.astimezone(UTC)).total_seconds()/1800)
            for row in rows:
                row['label']=stamp(row['start']).astimezone(self.zone).strftime('%H:%M')
                row['endLabel']=stamp(row['end']).astimezone(self.zone).strftime('%H:%M')
            days[label]={'date':str(day),'rows':rows,'expected':expected,'complete':covers_day(rows,midnight.astimezone(UTC),(midnight+timedelta(days=1)).astimezone(UTC))}
        age=None if not live else max(0,(self.now-stamp(live['at'])).total_seconds())
        usage_result=usage_summary(usage['rows'],rates,stamp(usage['start']),stamp(usage['end'])) if usage else None
        if usage_result:
            for row in usage_result['rows']:
                row['label']=stamp(row['start']).astimezone(self.zone).strftime('%H:%M')
                row['endLabel']=(stamp(row['start'])+timedelta(minutes=30)).astimezone(self.zone).strftime('%H:%M')
            usage_result['label']=stamp(usage_result['start']).astimezone(self.zone).strftime('%a %d %b %H:%M')+' → '+stamp(usage_result['end']).astimezone(self.zone).strftime('%a %d %b %H:%M')
        best=[w for h in [1,2,3] if (w:=windows(rates,self.now,h))]
        for w in best:
            w['label']=stamp(w['start']).astimezone(self.zone).strftime('%a %d %b  %H:%M')+'–'+stamp(w['end']).astimezone(self.zone).strftime('%H:%M')
        if current: current['endLabel']=stamp(current['end']).astimezone(self.zone).strftime('%H:%M')
        result = {'schema':1,'fetchedAt':iso(self.now),'timezone':str(self.zone),'tariff':self.cfg.get('tariff',''),
                'current':current,'live':live,'liveStale':age is None or age>120,'liveAge':age,
                'days':days,'usage':usage_result,'windows':best,
                'errors':self.errors,'configured':bool(self.creds),'homeMini':self.cfg.get('home_mini',True),
                'updated':{k:v.get('fetched') for k,v in self.cache.items()}}
        # Validate derived arithmetic too before committing newly fetched cache data.
        json.dumps(result, allow_nan=False)
        private_write(CACHE/'readings.json',self.cache)
        return result


def setup():
    print('Octopus Energy setup. Values remain in private local files.')
    product=input('Agile product [AGILE-24-10-01]: ').strip() or 'AGILE-24-10-01'
    tariff=input('Full tariff code (e.g. E-1R-AGILE-24-10-01-J): ').strip()
    mini=input('Home Mini? [y/N]: ').lower().startswith('y')
    key=getpass.getpass('Octopus API key (blank for prices only): ').strip()
    cfg={'product':product,'tariff':tariff,'timezone':'Europe/London','home_mini':mini}
    creds={}
    if key:
        creds={'api_key':key,'account_number':input('Account number: ').strip(),'mpan':input('Electricity MPAN: ').strip(),'electricity_serial':input('Meter serial (optional with Home Mini): ').strip()}
    # No settings change until all prompts finish. Wait behind existing readers,
    # then keep exclusive ownership across cache invalidation and replacement.
    # Only lock acquisition is timed: don't interrupt the local file transaction.
    CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(CACHE/'lock','w') as lock:
        with deadline(REFRESH_TIMEOUT_SECONDS):
            fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            # Invalidate first: even an I/O failure cannot pair a new key with an old JWT.
            for name in ['auth.json','readings.json']: (CACHE/name).unlink(missing_ok=True)
            if creds: private_write(CONFIG/'credentials.json',creds)
            else: (CONFIG/'credentials.json').unlink(missing_ok=True)
            private_write(CONFIG/'config.json',cfg)
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)
    print('Saved. Enable or refresh the Omarchy plugin.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--setup',action='store_true');p.add_argument('--refresh',action='store_true');args=p.parse_args()
    try:
        if args.setup: setup();return 0
        with deadline(REFRESH_TIMEOUT_SECONDS):
            with cache_lock():
                result=json.dumps(Adapter(args.refresh).snapshot(),allow_nan=False)
            print(result)
        return 0
    except RefreshTimeout:
        # Empty output and nonzero exit leave Quickshell's last complete report intact.
        if args.setup: print('Setup timed out waiting for an active refresh; settings unchanged.')
        return 1
    except Exception:
        if args.setup:
            print('Unable to save Octopus settings. Check local file permissions and retry.')
        else:
            print(json.dumps({'errors':['Unable to load Octopus settings or data'],'liveStale':True}))
        return 1

if __name__=='__main__': raise SystemExit(main())
