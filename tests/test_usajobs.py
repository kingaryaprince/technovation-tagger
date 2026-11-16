"""Tests for the USAJOBS importer. No network calls."""
import unittest
from datetime import date
from unittest import mock

from alumni_tagger import usajobs as uj
from alumni_tagger.clients import ServiceError

TODAY = date(2026, 11, 15)


def item(id_='JOA-1', **overrides):
    desc = {
        'PositionID': 'NASA-27-001',
        'PositionTitle': 'Pathways Intern (Data Science)',
        'PositionURI': 'https://www.usajobs.gov/job/811111111',
        'ApplyURI': ['https://www.usajobs.gov:443/GetJob/ViewDetails/811111111'],
        'PositionLocationDisplay': 'Multiple Locations',
        'PositionLocation': [{'LocationName': 'Moffett Field, California'},
                             {'LocationName': 'Greenbelt, Maryland'},
                             {'LocationName': 'Moffett Field, California'}],
        'OrganizationName': 'National Aeronautics and Space Administration',
        'DepartmentName': 'National Aeronautics and Space Administration',
        'QualificationSummary': 'Enrolled at least half time in a degree program.',
        'PositionRemuneration': [{'MinimumRange': '20.56', 'MaximumRange': '29.04',
                                  'RateIntervalCode': 'PH'}],
        'PositionSchedule': [{'Name': 'Part-time'}],
        'ApplicationCloseDate': '2026-12-01T23:59:59.9970',
        'UserArea': {'Details': {'JobSummary': 'Analyze mission data   with Python.',
                                 'WhoMayApply': {'Name': 'Students'},
                                 'RemoteIndicator': False}},
    }
    desc.update(overrides.pop('desc', {}))
    return {'MatchedObjectId': id_, 'MatchedObjectDescriptor': desc, **overrides}


class RowTests(unittest.TestCase):
    def test_maps_announcement_to_schema(self):
        row = uj.to_row(item(), 'student')
        self.assertEqual(row['source'], 'usajobs')
        self.assertEqual(row['source_id'], 'JOA-1')
        self.assertEqual(row['type'], 'internship')
        self.assertEqual(row['organization'], 'National Aeronautics and Space Administration')
        self.assertEqual(row['location'], 'Moffett Field, California; Greenbelt, Maryland')
        self.assertFalse(row['remote'])
        self.assertEqual(row['url'], 'https://www.usajobs.gov/job/811111111')
        self.assertEqual(row['deadline'], '2026-12-01')
        self.assertFalse(row['rolling'])
        self.assertEqual(row['missing_fields'], [])

    def test_description_has_facts_for_the_tagger(self):
        text = uj.to_row(item(), 'student')['description']
        self.assertIn('Pathways internship for current students', text)
        self.assertIn('Pay: $20.56 to $29.04 per hour', text)
        self.assertIn('Who may apply: Students', text)
        self.assertIn('Summary: Analyze mission data with Python.', text)
        self.assertNotIn('(National Aeronautics', text)       # department same as org: not repeated

    def test_default_port_is_removed_from_links(self):
        row = uj.to_row(item(desc={'PositionURI': 'https://www.usajobs.gov:443/job/854458300'}), 'student')
        self.assertEqual(row['url'], 'https://www.usajobs.gov/job/854458300')

    def test_graduates_are_jobs(self):
        self.assertEqual(uj.to_row(item(), 'graduates')['type'], 'job')

    def test_remote_from_flag_or_location(self):
        flagged = item(desc={'UserArea': {'Details': {'RemoteIndicator': 'true'}}})
        self.assertTrue(uj.to_row(flagged, 'student')['remote'])
        anywhere = item(desc={'PositionLocation': [{'LocationName': 'Anywhere in the U.S. (remote job)'}]})
        self.assertTrue(uj.to_row(anywhere, 'student')['remote'])

    def test_blank_fields_are_listed_for_review(self):
        bare = item(desc={'PositionLocation': [], 'PositionLocationDisplay': '',
                          'ApplicationCloseDate': None, 'PositionURI': '', 'ApplyURI': []})
        row = uj.to_row(bare, 'student')
        self.assertEqual(row['missing_fields'], ['location', 'url', 'deadline'])
        self.assertIsNone(row['deadline'])

    def test_rejects_items_without_id_or_title(self):
        self.assertIsNone(uj.to_row({'MatchedObjectDescriptor': {'PositionTitle': 'X'}}, 'student'))
        self.assertIsNone(uj.to_row(item(desc={'PositionTitle': '  '}), 'student'))

    def test_long_description_is_cut(self):
        long = item(desc={'UserArea': {'Details': {'JobSummary': 'word ' * 2000}}})
        self.assertLessEqual(len(uj.to_row(long, 'student')['description']), uj.DESCRIPTION_LIMIT)

    def test_bad_dates_and_pay_do_not_crash(self):
        odd = item(desc={'ApplicationCloseDate': 'soon', 'PositionRemuneration': [{'MinimumRange': 'n/a'}]})
        row = uj.to_row(odd, 'student')
        self.assertIsNone(row['deadline'])
        self.assertNotIn('Pay:', row['description'])


class PlanTests(unittest.TestCase):
    def test_first_path_wins_and_closed_are_skipped(self):
        closed = item('JOA-2', desc={'ApplicationCloseDate': '2026-11-01T00:00:00'})
        rows, skipped = uj.plan([('student', [item(), closed]), ('graduates', [item()])], TODAY)
        self.assertEqual([(r['source_id'], r['type']) for r in rows], [('JOA-1', 'internship')])
        self.assertEqual(skipped, 1)


class FetchTests(unittest.TestCase):
    def page(self, n_items, pages):
        return {'SearchResult': {'SearchResultItems': [item(f'J{i}') for i in range(n_items)],
                                 'UserArea': {'NumberOfPages': str(pages)}}}

    def test_reads_every_page_with_the_right_headers(self):
        get = mock.Mock(side_effect=[self.page(2, 2), self.page(1, 2)])
        headers = uj.credentials({'USAJOBS_API_KEY': 'k', 'USAJOBS_EMAIL': 'me@example.com'})
        items, complete = uj.fetch('student', headers, get=get)
        self.assertEqual(len(items), 3)
        self.assertTrue(complete)
        url, sent = get.call_args_list[1][0][:2]
        self.assertIn('HiringPath=student', url)
        self.assertIn('Page=2', url)
        self.assertEqual(sent['User-Agent'], 'me@example.com')
        self.assertEqual(sent['Authorization-Key'], 'k')

    def test_missing_credentials_stop_before_any_call(self):
        with self.assertRaises(ValueError):
            uj.credentials({'USAJOBS_API_KEY': 'k', 'USAJOBS_EMAIL': ''})

    def test_unexpected_response_raises(self):
        with self.assertRaises(ServiceError):
            uj.fetch('student', {}, get=mock.Mock(return_value={'oops': 1}))

    def test_too_many_pages_is_marked_incomplete(self):
        get = mock.Mock(return_value=self.page(1, uj.MAX_PAGES + 5))
        _, complete = uj.fetch('student', {}, get=get)
        self.assertFalse(complete)
        self.assertEqual(get.call_count, uj.MAX_PAGES)


class ChangesTests(unittest.TestCase):
    def setUp(self):
        self.row = uj.to_row(item(), 'student')

    def saved(self, **overrides):
        base = {f: self.row[f] for f in uj.FIELDS}
        base.update(source_id='JOA-1', status='active', deadline='2026-12-01T00:00:00+00:00')
        base.update(overrides)
        return base

    def test_new_rows_respect_limit(self):
        other = uj.to_row(item('JOA-9'), 'student')
        new, changed, expire = uj.changes([self.row, other], {}, TODAY, True, limit=1)
        self.assertEqual([r['source_id'] for r in new], ['JOA-1'])

    def test_unchanged_row_is_left_alone(self):
        new, changed, expire = uj.changes([self.row], {'JOA-1': self.saved()}, TODAY, True)
        self.assertEqual((new, changed, expire), ([], [], []))

    def test_changed_text_or_deadline_is_updated(self):
        _, changed, _ = uj.changes([self.row], {'JOA-1': self.saved(deadline='2026-11-20')}, TODAY, True)
        self.assertEqual(len(changed), 1)
        _, changed, _ = uj.changes([self.row], {'JOA-1': self.saved(title='Old title')}, TODAY, True)
        self.assertEqual(len(changed), 1)

    def test_gone_or_past_deadline_rows_expire(self):
        existing = {'GONE': self.saved(source_id='GONE'),
                    'PAST': self.saved(source_id='PAST', deadline='2026-11-10'),
                    'DONE': self.saved(source_id='DONE', status='expired')}
        _, _, expire = uj.changes([], existing, TODAY, True)
        self.assertEqual(expire, ['GONE', 'PAST'])

    def test_incomplete_fetch_only_expires_past_deadlines(self):
        existing = {'GONE': self.saved(source_id='GONE'),
                    'PAST': self.saved(source_id='PAST', deadline='2026-11-10')}
        _, _, expire = uj.changes([], existing, TODAY, False)
        self.assertEqual(expire, ['PAST'])


class WriteTests(unittest.TestCase):
    def test_inserts_updates_and_expires(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 's'})
        row = uj.to_row(item(), 'student')
        with mock.patch.object(uj, 'json_request') as req:
            uj.write(db, [row], [row], ['OLD'], 'pending')
        insert, update, expire = req.call_args_list
        self.assertEqual(insert[0][2][0]['status'], 'pending')
        self.assertEqual(insert[0][2][0]['tags'], [])
        self.assertEqual(update[1]['method'], 'PATCH')
        self.assertNotIn('status', update[0][2])                 # review status is kept
        self.assertIn('source=eq.usajobs', expire[0][0])
        self.assertEqual(expire[0][2], {'status': 'expired'})


class PathTests(unittest.TestCase):
    def test_parse_paths(self):
        self.assertEqual(uj.parse_paths('graduates, student,student'), ['graduates', 'student'])
        for bad in ('', 'public', 'student,vet'):
            with self.assertRaises(ValueError):
                uj.parse_paths(bad)


if __name__ == '__main__':
    unittest.main()
