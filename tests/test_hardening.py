"""Synthetic-only regression tests for API trust boundaries and worker isolation."""
import copy
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import fcntl
import http.client
import io
import json
import multiprocessing
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import octopus


def hold_lock(cache, acquired, release):
    octopus.CACHE = Path(cache)
    with octopus.cache_lock():
        acquired.set()
        if not release.wait(5): raise RuntimeError('Test lock release never arrived')


def finish_old_refresh(config, cache, acquired, setup_waiting):
    octopus.CONFIG, octopus.CACHE = Path(config), Path(cache)
    with octopus.cache_lock():
        adapter = octopus.Adapter()
        acquired.set()
        if not setup_waiting.wait(5): raise RuntimeError('Setup never attempted the lock')
        # Device lookup began with the old account; complete it while setup waits.
        data = {'account': {'electricityAgreements': [{'meterPoint': {
            'mpan': '123', 'meters': [{'smartImportElectricityMeter': {'deviceId': 'old-device'}}]
        }}]}}
        with patch.object(adapter, 'graphql', return_value=data):
            adapter.device()
        octopus.private_write(octopus.CACHE/'readings.json', {'old_account_reading': True})


class IsolatedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config, self.cache = root/'config', root/'cache'
        self.paths = patch.multiple(octopus, CONFIG=self.config, CACHE=self.cache)
        self.paths.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.paths.stop)
        self.now = datetime(2026, 10, 25, 2, tzinfo=timezone.utc)

    def adapter(self):
        a = octopus.Adapter()
        a.now = self.now
        a.cfg = {'home_mini': True}
        a.creds = {'api_key': 'synthetic-test-only'}
        a.auth = {}
        return a

    def rates(self, count=4):
        start = self.now-timedelta(hours=2)
        return [{'start': octopus.iso(start+timedelta(minutes=30*i)),
                 'end': octopus.iso(start+timedelta(minutes=30*(i+1))), 'price': 10}
                for i in range(count)]

    def cached(self):
        rates = self.rates()
        return {
            'rates': {'data': rates, 'fetched': 0},
            'live': {'data': {'at': octopus.iso(self.now), 'watts': 500}, 'fetched': time.time()},
            'usage': {'data': {'start': rates[0]['start'], 'end': rates[-1]['end'],
                             'rows': [{'start': r['start'], 'value': 1} for r in rates]},
                      'fetched': time.time()},
        }

    def test_invalid_loader_data_cannot_replace_good_cache_or_break_other_sections(self):
        invalid = [None, 'bad', {'start': 'bad'},
                   [{'start': 'not-a-timestamp', 'end': self.rates()[0]['end'], 'price': 10}]]
        for field, value in [('price', float('nan')), ('price', float('inf')),
                             ('price', '-Infinity'), ('start', '2026-10-25T00:00:00')]:
            rows = self.rates()
            rows[0][field] = value
            invalid.append(rows)
        for bad in invalid:
            with self.subTest(bad=bad):
                a = self.adapter()
                a.cache = self.cached()
                with patch.object(a, 'rates', return_value=bad) as loader:
                    result = a.snapshot()
                    self.assertEqual(result['live']['watts'], 500)
                    self.assertEqual(result['usage']['cost'], .4)
                    self.assertTrue(result['errors'])
                    self.assertEqual(octopus.read_json(self.cache/'readings.json')['rates']['data'][0]['price'], 10)
                    a.snapshot()
                    loader.assert_called_once()  # bad results retain normal retry backoff

    def test_invalid_cached_sections_recover_without_waiting_for_freshness(self):
        for bad in [None, [], {'fetched': float('nan')},
                    {'data': [{'start': 'bad'}], 'fetched': time.time()}]:
            with self.subTest(bad=bad):
                a = self.adapter()
                a.cache = self.cached()
                a.cache['rates'] = bad
                with patch.object(a, 'rates', return_value=self.rates()) as loader:
                    result = a.snapshot()
                    loader.assert_called_once()
                    self.assertTrue(result['usage']['costComplete'])
                    self.assertFalse(result['errors'])

    def test_poisoned_cache_with_offline_loader_preserves_other_sections(self):
        a = self.adapter()
        a.cache = self.cached()
        a.cache['rates']['data'][0]['start'] = 'bad'
        a.cache['rates']['fetched'] = time.time()
        with patch.object(a, 'rates', side_effect=octopus.SafeError('Offline')):
            result = a.snapshot()
        self.assertEqual(result['live']['watts'], 500)
        self.assertEqual(result['usage']['kwh'], 4)
        self.assertFalse(result['usage']['costComplete'])
        self.assertNotIn('data', octopus.read_json(self.cache/'readings.json')['rates'])

    def test_disabled_malformed_sections_cannot_break_prices_only_snapshot(self):
        a = self.adapter()
        a.creds = {}
        a.cache = self.cached()
        a.cache.update(live=None, usage=[])
        with patch.object(a, 'rates', return_value=self.rates()):
            result = a.snapshot()
        self.assertIsNone(result['usage'])
        self.assertIsNone(result['live'])
        self.assertEqual(set(result['updated']), {'rates'})

    def test_truncated_chunked_response_keeps_cached_rates_and_refreshes_other_sections(self):
        a = self.adapter()
        a.cfg.update(product='AGILE-24-10-01', tariff='E-1R-AGILE-24-10-01-A')
        a.cache = self.cached()
        for entry in a.cache.values(): entry['fetched'] = 0
        octopus.private_write(self.cache/'readings.json', a.cache)
        healthy_usage = copy.deepcopy(a.cache['usage']['data'])
        for row in healthy_usage['rows']: row['value'] = 2
        client, server = socket.socketpair()
        # The HTTP parser itself raises IncompleteRead: not a mocked read failure.
        server.sendall(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n{}')
        server.close()
        response = http.client.HTTPResponse(client)
        response.begin()
        output = io.StringIO()
        try:
            with patch('octopus.urllib.request.build_opener') as opener, \
                    patch('octopus.Adapter', return_value=a), patch('sys.argv',['octopus.py']), \
                    patch.object(a, 'live', return_value={'at':octopus.iso(self.now),'watts':900}), \
                    patch.object(a, 'usage', return_value=healthy_usage), redirect_stdout(output):
                opener.return_value.open.return_value = response
                self.assertEqual(octopus.main(),0)
            result = json.loads(output.getvalue())
            self.assertEqual(result['schema'],1)
            self.assertEqual(result['live']['watts'],900)
            self.assertEqual(result['usage']['kwh'],8)
            self.assertEqual(result['usage']['cost'],.8)
            self.assertEqual(result['errors'],['rates: Octopus unavailable; cached readings retained'])
            cached = octopus.read_json(self.cache/'readings.json')
            self.assertEqual([r['price'] for r in cached['rates']['data']],[10]*4)
            self.assertGreater(cached['rates']['retryAfter'],time.time())
            # The persisted retry delay applies to the next independent refresh too.
            reloaded = self.adapter()
            with patch.object(reloaded,'rates',side_effect=AssertionError('Backoff ignored')):
                self.assertEqual(reloaded.snapshot()['usage']['cost'],.8)
        finally:
            response.close()
            client.close()

    def test_top_level_and_nested_api_shapes_fail_safely(self):
        for payload in [None, [], 1, 'text']:
            response = MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps(payload).encode()
            with self.subTest(payload=payload), patch('octopus.urllib.request.build_opener') as opener:
                opener.return_value.open.return_value = response
                with self.assertRaises(octopus.SafeError): octopus.request(octopus.API+'/v1/graphql/')
        a = self.adapter()
        for raw in [{'data': []}, {'data': {'smartMeterTelemetry': 'wrong'}},
                    {'data': {'smartMeterTelemetry': [False]}}]:
            with self.subTest(raw=raw), patch.object(a, 'device', return_value='test'), \
                    patch.object(a, 'token', return_value='test'), patch('octopus.request', return_value=raw):
                with self.assertRaises(octopus.SafeError): a.telemetry(self.now,self.now,'TEN_SECONDS')

    def test_live_loader_compares_instants_not_raw_offset_strings(self):
        a = self.adapter()
        with patch.object(a, 'telemetry', return_value=[
            {'readAt': '2026-10-25T01:50:00+01:00', 'demand': 100},
            {'readAt': '2026-10-25T01:10:00+00:00', 'demand': 200},
        ]):
            self.assertEqual(a.live(), {'at': '2026-10-25T01:10:00+00:00', 'watts': 200})

    def test_bad_live_and_usage_values_never_enter_cache(self):
        for name, bad in [('live', {'at': 'invalid', 'watts': 200}),
                          ('live', {'at': octopus.iso(self.now), 'watts': float('nan')}),
                          ('usage', {'rows': [], 'start': 'invalid', 'end': octopus.iso(self.now)}),
                          ('usage', {'rows': [{'start': self.rates()[0]['start'], 'value': float('inf')}],
                                     'start': self.rates()[0]['start'], 'end': octopus.iso(self.now)})]:
            with self.subTest(name=name,bad=bad):
                a = self.adapter()
                a.cache = self.cached()
                a.cache[name]['fetched'] = 0
                original = copy.deepcopy(a.cache[name]['data'])
                self.assertEqual(a.section(name, 15, lambda: bad), original)

    def test_day_count_cannot_hide_duplicate_and_missing_interval(self):
        a = self.adapter()
        start = datetime(2026,10,24,23,tzinfo=timezone.utc)
        rates = [{'start':octopus.iso(start+timedelta(minutes=30*i)),
                  'end':octopus.iso(start+timedelta(minutes=30*(i+1))), 'price':10} for i in range(50)]
        a.creds = {}
        a.cache = {'rates': {'data': rates, 'fetched': time.time()}}
        self.assertTrue(a.snapshot()['days']['today']['complete'])
        rates[-1] = copy.deepcopy(rates[0])
        a.cache = {'rates': {'data': rates, 'fetched': time.time()}}
        day = a.snapshot()['days']['today']
        self.assertEqual(day['expected'], 50)
        self.assertFalse(day['complete'])
        self.assertEqual(len(day['rows']), 49)

    def test_dst_offset_spellings_are_sorted_and_deduplicated_by_instant(self):
        starts = ['2026-10-25T01:00:00+01:00','2026-10-25T01:30:00+01:00',
                  '2026-10-25T01:00:00+00:00','2026-10-25T01:30:00+00:00']
        rows = [{'start': s, 'value': 1} for s in starts]
        rows.append({'start':'2026-10-25T00:00:00Z', 'value':1})
        rates = [{**r,'start':s} for r,s in zip(self.rates(),starts)]
        rates.append({**rates[0],'start':'2026-10-25T00:00:00Z'})
        result = octopus.usage_summary(rows,rates,self.now-timedelta(hours=2),self.now)
        self.assertTrue(result['complete'])
        self.assertTrue(result['costComplete'])
        self.assertEqual(result['kwh'],4)
        self.assertEqual([r['start'] for r in result['rows']],[r['start'] for r in self.rates()])
        self.assertEqual(octopus.windows(rates,self.now-timedelta(hours=2),2)['end'],octopus.iso(self.now))

    def test_abort_setup_before_final_prompt_never_changes_settings(self):
        octopus.private_write(self.config/'config.json', {'old':True})
        octopus.private_write(self.config/'credentials.json', {'api_key':'old-synthetic-key'})
        octopus.private_write(self.cache/'auth.json', {'token':'old-synthetic-token'})
        before = {p: p.read_bytes() for p in (self.config/'config.json', self.config/'credentials.json', self.cache/'auth.json')}
        with patch('builtins.input',side_effect=['NEW','NEW','y','NEW',EOFError]), \
                patch('octopus.getpass.getpass',return_value='new-synthetic-key'), redirect_stdout(io.StringIO()):
            with self.assertRaises(EOFError): octopus.setup()
        self.assertEqual({p:p.read_bytes() for p in before},before)

    def test_setup_waits_for_inflight_old_account_then_invalidates_its_caches(self):
        octopus.private_write(self.config/'config.json', {'home_mini':True})
        octopus.private_write(self.config/'credentials.json', {'api_key':'old-synthetic-key'})
        octopus.private_write(self.cache/'auth.json', {'token':'old-synthetic-token','expires':time.time()+3600})
        ctx = multiprocessing.get_context('fork')
        acquired, waiting = ctx.Event(), ctx.Event()
        worker = ctx.Process(target=finish_old_refresh,args=(str(self.config),str(self.cache),acquired,waiting))
        worker.start()
        real_flock = fcntl.flock
        def observe_lock(fd, mode):
            if mode == fcntl.LOCK_EX: waiting.set()
            return real_flock(fd, mode)
        try:
            self.assertTrue(acquired.wait(3))
            with patch('builtins.input',side_effect=['NEW','NEW','y','NEW-ACCOUNT','456','SERIAL']), \
                    patch('octopus.getpass.getpass',return_value='new-synthetic-key'), \
                    patch('octopus.fcntl.flock',side_effect=observe_lock), redirect_stdout(io.StringIO()):
                octopus.setup()
            worker.join(3)
            self.assertEqual(worker.exitcode,0)
            self.assertEqual(octopus.read_json(self.config/'credentials.json')['api_key'],'new-synthetic-key')
            self.assertFalse((self.cache/'auth.json').exists())
            self.assertFalse((self.cache/'readings.json').exists())
            self.assertEqual(octopus.Adapter().auth,{})
        finally:
            waiting.set()
            worker.join(1)
            if worker.is_alive(): worker.terminate(); worker.join()

    def test_wall_clock_deadline_interrupts_slow_progress_response_and_releases_lock(self):
        octopus.private_write(self.cache/'readings.json', self.cached())
        before = (self.cache/'readings.json').read_bytes()
        client, server = socket.socketpair()
        client.settimeout(.15)
        def drip():
            try:
                server.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n')
                for _ in range(100):
                    server.sendall(b' ')
                    time.sleep(.02)  # each byte arrives before the socket timeout
            except OSError:
                pass
            finally:
                server.close()
        sender = threading.Thread(target=drip, daemon=True)
        sender.start()
        response = http.client.HTTPResponse(client)
        response.begin()
        a = self.adapter()
        a.cache = self.cached()
        a.cache['live']['fetched'] = 0
        def blocked_snapshot():
            return a.section('live', 15, lambda: octopus.request(octopus.API+'/v1/graphql/'))
        output = io.StringIO()
        started = time.monotonic()
        try:
            with patch('octopus.urllib.request.build_opener') as opener, \
                    patch('octopus.REFRESH_TIMEOUT_SECONDS', .25), patch('sys.argv',['octopus.py']), \
                    patch('octopus.Adapter') as adapter, redirect_stdout(output):
                opener.return_value.open.return_value = response
                adapter.return_value.snapshot.side_effect = blocked_snapshot
                self.assertEqual(octopus.main(),1)
            self.assertLess(time.monotonic()-started,1)
            self.assertEqual(output.getvalue(),'')
            self.assertEqual((self.cache/'readings.json').read_bytes(),before)
            with open(self.cache/'lock','w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:
            response.close()
            client.close()
            sender.join(1)

    def test_deadline_includes_lock_contention_for_refresh_and_setup(self):
        ctx = multiprocessing.get_context('fork')
        acquired, release = ctx.Event(), ctx.Event()
        worker = ctx.Process(target=hold_lock,args=(str(self.cache),acquired,release))
        worker.start()
        octopus.private_write(self.config/'config.json', {'original':True})
        try:
            self.assertTrue(acquired.wait(3))
            for args in [[], ['--setup']]:
                output = io.StringIO()
                started = time.monotonic()
                with patch('octopus.REFRESH_TIMEOUT_SECONDS', .1),patch('sys.argv',['octopus.py']+args), \
                        patch('builtins.input',side_effect=['NEW','NEW','n']), \
                        patch('octopus.getpass.getpass',return_value=''), \
                        patch('octopus.Adapter') as adapter,redirect_stdout(output):
                    self.assertEqual(octopus.main(),1)
                self.assertLess(time.monotonic()-started,1)
                adapter.assert_not_called()
                self.assertEqual(octopus.read_json(self.config/'config.json'),{'original':True})
                if not args: self.assertEqual(output.getvalue(),'')
        finally:
            release.set()
            worker.join(3)
            if worker.is_alive(): worker.terminate(); worker.join()


if __name__ == '__main__': unittest.main()
