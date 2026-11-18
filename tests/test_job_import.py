"""Tests for the GitHub job-list importer. No network calls."""
import unittest
from unittest import mock

from alumni_tagger import job_import as imp
from alumni_tagger.job_import import EXPIRED

NOW = 1_800_000_000


def listing(**overrides):
    base = {
        'id': 'abc-123', 'company_name': 'Visa', 'title': 'Software Engineer Intern',
        'url': 'https://visa.wd5.myworkdayjobs.com/Visa/job/London/SWE-Intern_REF1',
        'locations': ['London, UK', 'Reading, UK'], 'terms': ['Summer 2027'],
        'category': 'Software', 'degrees': ["Bachelor's"], 'sponsorship': 'Other',
        'active': True, 'is_visible': True, 'date_posted': NOW - 86400,
    }
    return {**base, **overrides}


README = '''# Some list

| Feature | What |
|---|---|
| Fast | yes |

| Company | Role | Location | Apply | Date Posted |
|--------|------|----------|:-----:|--------------|
| **Botpress** | Software Intern (Winter 2027) 🛂 | Montreal, QC | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://apply.workable.com/botpress/j/16C/?utm_source=list) | Nov 3, 2026 |
| ↳ | Data Intern | Montreal, QC</br>Remote in Canada | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://apply.workable.com/botpress/j/9B3/) | Nov 3, 2026 |
| Arlo | Firmware Co-op | Richmond, BC | 🔒 | Nov 1, 2026 |
| Short | Ops Intern | NYC | <a href="https://go.vansh.nyc/1xy?s=y"><img src="https://i.imgur.com/x.png"></a> | Nov 2 |
| Zap | QA Intern | Austin, TX | [<img src="a.png">](https://zapply.jobs/l/d/abc) | 1d |
| Pipe \\| Co | Analyst Intern | Boston, MA | [Apply](https://job-boards.greenhouse.io/pipe/jobs/1?gh_jid=1) | Nov 1 |
'''


class CleanUrlTests(unittest.TestCase):
    def test_strips_tracking(self):
        self.assertEqual(
            imp.clean_url('https://lifeattiktok.com/search/77?utm_source=Simplify&ref=Simplify'),
            'https://lifeattiktok.com/search/77')

    def test_keeps_job_board_parameters(self):
        self.assertEqual(
            imp.clean_url('https://boards.greenhouse.io/acme/jobs?gh_jid=42&utm_medium=x&ref=Simplify'),
            'https://boards.greenhouse.io/acme/jobs?gh_jid=42')
        self.assertEqual(imp.clean_url('https://jobs.example.com/1?ref=careers'),
                         'https://jobs.example.com/1?ref=careers')

    def test_rejects_list_sites_and_bad_links(self):
        for url in ['https://simplify.jobs/p/4712ce89', 'https://app.simplify.jobs/x',
                    'https://zapply.jobs/l/d/x', 'javascript:alert(1)', '', None, 'not a url']:
            self.assertIsNone(imp.clean_url(url), url)

    def test_shorteners_need_resolving(self):
        self.assertTrue(imp.needs_resolving('https://go.vansh.nyc/1xy'))
        self.assertFalse(imp.needs_resolving('https://jobs.lever.co/acme/1'))


class FeedTests(unittest.TestCase):
    def test_default_feed_matches_existing_rows(self):
        feed = imp.feed_for()
        self.assertEqual((feed.term, feed.source, feed.type),
                         ('Summer 2027', 'simplify_summer2027', 'internship'))

    def test_other_repos(self):
        cases = {
            'SimplifyJobs/Summer2028-Internships': ('Summer 2028', 'simplify_summer2028', 'internship'),
            'SimplifyJobs/New-Grad-Positions': (None, 'simplify_newgradpositions', 'job'),
            'vanshb03/Summer2027-Internships': ('Summer 2027', 'vanshb03_summer2027', 'internship'),
            'negarprh/Canadian-Tech-Internships-2027':
                (None, 'negarprh_canadiantechinternships2027', 'internship'),
        }
        for repo, expected in cases.items():
            feed = imp.feed_for(repo)
            self.assertEqual((feed.term, feed.source, feed.type), expected, repo)
        self.assertIsNone(imp.feed_for('SimplifyJobs/Summer2027-Internships', term='').term)

    def test_bad_input(self):
        for repo in ['Summer2027-Internships', 'a/b/c', '../x', '']:
            with self.assertRaises(ValueError):
                imp.feed_for(repo)
        with self.assertRaises(ValueError):
            imp.feed_for(fmt='csv')


class ListingsPlanTests(unittest.TestCase):
    def test_row_shape(self):
        row = imp.to_row(listing())
        self.assertEqual(row['source'], 'simplify_summer2027')
        self.assertEqual(row['source_id'], 'abc-123')
        self.assertEqual(row['type'], 'internship')
        self.assertEqual(row['organization'], 'Visa')
        self.assertEqual(row['location'], 'London, UK; Reading, UK')
        self.assertFalse(row['remote'])
        self.assertIn('Software Engineer Intern at Visa.', row['description'])
        self.assertIn('Posted: 2027-01-14.', row['description'])
        self.assertEqual(row['date_posted'], '2027-01-14')
        self.assertIsNone(imp.to_row(listing(date_posted=None))['date_posted'])
        self.assertNotIn('Posted:', imp.to_row(listing(date_posted=0))['description'])
        self.assertNotIn('Sponsorship', row['description'])
        self.assertTrue(imp.to_row(listing(locations=['Remote in USA']))['remote'])

    def test_filters_term_visibility_age_and_links(self):
        listings = [
            listing(id='open'),
            listing(id='closed', active=False),
            listing(id='other-term', terms=['Summer 2026']),
            listing(id='hidden', is_visible=False),
            listing(id='old', date_posted=NOW - 90 * 86400),
            listing(id='simplify-only', url='https://simplify.jobs/p/xyz'),
        ]
        rows, closed, skipped = imp.plan(listings, max_age_days=60, now=NOW)
        self.assertEqual([r['source_id'] for r in rows], ['open'])
        self.assertEqual(closed, ['closed'])
        self.assertEqual(skipped, 1)
        rows, _, _ = imp.plan(listings, now=NOW)
        self.assertEqual({r['source_id'] for r in rows}, {'open', 'old'})

    def test_season_only_listings(self):
        feed = imp.feed_for('vanshb03/Summer2027-Internships')
        a = listing(id='a', season='Summer')
        b = listing(id='b', season='Fall')
        del a['terms'], b['terms']
        rows, _, _ = imp.plan([a, b], now=NOW, feed=feed)
        self.assertEqual([r['source_id'] for r in rows], ['a'])
        self.assertEqual(rows[0]['source'], 'vanshb03_summer2027')

    def test_no_term_feed_keeps_all_terms(self):
        feed = imp.feed_for('SimplifyJobs/New-Grad-Positions')
        rows, _, _ = imp.plan([listing(id='a', terms=[]), listing(id='b', terms=['Fall 2026'])],
                              now=NOW, feed=feed)
        self.assertEqual({r['source_id'] for r in rows}, {'a', 'b'})
        self.assertEqual({r['type'] for r in rows}, {'job'})
        self.assertNotIn('Term:', rows[0]['description'])

    def test_newest_first_and_duplicates(self):
        rows, _, _ = imp.plan([listing(id='older', date_posted=NOW - 5 * 86400),
                               listing(id='newer'), listing(id='newer', title='Dup')], now=NOW)
        self.assertEqual([r['source_id'] for r in rows], ['newer', 'older'])


class ReadmeTests(unittest.TestCase):
    feed = imp.feed_for('negarprh/Canadian-Tech-Internships-2027')

    def test_parses_job_table(self):
        rows, skipped, closed = imp.plan_readme(README, self.feed)
        by_title = {r['title']: r for r in rows}
        self.assertEqual(set(by_title), {'Software Intern (Winter 2027)', 'Data Intern',
                                         'Ops Intern', 'Analyst Intern'})
        self.assertEqual((skipped, closed), (1, 1))          # zapply link skipped, Arlo closed
        first = by_title['Software Intern (Winter 2027)']
        self.assertEqual(first['organization'], 'Botpress')
        self.assertEqual(first['url'], 'https://apply.workable.com/botpress/j/16C/')
        self.assertEqual(first['source'], 'negarprh_canadiantechinternships2027')
        self.assertEqual(len(first['source_id']), 20)
        self.assertIn('Listed in: github.com/negarprh/Canadian-Tech-Internships-2027.',
                      first['description'])
        self.assertIsNone(first['date_posted'])                  # README dates aren't reliable
        data = by_title['Data Intern']
        self.assertEqual(data['organization'], 'Botpress')               # ↳ repeats company
        self.assertEqual(data['location'], 'Montreal, QC; Remote in Canada')
        self.assertTrue(data['remote'])
        self.assertEqual(by_title['Ops Intern']['url'], 'https://go.vansh.nyc/1xy?s=y')
        self.assertEqual(by_title['Analyst Intern']['organization'], 'Pipe | Co')
        self.assertEqual(by_title['Analyst Intern']['url'],
                         'https://job-boards.greenhouse.io/pipe/jobs/1?gh_jid=1')

    def test_same_listing_keeps_same_id(self):
        first, _, _ = imp.plan_readme(README, self.feed)
        again, _, _ = imp.plan_readme(README.replace('Nov 3, 2026', 'Nov 4, 2026'), self.feed)
        self.assertEqual([r['source_id'] for r in first], [r['source_id'] for r in again])

    def test_no_table_stops_the_run(self):
        with self.assertRaises(ValueError):
            imp.plan_readme('# Quant\n|Role|Links|\n|---|---|\n|QR|[x](https://a.com/1)|\n', self.feed)


class ChangesTests(unittest.TestCase):
    def test_insert_only_new_respecting_limit(self):
        rows, _, _ = imp.plan([listing(id=str(i), title=f'Intern {i}', date_posted=NOW - i)
                               for i in range(5)], now=NOW)
        existing = {'0': {**rows[0], 'status': 'active'}}
        new, changed, expire, unresolved, _ = imp.changes(rows, [], existing, limit=2)
        self.assertEqual([r['source_id'] for r in new], ['1', '2'])
        self.assertEqual((changed, expire, unresolved), ([], [], 0))

    def test_changed_text_and_expiry(self):
        row = imp.to_row(listing(id='a'))
        existing = {'a': {**row, 'title': 'Old title', 'status': 'active'},
                    'gone': {'status': 'active'}, 'done': {'status': 'expired'}}
        new, changed, expire, _, _ = imp.changes([row], ['gone', 'done', 'never-saved'], existing)
        self.assertEqual(new, [])
        self.assertEqual([r['source_id'] for r in changed], ['a'])
        self.assertEqual(expire, ['gone'])

    def test_fills_missing_date_on_existing_rows(self):
        row = imp.to_row(listing(id='a'))
        existing = {'a': {**row, 'date_posted': None, 'status': 'active'}}
        _, changed, _, _, _ = imp.changes([row], [], existing)
        self.assertEqual([r['date_posted'] for r in changed], ['2027-01-14'])

    def test_skips_repeats_of_an_open_job(self):
        careers = 'https://careers.publicisgroupe.com/jobs/182898?icims=1'
        ats = 'https://publicisgroupe.jibeapply.com/jobs/182898?icims=1'
        base = dict(company_name='Publicis Groupe', title='PhD Data Science Intern',
                    locations=['Chicago, IL'])
        rows, _, _ = imp.plan([listing(id='a', url=careers, date_posted=NOW, **base),
                               listing(id='b', url=ats, date_posted=NOW - 1, **base),
                               listing(id='c', url=ats + '&x=1', date_posted=NOW - 2,
                                       **{**base, 'locations': ['New York, NY']})], now=NOW)
        new, _, _, _, duplicates = imp.changes(rows, [], {}, limit=2)
        self.assertEqual([r['source_id'] for r in new], ['a', 'c'])   # other city is kept
        self.assertEqual(duplicates, 1)
        # A repeat of a saved open job is skipped; a repeat of an expired or closing one isn't.
        saved = {**rows[0], 'title': 'PHD data-science intern', 'status': 'active'}
        new, _, _, _, duplicates = imp.changes(rows[1:], [], {'old': saved})
        self.assertEqual(([r['source_id'] for r in new], duplicates), (['c'], 1))
        for existing, closed in (({'old': {**saved, 'status': EXPIRED}}, []), ({'old': saved}, ['old'])):
            new, _, _, _, duplicates = imp.changes(rows[1:], closed, existing)
            self.assertEqual(([r['source_id'] for r in new], duplicates), (['b', 'c'], 0))

    def test_readme_expires_rows_no_longer_open(self):
        row = imp.to_row(listing(id='still'))
        existing = {'still': {**row, 'status': 'active'}, 'dropped': {'status': 'pending'}}
        _, _, expire, _, _ = imp.changes([row], None, existing)
        self.assertEqual(expire, ['dropped'])

    def test_shortener_links(self):
        short = 'https://go.vansh.nyc/1xy?s=y'
        rows = [imp.to_row(listing(id=i, url=short, title=i)) for i in ('kept', 'new', 'dead')]
        existing = {'kept': {**rows[0], 'url': 'https://jobs.ashbyhq.com/a/1', 'status': 'active'}}
        resolved = {'new': 'https://jobs.ashbyhq.com/a/2'}
        calls = []

        def resolve(url):
            calls.append(url)
            return resolved.pop('new', None)
        new, changed, _, unresolved, _ = imp.changes(rows, [], existing, resolve=resolve)
        self.assertEqual([(r['source_id'], r['url']) for r in new],
                         [('new', 'https://jobs.ashbyhq.com/a/2')])
        self.assertEqual(unresolved, 1)                     # 'dead' didn't resolve: skipped
        self.assertEqual(len(calls), 2)                     # existing row never re-resolved
        self.assertEqual(changed, [])


class ResolveTests(unittest.TestCase):
    def fake_opener(self, responses):
        from urllib import error

        class Opener:
            def open(self, req, timeout):
                status, location = responses[req.full_url]
                if status == 200:
                    return mock.MagicMock()
                raise error.HTTPError(req.full_url, status, 'x', {'Location': location}, None)
        return Opener()

    def test_follows_shortener_to_employer(self):
        opener = self.fake_opener({'https://go.vansh.nyc/1': (302, 'https://jobs.lever.co/a/1?utm_source=x')})
        with mock.patch.object(imp.request, 'build_opener', return_value=opener):
            self.assertEqual(imp.resolve_link('https://go.vansh.nyc/1'), 'https://jobs.lever.co/a/1')

    def test_dead_or_blocked_targets(self):
        opener = self.fake_opener({'https://go.vansh.nyc/1': (404, None),
                                   'https://bit.ly/2': (302, 'https://simplify.jobs/p/x'),
                                   'https://bit.ly/3': (200, None)})
        with mock.patch.object(imp.request, 'build_opener', return_value=opener):
            for url in ['https://go.vansh.nyc/1', 'https://bit.ly/2', 'https://bit.ly/3']:
                self.assertIsNone(imp.resolve_link(url), url)


class WriteTests(unittest.TestCase):
    def test_requests(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        calls = []
        new = [imp.to_row(listing(id=str(i))) for i in range(imp.BATCH_SIZE + 1)]
        with mock.patch.object(imp, 'json_request', lambda *a, **k: calls.append((a, k))):
            imp.write(db, new, [new[0]], ['a-1', 'b-2'], 'pending')
        self.assertEqual(len(calls), 4)
        (url, _, body), kwargs = calls[0]
        self.assertTrue(url.endswith('/rest/v1/opportunities'))
        self.assertEqual(len(body), imp.BATCH_SIZE)
        self.assertEqual((body[0]['status'], body[0]['tags'], body[0]['missing_fields']),
                         ('pending', [], []))
        (url, _, body), kwargs = calls[2]
        self.assertEqual(kwargs['method'], 'PATCH')
        self.assertNotIn('status', body)
        self.assertNotIn('tags', body)
        (url, _, body), kwargs = calls[3]
        self.assertIn('source=eq.simplify_summer2027&source_id=in.(%22a-1%22,%22b-2%22)', url)
        self.assertEqual((body, kwargs['method']), ({'status': 'expired'}, 'PATCH'))


class LoadTests(unittest.TestCase):
    def test_auto_falls_back_to_readme_only_on_404(self):
        feed = imp.feed_for('negarprh/Canadian-Tech-Internships-2027')
        with mock.patch.object(imp, 'json_request', side_effect=imp.ServiceError('GitHub', 404)), \
                mock.patch.object(imp, 'fetch_text', return_value=README):
            fmt, rows, closed, _, _ = imp.load(feed)
        self.assertEqual((fmt, closed), ('readme', None))
        self.assertEqual(len(rows), 4)
        with mock.patch.object(imp, 'json_request', side_effect=imp.ServiceError('GitHub', 500)):
            with self.assertRaises(imp.ServiceError):
                imp.load(feed)

    def test_dry_run_writes_nothing(self):
        db = mock.Mock()
        db.select.return_value = []
        with mock.patch.object(imp, 'json_request', return_value=[listing()]) as get, \
                mock.patch.object(imp, 'SupabaseClient', return_value=db), \
                mock.patch.object(imp, 'write', side_effect=AssertionError), \
                mock.patch.object(imp, 'settings', return_value={}), \
                mock.patch('builtins.print'):
            self.assertEqual(imp.run(dry_run=True), 0)
        self.assertEqual(get.call_args[1]['method'], 'GET')


if __name__ == '__main__':
    unittest.main()
