"""Tests for fetching full job postings from hiring sites. No network calls."""
import unittest

from alumni_tagger import job_import as imp
from alumni_tagger import postings
from alumni_tagger.clients import ServiceError

WORKDAY = ('https://vizient.wd1.myworkdayjobs.com/Vizient_Careers/job/Chicago-IL-60607/'
           'Intern---Analytics_36238R-1')
GREENHOUSE = 'https://job-boards.greenhouse.io/lightmatter/jobs/5473740008'
ASHBY = 'https://jobs.ashbyhq.com/bedrock/c51d682e-58ee/application?embed=true'
LEVER = 'https://jobs.lever.co/palantir/09846827-b931/apply'
SMART = 'https://jobs.smartrecruiters.com/BoschGroup/744000148575999-intern'


class ApiUrlTests(unittest.TestCase):
    def test_each_site(self):
        cases = {
            WORKDAY: ('workday', 'https://vizient.wd1.myworkdayjobs.com/wday/cxs/vizient/'
                                 'Vizient_Careers/job/Chicago-IL-60607/Intern---Analytics_36238R-1'),
            'https://acme.wd5.myworkdayjobs.com/en-US/Careers/job/NYC/X_R1':
                ('workday', 'https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/job/NYC/X_R1'),
            GREENHOUSE: ('greenhouse',
                         'https://boards-api.greenhouse.io/v1/boards/lightmatter/jobs/5473740008'),
            'https://boards.greenhouse.io/acme/jobs/12?gh_jid=12':
                ('greenhouse', 'https://boards-api.greenhouse.io/v1/boards/acme/jobs/12'),
            ASHBY: ('ashby', 'https://api.ashbyhq.com/posting-api/job-board/bedrock'),
            LEVER: ('lever', 'https://api.lever.co/v0/postings/palantir/09846827-b931'),
            SMART: ('smartrecruiters', 'https://api.smartrecruiters.com/v1/companies/'
                                       'BoschGroup/postings/744000148575999'),
        }
        for url, (site, api) in cases.items():
            self.assertEqual(postings.posting_api(url)[:2], (site, api), url)

    def test_other_sites_and_bad_links(self):
        for url in ['https://careers.publicisgroupe.com/jobs/182898?icims=1',
                    'http://jobs.lever.co/a/b', 'https://jobs.lever.co/onlycompany',
                    'https://boards.greenhouse.io/acme/jobs/notanumber',
                    'https://acme.myworkdayjobs.com/Careers/job/X', '', 'not a url']:
            self.assertFalse(postings.supported(url), url)


class TextTests(unittest.TestCase):
    def test_html_to_lines(self):
        text = postings.html_text('<p>About&nbsp;us</p><script>x()</script><ul><li>Python</li>'
                                  '<li><b>SQL</b> and R</li></ul><p></p>Done')
        self.assertEqual(text, 'About us\n- Python\n- SQL and R\nDone')

    def test_long_text_is_cut_under_tagger_limit(self):
        text = postings.tidy('\n'.join(['word ' * 30] * 1000))
        self.assertLessEqual(len(text), postings.MAX_CHARS)
        self.assertFalse(text.endswith(' '))


class FetchTests(unittest.TestCase):
    def fetch(self, url, response, cache=None):
        calls = []

        def get(api, site):
            calls.append(api)
            if isinstance(response, Exception):
                raise response
            return response
        return postings.fetch_posting(url, get, cache), calls

    def test_reads_each_site(self):
        cases = [
            (WORKDAY, {'jobPostingInfo': {'jobDescription': '<p>Use <b>SQL</b></p>'}}),
            (GREENHOUSE, {'content': '&lt;p&gt;Use SQL&lt;/p&gt;'}),
            (ASHBY, {'jobs': [{'id': 'other', 'descriptionPlain': 'no'},
                              {'id': 'c51d682e-58ee', 'descriptionPlain': 'Use SQL'}]}),
            (LEVER, {'descriptionPlain': 'Intro', 'additionalPlain': '',
                     'lists': [{'text': 'Requirements', 'content': '<li>Use SQL</li>'}]}),
            (SMART, {'jobAd': {'sections': {'qualifications': {'title': 'Qualifications',
                                                               'text': '<p>Use SQL</p>'}}}}),
        ]
        for url, data in cases:
            text, _ = self.fetch(url, data)
            self.assertIn('Use SQL', text, url)

    def test_gone_unreachable_and_unsupported(self):
        self.assertEqual(self.fetch(GREENHOUSE, ServiceError('Hiring site', 404))[0], '')
        self.assertEqual(self.fetch(ASHBY, {'jobs': []})[0], '')        # off the board
        self.assertEqual(self.fetch(LEVER, {'unexpected': 1})[0], '')
        self.assertIsNone(self.fetch(WORKDAY, ServiceError('Hiring site', 429))[0])
        self.assertIsNone(self.fetch(WORKDAY, ServiceError('Hiring site'))[0])
        text, calls = self.fetch('https://example.com/job/1', {'content': 'x'})
        self.assertEqual((text, calls), (None, []))

    def test_ashby_board_downloaded_once(self):
        cache = {}
        board = {'jobs': [{'id': 'c51d682e-58ee', 'descriptionPlain': 'A'},
                          {'id': 'b2', 'descriptionPlain': 'B'}]}
        self.assertEqual(self.fetch(ASHBY, board, cache)[0], 'A')
        text, calls = self.fetch('https://jobs.ashbyhq.com/bedrock/b2', board, cache)
        self.assertEqual((text, calls), ('B', []))


class PoliteGetterTests(unittest.TestCase):
    def test_spacing_and_retry_on_429(self):
        now, slept, answers = [0.0], [], [429, 429, 'ok']

        def sleep(seconds):
            slept.append(seconds)
            now[0] += seconds

        def fake_request(api, headers, method, service):
            answer = answers.pop(0)
            if answer != 'ok':
                raise ServiceError(service, answer)
            return {'ok': True}
        original = postings.json_request
        postings.json_request = fake_request
        try:
            get = postings.PoliteGetter(sleep, lambda: now[0])
            self.assertEqual(get('https://a', 'workday'), {'ok': True})
            self.assertEqual(slept[0], 5)                       # first 429: wait 5s
            self.assertIn(15, slept)                            # second 429: wait 15s
            answers.append('ok')
            slept.clear()
            get('https://b', 'workday')                         # right away: waits the gap
            self.assertAlmostEqual(sum(slept), postings.GAP['workday'])
            answers.extend([429] * 4)
            with self.assertRaises(ServiceError):               # gives up after the last wait
                get('https://c', 'workday')
            # A site that keeps refusing is skipped for the rest of the run, without waiting.
            answers.extend([429] * 3 * (postings.GIVE_UP_AFTER - 1))
            for _ in range(postings.GIVE_UP_AFTER - 1):
                with self.assertRaises(ServiceError):
                    get('https://d', 'workday')
            slept.clear()
            with self.assertRaises(ServiceError):
                get('https://e', 'workday')
            self.assertEqual(slept, [])
            answers.append('ok')
            self.assertEqual(get('https://f', 'lever'), {'ok': True})   # other sites still work
        finally:
            postings.json_request = original


class ImporterTests(unittest.TestCase):
    def test_which_saved_rows_get_filled(self):
        existing = {
            'needs': {'url': LEVER, 'status': 'pending', 'posting_text': None},
            'has': {'url': LEVER, 'status': 'pending', 'posting_text': 'text'},
            'gone': {'url': LEVER, 'status': 'pending', 'posting_text': ''},
            'expired': {'url': LEVER, 'status': 'expired', 'posting_text': None},
            'expiring': {'url': LEVER, 'status': 'active', 'posting_text': None},
            'other-site': {'url': 'https://example.com/1', 'status': 'active', 'posting_text': None},
            'needs2': {'url': GREENHOUSE, 'status': 'active', 'posting_text': None},
        }
        self.assertEqual(imp.postings_to_fill(existing, ['expiring']), ['needs', 'needs2'])
        self.assertEqual(imp.postings_to_fill(existing, [], limit=1), ['expiring'])

    def test_untagged_and_newest_filled_first(self):
        existing = {
            'thin-new': {'url': LEVER, 'status': 'pending', 'posting_text': None,
                         'tags': ['Python'], 'date_posted': '2026-11-10'},
            'untagged-old': {'url': LEVER, 'status': 'pending', 'posting_text': None,
                             'tags': [], 'date_posted': '2026-09-01'},
            'untagged-new': {'url': LEVER, 'status': 'pending', 'posting_text': None,
                             'tags': [], 'date_posted': '2026-11-01'},
            'untagged-nodate': {'url': LEVER, 'status': 'pending', 'posting_text': None,
                                'tags': None, 'date_posted': None},
            'thin-old': {'url': LEVER, 'status': 'pending', 'posting_text': None,
                         'tags': ['SQL'], 'date_posted': '2026-08-01'},
        }
        self.assertEqual(imp.postings_to_fill(existing, []),
                         ['untagged-new', 'untagged-old', 'untagged-nodate',
                          'thin-new', 'thin-old'])

    def test_short_text_tags_cleared_when_posting_arrives(self):
        thin = {'tags': ['Python'], 'status': 'pending'}
        self.assertTrue(imp.retag_after_posting(thin, 'Full posting'))
        self.assertTrue(imp.retag_after_posting({'tags': ['Python']}, 'Full posting'))
        self.assertFalse(imp.retag_after_posting(thin, ''))            # job gone: keep tags
        self.assertFalse(imp.retag_after_posting(thin, None))          # unreachable
        self.assertFalse(imp.retag_after_posting({'tags': []}, 'Full posting'))
        self.assertFalse(imp.retag_after_posting({'tags': ['Python'], 'status': 'approved'},
                                                 'Full posting'))      # reviewed: keep

    def test_save_posting_clears_tags_only_when_asked(self):
        sent = []
        original = imp.json_request
        imp.json_request = lambda url, headers, payload, **kw: sent.append(payload)
        try:
            db = type('DB', (), {'url': 'https://x', 'headers': {}})()
            imp.save_posting(db, 'a', 'text')
            imp.save_posting(db, 'b', 'text', retag=True)
        finally:
            imp.json_request = original
        self.assertEqual(sent, [{'posting_text': 'text'}, {'posting_text': 'text', 'tags': []}])

    def test_fetch_for_new_and_saved_rows(self):
        new = [{'url': LEVER}, {'url': 'https://example.com/1'}, {'url': GREENHOUSE}]
        existing = {'a': {'url': WORKDAY}, 'b': {'url': SMART}}
        answers = {LEVER: 'Lever text', GREENHOUSE: None, WORKDAY: '', SMART: None}
        filled = imp.fetch_postings(new, ['a', 'b'], existing, answers.get)
        self.assertEqual([r['posting_text'] for r in new], ['Lever text', None, None])
        self.assertEqual(filled, {'a': ''})                    # 'b' unreachable: next run

    def test_saved_rows_are_saved_as_they_arrive(self):
        existing = {'a': {'url': WORKDAY}, 'b': {'url': LEVER}, 'c': {'url': SMART}}
        saved = []
        answers = {WORKDAY: 'W', LEVER: 'L', SMART: None}
        filled = imp.fetch_postings([], ['a', 'b', 'c'], existing, answers.get,
                                    save=lambda i, text: saved.append((i, text)))
        self.assertEqual(sorted(saved), [('a', 'W'), ('b', 'L')])
        self.assertEqual(filled, {'a': 'W', 'b': 'L'})


if __name__ == '__main__':
    unittest.main()
