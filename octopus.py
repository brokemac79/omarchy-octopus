#!/usr/bin/env python3
"""Standalone read-only Octopus adapter. Stdlib only; JSON stdout, no secrets."""
import argparse
import base64
import fcntl
import getpass
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

API = 'https://api.octopus.energy'
UTC = timezone.utc
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home()/'.config')))/'omarchy-octopus'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home()/'.cache')))/'omarchy-octopus'

class SafeError(Exception): pass
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SafeError('Unexpected API redirect refused')


def read_json(path):
    try: return json.loads(path.read_text())
    except (OSError, ValueError): return {}


def private_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f: json.dump(data, f)
    os.replace(tmp, path)


def stamp(s): return datetime.fromisoformat(s.replace('Z', '+00:00'))
def iso(d): return d.astimezone(UTC).isoformat()


def request(url, payload=None, token=None, key=None):
    if urllib.parse.urlsplit(url).netloc != 'api.octopus.energy' or not url.startswith(API+'/'):
        raise SafeError('Unexpected API host refused')
    headers = {'User-Agent':'omarchy-octopus/0.1'}
    if token: headers['Authorization'] = 'JWT '+token
    if key: headers['Authorization'] = 'Basic '+base64.b64encode((key+':').encode()).decode()
    if payload is not None: headers['Content-Type'] = 'application/json'
    try:
        req=urllib.request.Request(url, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
        with urllib.request.build_opener(NoRedirect).open(req, timeout=12) as r: data=json.load(r)
        if data.get('errors'): raise SafeError('Octopus rejected the query; check account access')
        return data
    except urllib.error.HTTPError as e:
        if e.code in (401,403): raise SafeError('Octopus authentication failed') from None
        if e.code == 429: raise SafeError('Octopus rate limit; retrying later') from None
        raise SafeError('Octopus service error') from None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        raise SafeError('Octopus unavailable; cached readings retained') from None


def pages(url, key=None):
    rows=[]
    for _ in range(10):
        d=request(url,key=key); rows.extend(d.get('results',[])); url=d.get('next')
        if not url: return rows
    raise SafeError('Incomplete API pagination')


def windows(rates, now, hours):
    count=hours*2; candidates=[]
    future=sorted([r for r in rates if stamp(r['start'])>=now],key=lambda r:r['start'])
    for i in range(len(future)-count+1):
        chunk=future[i:i+count]
        if any((stamp(r['end'])-stamp(r['start'])).total_seconds()!=1800 for r in chunk): continue
        if any(stamp(a['end'])!=stamp(b['start']) for a,b in zip(chunk,chunk[1:])): continue
        candidates.append({'hours':hours,'start':chunk[0]['start'],'end':chunk[-1]['end'],'price':sum(r['price'] for r in chunk)/count})
    return min(candidates,key=lambda r:r['price']) if candidates else None


def usage_summary(rows, rates, start, end):
    rows=sorted({r['start']:r for r in rows if start<=stamp(r['start'])<end}.values(),key=lambda r:r['start'])
    price_by_time={stamp(r['start']):r['price'] for r in rates}
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
        self.cache=read_json(CACHE/'readings.json'); self.auth=read_json(CACHE/'auth.json')
        self.now=datetime.now(UTC)
        self.zone=ZoneInfo(self.cfg.get('timezone','Europe/London'))
        self.errors=[]
        self.refresh=refresh
    def graphql(self,q, authenticated=True):
        token=self.token() if authenticated else None
        return request(API+'/v1/graphql/',{'query':q},token=token)['data']
    def token(self):
        if self.auth.get('expires',0)>time.time()+120: return self.auth['token']
        key=self.creds.get('api_key')
        if not key: raise SafeError('Set up your Octopus API key for usage')
        data=self.graphql('mutation { obtainKrakenToken(input:{APIKey:'+json.dumps(key)+'}) { token } }',False)
        token=data['obtainKrakenToken']['token']
        payload=token.split('.')[1]; claims=json.loads(base64.urlsafe_b64decode(payload+'='*((-len(payload))%4)))
        self.auth={'token':token,'expires':claims['exp']}
        private_write(CACHE/'auth.json',self.auth)
        return token
    def device(self):
        if self.auth.get('device'): return self.auth['device']
        account=self.creds.get('account_number','')
        q='query { account(accountNumber:'+json.dumps(account)+') { electricityAgreements(active:true) { meterPoint { mpan meters(includeInactive:false) { smartImportElectricityMeter { deviceId } } } } } }'
        a=self.graphql(q)
        ids=[]
        for agreement in a['account']['electricityAgreements']:
            p=agreement['meterPoint']
            if self.creds.get('mpan') and str(p['mpan'])!=str(self.creds['mpan']): continue
            ids.extend(m['smartImportElectricityMeter']['deviceId'] for m in p['meters'] if m.get('smartImportElectricityMeter'))
        ids=list(set(ids))
        if len(ids)!=1: raise SafeError('Select one smart electricity meter in account settings')
        self.auth['device']=ids[0]; private_write(CACHE/'auth.json',self.auth)
        return ids[0]
    def telemetry(self,start,end,group):
        q='query { smartMeterTelemetry(deviceId:'+json.dumps(self.device())+', grouping:'+group+', start:'+json.dumps(iso(start))+', end:'+json.dumps(iso(end))+') { readAt consumptionDelta demand } }'
        return self.graphql(q)['smartMeterTelemetry'] or []
    def rates(self):
        tariff=self.cfg.get('tariff',''); product=self.cfg.get('product','')
        if not re.fullmatch(r'[A-Z0-9-]+',tariff) or not re.fullmatch(r'[A-Z0-9-]+',product): raise SafeError('Configure your Agile product and tariff')
        local=self.now.astimezone(self.zone); midnight=local.replace(hour=0,minute=0,second=0,microsecond=0)
        # Standard-meter readings can lag by days. Cover the entire three-day
        # consumption query below so available historical readings remain priced.
        params=urllib.parse.urlencode({'period_from':iso(midnight-timedelta(days=3)),'period_to':iso(midnight+timedelta(days=2)),'page_size':200})
        raw=pages(f'{API}/v1/products/{product}/electricity-tariffs/{tariff}/standard-unit-rates/?{params}')
        return sorted([{'start':r['valid_from'],'end':r['valid_to'],'price':float(r['value_inc_vat'])} for r in raw if r.get('valid_to') and r.get('value_inc_vat') is not None],key=lambda r:r['start'])
    def live(self):
        raw=self.telemetry(self.now-timedelta(minutes=5),self.now,'TEN_SECONDS')
        raw=[r for r in raw if r.get('demand') is not None]
        if not raw: raise SafeError('No recent Home Mini readings')
        r=max(raw,key=lambda r:r['readAt'])
        return {'at':r['readAt'],'watts':float(r['demand'])}
    def usage(self):
        end=self.now.replace(minute=(self.now.minute//30)*30,second=0,microsecond=0); start=end-timedelta(hours=24)
        if self.cfg.get('home_mini',True):
            raw=self.telemetry(start,end,'HALF_HOURLY')
            rows=[{'start':r['readAt'],'value':float(r['consumptionDelta'])/1000} for r in raw if r.get('consumptionDelta') is not None]
        else:
            mpan=self.creds.get('mpan',''); serial=self.creds.get('electricity_serial','')
            if not re.fullmatch(r'[0-9]+',mpan) or not re.fullmatch(r'[A-Za-z0-9]+',serial): raise SafeError('Configure your meter for usage')
            params=urllib.parse.urlencode({'period_from':iso(start-timedelta(days=2)),'period_to':iso(end),'page_size':200})
            raw=pages(f'{API}/v1/electricity-meter-points/{mpan}/meters/{serial}/consumption/?{params}',key=self.creds.get('api_key'))
            rows=[{'start':r['interval_start'],'value':float(r['consumption'])} for r in raw]
            if rows:
                end=min(end,max(stamp(r['start']) for r in rows)+timedelta(minutes=30));start=end-timedelta(hours=24)
        return {'rows':rows,'start':iso(start),'end':iso(end)}
    def section(self,name,ttl,loader):
        entry=self.cache.get(name,{})
        if (time.time()-entry.get('fetched',0)>=ttl or (self.refresh and time.time()-entry.get('fetched',0)>=30)) and time.time()>=entry.get('retryAfter',0):
            try:
                entry={'data':loader(),'fetched':time.time()}
            except (SafeError,KeyError,TypeError,ValueError) as e:
                msg=str(e) if isinstance(e,SafeError) else 'Unexpected Octopus response'
                entry={**entry,'error':msg,'retryAfter':time.time()+60}
                if 'authentication' in msg.lower() or 'rejected' in msg.lower():
                    self.auth={};private_write(CACHE/'auth.json',{})
            self.cache[name]=entry
        if entry.get('error'): self.errors.append(name+': '+entry['error'])
        return entry.get('data')
    def snapshot(self):
        rates=self.section('rates',900,self.rates) or []
        live=self.section('live',15,self.live) if self.creds and self.cfg.get('home_mini',True) else None
        usage=self.section('usage',300,self.usage) if self.creds else None
        private_write(CACHE/'readings.json',self.cache)
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
            days[label]={'date':str(day),'rows':rows,'expected':expected,'complete':len(rows)==expected}
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
        return {'schema':1,'fetchedAt':iso(self.now),'timezone':str(self.zone),'tariff':self.cfg.get('tariff',''),
                'current':current,'live':live,'liveStale':age is None or age>120,'liveAge':age,
                'days':days,'usage':usage_result,'windows':best,
                'errors':self.errors,'configured':bool(self.creds),'homeMini':self.cfg.get('home_mini',True),
                'updated':{k:v.get('fetched') for k,v in self.cache.items()}}


def setup():
    print('Octopus Energy setup. Values remain in private local files.')
    product=input('Agile product [AGILE-24-10-01]: ').strip() or 'AGILE-24-10-01'
    tariff=input('Full tariff code (e.g. E-1R-AGILE-24-10-01-J): ').strip()
    mini=input('Home Mini? [y/N]: ').lower().startswith('y')
    key=getpass.getpass('Octopus API key (blank for prices only): ').strip()
    private_write(CONFIG/'config.json',{'product':product,'tariff':tariff,'timezone':'Europe/London','home_mini':mini})
    if key:
        creds={'api_key':key,'account_number':input('Account number: ').strip(),'mpan':input('Electricity MPAN: ').strip(),'electricity_serial':input('Meter serial (optional with Home Mini): ').strip()}
        private_write(CONFIG/'credentials.json',creds)
    else: (CONFIG/'credentials.json').unlink(missing_ok=True)
    for name in ['auth.json','readings.json']: (CACHE/name).unlink(missing_ok=True)
    print('Saved. Enable or refresh the Omarchy plugin.')


def main():
    p=argparse.ArgumentParser();p.add_argument('--setup',action='store_true');p.add_argument('--refresh',action='store_true');args=p.parse_args()
    if args.setup: setup();return
    CACHE.mkdir(parents=True,exist_ok=True,mode=0o700)
    with open(CACHE/'lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try: print(json.dumps(Adapter(args.refresh).snapshot(),allow_nan=False))
        except Exception: print(json.dumps({'errors':['Unable to load Octopus settings or data'],'liveStale':True}))

if __name__=='__main__': main()
