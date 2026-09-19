import sys,unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from octopus import windows,usage_summary,iso,request,SafeError,Adapter,MAX_RESPONSE_BYTES,API
from unittest.mock import patch,MagicMock
import io
import time
import urllib.parse
from zoneinfo import ZoneInfo

class ResponseLimitTests(unittest.TestCase):
    def response(self, body):
        stream = io.BytesIO(body)
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.side_effect = stream.read
        return response

    def test_valid_responses_up_to_exact_limit(self):
        for size in (2, MAX_RESPONSE_BYTES):
            with self.subTest(size=size):
                response = self.response(b'{}' + b' ' * (size - 2))
                with patch('octopus.urllib.request.build_opener') as opener:
                    opener.return_value.open.return_value = response
                    self.assertEqual(request(API + '/v1/graphql/'), {})
                response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
                response.__exit__.assert_called_once()

    def test_oversized_responses_rejected_before_parsing(self):
        for extra in (1, 10000):
            with self.subTest(extra=extra):
                response = self.response(b'{}' + b' ' * (MAX_RESPONSE_BYTES + extra - 2))
                with patch('octopus.urllib.request.build_opener') as opener, patch('octopus.json.loads') as parse:
                    opener.return_value.open.return_value = response
                    with self.assertRaisesRegex(SafeError, 'exceeds 1 MiB'):
                        request(API + '/v1/graphql/')
                    parse.assert_not_called()
                response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
                response.__exit__.assert_called_once()

    def test_unending_response_is_bounded_and_preserves_cache(self):
        response = MagicMock()
        response.__enter__.return_value = response
        # Simulate a source with unlimited data: it returns any requested amount.
        response.read.side_effect = lambda amount: b' ' * amount
        a = Adapter.__new__(Adapter)
        a.cache = {'live': {'data': {'at': '2026-09-19T00:00:00+00:00', 'watts': 700}, 'fetched': 0}}
        a.errors = []; a.refresh = False
        with patch('octopus.urllib.request.build_opener') as opener, patch('octopus.json.loads') as parse:
            opener.return_value.open.return_value = response
            loader = lambda: request(API + '/v1/graphql/')
            self.assertEqual(a.section('live', 15, loader), {'at': '2026-09-19T00:00:00+00:00', 'watts': 700})
            self.assertIn('exceeds 1 MiB', a.errors[0])
            self.assertGreater(a.cache['live']['retryAfter'], time.time())
            a.section('live', 15, loader)
            response.read.assert_called_once_with(MAX_RESPONSE_BYTES + 1)
            parse.assert_not_called()

    def test_invalid_json_still_raises_safe_error(self):
        response = self.response(b'not json')
        with patch('octopus.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(SafeError, 'cached readings retained'):
                request(API + '/v1/graphql/')


class EnergyTests(unittest.TestCase):
    def setUp(self): self.start=datetime(2026,9,17,tzinfo=timezone.utc)
    def rates(self,values):
        return [{'start':iso(self.start+timedelta(minutes=30*i)),'end':iso(self.start+timedelta(minutes=30*(i+1))),'price':v} for i,v in enumerate(values)]
    def test_negative_prices_select_true_cheapest_window(self):
        result=windows(self.rates([20,-5,-10,5]),self.start,1)
        self.assertEqual(result['price'],-7.5)
        self.assertEqual(result['start'],iso(self.start+timedelta(minutes=30)))
    def test_gap_is_not_a_cheap_contiguous_window(self):
        rates=self.rates([-10,99,-10]);del rates[1]
        self.assertIsNone(windows(rates,self.start,1))
    def test_no_started_window_is_offered_as_full_hour(self):
        result=windows(self.rates([-100,10,20]),self.start+timedelta(minutes=1),1)
        self.assertEqual(result['price'],15)
    def test_missing_usage_is_not_zero_or_complete(self):
        rows=[{'start':iso(self.start),'value':1}]
        result=usage_summary(rows,self.rates([10,20]),self.start,self.start+timedelta(hours=1))
        self.assertFalse(result['complete']);self.assertFalse(result['costComplete'])
        self.assertEqual(result['kwh'],1)
    def test_whole_cost_handles_negative_tariff(self):
        rows=[{'start':r['start'],'value':1} for r in self.rates([-10,20])]
        result=usage_summary(rows,self.rates([-10,20]),self.start,self.start+timedelta(hours=1))
        self.assertTrue(result['costComplete']);self.assertAlmostEqual(result['cost'],.1)
        self.assertEqual([r['cost'] for r in result['rows']],[-.1,.2])
    def test_missing_prices_do_not_claim_complete_cost(self):
        rows=[{'start':r['start'],'value':1} for r in self.rates([10,20])]
        result=usage_summary(rows,self.rates([10]),self.start,self.start+timedelta(hours=1))
        self.assertTrue(result['complete']);self.assertFalse(result['costComplete'])
        self.assertIsNone(result['rows'][1]['cost'])
    def test_dst_calendar_day_has_variable_intervals(self):
        zone=ZoneInfo('Europe/London')
        for day,count in [(datetime(2026,3,29,tzinfo=zone),46),(datetime(2026,10,25,tzinfo=zone),50)]:
            start=day.astimezone(timezone.utc);end=(day+timedelta(days=1)).astimezone(timezone.utc)
            rows=[{'start':iso(start+timedelta(minutes=30*i)),'value':1} for i in range(count)]
            self.assertTrue(usage_summary(rows,[],start,end)['complete'])
    def test_network_failure_keeps_cached_value_and_marks_error(self):
        a=Adapter.__new__(Adapter);a.cache={'live':{'data':{'at':'2026-09-19T00:00:00+00:00','watts':700},'fetched':0}};a.errors=[];a.refresh=False
        def fail(): raise SafeError('Offline')
        self.assertEqual(a.section('live',15,fail),{'at':'2026-09-19T00:00:00+00:00','watts':700})
        self.assertEqual(a.errors,['live: Offline'])
        self.assertGreater(a.cache['live']['retryAfter'],time.time())
    def test_auth_never_sent_to_foreign_pagination_host(self):
        with self.assertRaises(SafeError): request('https://example.org/v1/anything',key='test-not-secret')
    def test_delayed_standard_meter_history_has_matching_prices(self):
        a=Adapter.__new__(Adapter)
        a.now=self.start+timedelta(hours=12)
        a.zone=ZoneInfo('Europe/London')
        a.cfg={'product':'AGILE-24-10-01','tariff':'E-1R-AGILE-24-10-01-A','home_mini':False}
        a.creds={'api_key':'test-not-secret','mpan':'123','electricity_serial':'TEST123'}
        a.cache={};a.auth={};a.errors=[];a.refresh=False
        # A complete day of readings whose newest interval is two days old.
        first=a.now-timedelta(days=3)
        starts=[first+timedelta(minutes=30*i) for i in range(48)]
        def api_pages(url,key=None):
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            cutoff=datetime.fromisoformat(query['period_from'][0])
            if '/consumption/' in url:
                return [{'interval_start':iso(t),'consumption':1} for t in starts if t>=cutoff]
            return [{'valid_from':iso(t),'valid_to':iso(t+timedelta(minutes=30)),
                     'value_inc_vat':-10} for t in starts if t>=cutoff]
        with patch('octopus.pages',side_effect=api_pages),patch('octopus.private_write'):
            result=a.snapshot()['usage']
        self.assertTrue(result['complete'])
        self.assertTrue(result['costComplete'])
        self.assertAlmostEqual(result['cost'],-4.8)
        self.assertTrue(all(row['cost']==-.1 for row in result['rows']))

if __name__=='__main__':unittest.main()
