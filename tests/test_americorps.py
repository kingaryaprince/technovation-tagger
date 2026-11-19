"""Tests for the AmeriCorps importer. No network calls: pages come from tests/fixtures,
which copy the structure of the live my.americorps.gov pages (contact details made up)."""
import unittest
from datetime import date
from unittest import mock

from alumni_tagger import americorps as ac
from alumni_tagger.clients import ServiceError
from alumni_tagger.core import ROOT

TODAY = date(2026, 11, 19)
RESULTS = (ROOT / 'tests/fixtures/americorps_results.html').read_text(encoding='utf-8')
LISTING = (ROOT / 'tests/fixtures/americorps_listing.html').read_text(encoding='utf-8')


class ParseTests(unittest.TestCase):
    def test_results_rows(self):
        rows = ac.parse_results(RESULTS)
        self.assertEqual([r['id'] for r in rows], ['127137', '130344', '129209'])
        first = rows[0]
        self.assertEqual(first['title'], 'WI Association and Runaway Services')
        self.assertEqual(first['program'], 'SN')
        self.assertEqual(first['apply_to'], date(2027, 8, 1))
        self.assertEqual(first['state'], 'WI')
        self.assertIn('prevention materials & referral', first['summary'])
        self.assertEqual(ac.result_count(RESULTS), 23)

    def test_listing_details_skip_contact(self):
        d = ac.parse_listing(LISTING)
        self.assertIn('LEGO MINDSTORMS', d['about'])
        self.assertTrue(d['duties'].startswith('Plan and conduct'))
        self.assertEqual(d['benefits'], 'Education award upon successful completion of service, Stipend, Training')
        self.assertEqual(d['skills'], 'Computers/Technology, Teaching/Tutoring')
        self.assertEqual(d['schedule'], 'Part Time')
        self.assertEqual(d['program_dates'], '09/21/2026 - 08/31/2027')
        self.assertNotIn('example.org', str(d))
        self.assertNotIn('Jane', str(d))

    def test_missing_pieces_are_blank_not_errors(self):
        self.assertEqual(ac.parse_results('<html>nothing</html>'), [])
        self.assertEqual(ac.result_count('nothing'), 0)
        self.assertEqual(ac.parse_listing('<html></html>')['duties'], '')


class RowTests(unittest.TestCase):
    def setUp(self):
        self.items = {r['id']: r for r in ac.parse_results(RESULTS)}

    def test_row_with_details(self):
        row = ac.to_row(self.items['127137'], ac.parse_listing(LISTING), TODAY)
        self.assertEqual(row['source'], 'americorps')
        self.assertEqual(row['type'], 'volunteer')
        self.assertEqual(row['organization'], 'AmeriCorps State and National')
        self.assertEqual(row['deadline'], '2027-08-01')
        self.assertFalse(row['rolling'])
        self.assertEqual(row['url'], 'https://my.americorps.gov/mp/listing/viewListing.do?id=127137')
        self.assertIn('Skills: Computers/Technology', row['description'])
        self.assertEqual(row['missing_fields'], [])

    def test_far_future_deadline_is_rolling(self):
        row = ac.to_row(self.items['130344'], {}, TODAY)
        self.assertIsNone(row['deadline'])
        self.assertTrue(row['rolling'])
        self.assertNotIn('deadline', row['missing_fields'])

    def test_remote_and_state_fallback(self):
        row = ac.to_row(self.items['129209'], {}, TODAY)
        self.assertTrue(row['remote'])
        self.assertEqual(row['location'], 'WI')
        self.assertEqual(row['organization'], 'AmeriCorps VISTA')

    def test_long_description_is_cut(self):
        row = ac.to_row(self.items['127137'], {'duties': 'word ' * 2000}, TODAY)
        self.assertLessEqual(len(row['description']), ac.DESCRIPTION_LIMIT)


class FakeSite:
    """Answers the start page, then search pages, then listing pages."""
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get(self, url, form=None):
        self.calls.append((url, form))
        if url == ac.START_URL:
            return ''
        if url == ac.SEARCH_URL:
            return self.pages.get(int(form['curPage']), '')
        return LISTING


class FetchTests(unittest.TestCase):
    def test_reads_every_page_and_dedupes(self):
        site = FakeSite({1: RESULTS, 2: RESULTS, 3: RESULTS})
        items, total, complete = ac.fetch_results(site, ['T', 'E'])
        self.assertEqual(total, 23)
        self.assertEqual(len(items), 3)
        self.assertFalse(complete)        # 3 unique listings, site said 23
        forms = [f for u, f in site.calls if u == ac.SEARCH_URL]
        self.assertEqual([f['curPage'] for f in forms], ['1', '2', '3'])
        self.assertEqual(forms[0]['criteria.serviceAreas'], ['T', 'E'])
        self.assertEqual(forms[1]['count'], '23')

    def test_site_errors_become_service_errors(self):
        opener = mock.Mock()
        opener.open.side_effect = OSError('down')
        with self.assertRaises(ServiceError):
            ac.Site(sleep=lambda s: None, opener=opener).get(ac.START_URL)

    def test_waits_between_requests(self):
        waits = []
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'ok'
        opener = mock.Mock()
        opener.open.return_value = response
        site = ac.Site(sleep=waits.append, opener=opener)
        site.get(ac.START_URL)
        site.get(ac.SEARCH_URL, {'curPage': '1'})
        self.assertEqual(waits, [ac.DELAY])


class ResilienceTests(unittest.TestCase):
    def test_failed_listing_page_keeps_the_row(self):
        class Flaky(FakeSite):
            def get(self, url, form=None):
                if 'viewListing' in url:
                    raise ServiceError('AmeriCorps')
                return super().get(url, form)
        site = Flaky({1: RESULTS.replace('returned 23', 'returned 3')})
        site.sleep = lambda s: None
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = []
        with mock.patch.object(ac, 'settings', return_value={}), \
                mock.patch.object(ac, 'SupabaseClient', return_value=db), \
                mock.patch.object(ac, 'json_request') as req, \
                mock.patch('builtins.print') as out:
            ac.run(['T'], today=TODAY, site=site)
        batch = req.call_args_list[0][0][2]
        self.assertEqual(len(batch), 2)                       # both open listings still added
        self.assertNotIn('Member duties', batch[0]['description'])
        printed = '\n'.join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn('2 listing page(s) could not be read', printed)

    def test_results_page_is_retried(self):
        calls = []
        def opener_open(req, timeout):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise OSError('timeout')
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'ok'
            return response
        site = ac.Site(sleep=lambda s: None, opener=mock.Mock(open=opener_open))
        self.assertEqual(site.get(ac.SEARCH_URL, {'curPage': '1'}), 'ok')
        self.assertEqual(len(calls), 2)


class ChangesTests(unittest.TestCase):
    def setUp(self):
        self.row = ac.to_row(ac.parse_results(RESULTS)[0], {}, TODAY)

    def saved(self, **overrides):
        base = {f: self.row[f] for f in ac.FIELDS}
        base.update(source_id='127137', status='active', deadline='2027-08-01T00:00:00+00:00')
        base.update(overrides)
        return base

    def test_unchanged_row_is_left_alone(self):
        self.assertEqual(ac.changes([self.row], {'127137': self.saved()}, TODAY, True), ([], [], []))

    def test_new_limit_and_expiry(self):
        other = {**self.row, 'source_id': '999'}
        existing = {'GONE': self.saved(source_id='GONE'),
                    'PAST': self.saved(source_id='PAST', deadline='2026-11-01')}
        new, _, expire = ac.changes([self.row, other], existing, TODAY, True, limit=1)
        self.assertEqual([r['source_id'] for r in new], ['127137'])
        self.assertEqual(expire, ['GONE', 'PAST'])
        _, _, expire = ac.changes([], existing, TODAY, False)
        self.assertEqual(expire, ['PAST'])


class RunTests(unittest.TestCase):
    def test_dry_run_writes_nothing_and_skips_closed(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = []
        site = FakeSite({1: RESULTS.replace('returned 23', 'returned 3')})
        with mock.patch.object(ac, 'settings', return_value={}), \
                mock.patch.object(ac, 'SupabaseClient', return_value=db), \
                mock.patch.object(ac, 'json_request') as req, \
                mock.patch('builtins.print') as out:
            self.assertEqual(ac.run(['T'], dry_run=True, today=TODAY, site=site), 0)
        req.assert_not_called()
        printed = '\n'.join(str(c.args[0]) for c in out.call_args_list)
        self.assertIn('2 open listings, 1 already closed', printed)
        self.assertIn('To add: 2', printed)

    def test_real_run_inserts_with_details(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = []
        site = FakeSite({1: RESULTS.replace('returned 23', 'returned 3')})
        with mock.patch.object(ac, 'settings', return_value={}), \
                mock.patch.object(ac, 'SupabaseClient', return_value=db), \
                mock.patch.object(ac, 'json_request') as req, \
                mock.patch('builtins.print'):
            ac.run(['T'], limit=1, today=TODAY, site=site)
        batch = req.call_args_list[0][0][2]
        self.assertEqual(len(batch), 1)
        self.assertEqual(batch[0]['status'], 'pending')
        self.assertEqual(batch[0]['tags'], [])
        self.assertIn('Member duties', batch[0]['description'])


class AreaTests(unittest.TestCase):
    def test_parse_areas(self):
        self.assertEqual(ac.parse_areas('t, e,T'), ['T', 'E'])
        for bad in ('', 'Z', 'T,ZZ'):
            with self.assertRaises(ValueError):
                ac.parse_areas(bad)


if __name__ == '__main__':
    unittest.main()
