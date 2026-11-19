"""Tests for sending the opportunity digest. No network: Supabase, Gmail SMTP and IMAP are faked."""
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from alumni_tagger import digest, send_digest as sd

TODAY = date(2026, 11, 19)
NOW = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
CONFIG = {'DIGEST_FROM_EMAIL': 'technovationpilot@gmail.com', 'DIGEST_EMAIL_PASSWORD': 'abcdabcdabcdabcd'}


def opp(n, **extra):
    return {'id': f'o{n}', 'title': f'Opportunity {n}', 'type': 'internship', 'organization': 'Org',
            'url': f'https://example.org/{n}', 'deadline': TODAY + timedelta(days=20), 'rolling': False,
            'matched_tags': ['Python'], **extra}


ANA = {'id': 'a1', 'full_name': 'Ana Lee', 'email': 'Ana@Example.org'}
BO = {'id': 'b2', 'full_name': 'Bo Kim', 'email': 'bo@example.org'}
CY = {'id': 'c3', 'full_name': 'Cy', 'email': ''}


class PlanTests(unittest.TestCase):
    def test_reasons(self):
        pairs = [(ANA, [opp(1), opp(2)]), (BO, [opp(3)]), (CY, [opp(4)])]
        plans = sd.plan(pairs, set(), [], NOW)
        self.assertEqual([(a['id'], len(o), why) for a, o, why in plans],
                         [('a1', 2, ''), ('b2', 1, ''), ('c3', 0, 'no email address')])

    def test_unsubscribed_only_and_no_matches(self):
        pairs = [(ANA, [opp(1)]), (BO, [])]
        plans = sd.plan(pairs, {'ana@example.org'}, [], NOW)
        self.assertEqual([why for _, _, why in plans], ['unsubscribed', 'no matching opportunities'])
        plans = sd.plan([(ANA, [opp(1)]), (BO, [opp(2)])], set(), [], NOW, only={'bo@example.org'})
        self.assertEqual([why for _, _, why in plans], ['not in --only', ''])

    def test_recent_digest_and_repeats(self):
        recent = [{'response_id': 'a1', 'opportunity_keys': ['o1'], 'sent_at': (NOW - timedelta(days=3)).isoformat()}]
        self.assertEqual(sd.plan([(ANA, [opp(1), opp(2)])], set(), recent, NOW)[0][2], 'got one 3 day(s) ago')
        older = [{'response_id': 'a1', 'opportunity_keys': ['o1'], 'sent_at': '2026-10-01T00:00:00Z'}]
        _, opps, why = sd.plan([(ANA, [opp(1), opp(2)])], set(), older, NOW)[0]
        self.assertEqual((why, [o['id'] for o in opps]), ('', ['o2']))   # o1 already sent: left out
        _, _, why = sd.plan([(ANA, [opp(1)])], set(), older, NOW)[0]
        self.assertEqual(why, 'no new matches since the last digest')

    def test_clean_emails(self):
        self.assertEqual(sd.clean_emails(' A@x.com, b@y.org ,'), ['a@x.com', 'b@y.org'])
        with self.assertRaises(ValueError):
            sd.clean_emails('not-an-email')


class EmailTests(unittest.TestCase):
    def tearDown(self):
        digest.UNSUBSCRIBE_EMAIL = ''

    def test_unsubscribe_link_and_header(self):
        digest.UNSUBSCRIBE_EMAIL = 'technovationpilot@gmail.com'
        msg = digest.build_email(ANA, [opp(1)], TODAY, 'technovationpilot@gmail.com')
        self.assertEqual(msg['From'], 'Technovation Alumni Hub <technovationpilot@gmail.com>')
        self.assertTrue(msg['List-Unsubscribe'].startswith('<mailto:technovationpilot@gmail.com?subject=unsubscribe'))
        html = msg.get_body(('html',)).get_content()
        text = msg.get_body(('plain',)).get_content()
        self.assertIn('Unsubscribe</a>', html)
        self.assertIn('subject "unsubscribe"', text)

    def test_preview_has_no_unsubscribe(self):
        msg = digest.build_email(ANA, [opp(1)], TODAY)
        self.assertIsNone(msg['List-Unsubscribe'])

    def test_test_copy_goes_to_tester(self):
        msg = digest.build_email(ANA, [opp(1)], TODAY, 'x@gmail.com', to='me@team.org',
                                 subject_prefix='[TEST for Ana Lee] ')
        self.assertEqual(msg['To'], 'me@team.org')
        self.assertTrue(msg['Subject'].startswith('[TEST for Ana Lee] Ana, your top'))


class FakeMailer:
    def __init__(self):
        self.sent = []
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        pass
    def send(self, msg):
        self.sent.append(msg)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        self.db.select.return_value = []
        self.pairs = [(ANA, [opp(1)]), (BO, [opp(2)])]

    def run_sd(self, **kw):
        mailer = FakeMailer()
        with mock.patch.object(sd, 'settings', return_value=dict(CONFIG)), \
                mock.patch.object(sd, 'inbox_unsubscribes', return_value=set()), \
                mock.patch.object(sd, 'json_request') as req, \
                mock.patch('builtins.print'):
            sd.run(today=TODAY, now=NOW, db=self.db, mailer=mailer, pairs=self.pairs, **kw)
        digest.UNSUBSCRIBE_EMAIL = ''
        return mailer, req

    def test_preview_sends_nothing(self):
        mailer, req = self.run_sd()
        self.assertEqual(mailer.sent, [])
        req.assert_not_called()

    def test_test_mode_sends_copies_and_records_nothing(self):
        mailer, req = self.run_sd(test_to=['me@team.org'])
        self.assertEqual([m['To'] for m in mailer.sent], ['me@team.org', 'me@team.org'])
        self.assertFalse(any('digest_deliveries' in c.args[0] for c in req.call_args_list))

    def test_real_send_records_each_delivery(self):
        mailer, req = self.run_sd(send=True, max_emails=1)
        self.assertEqual([m['To'] for m in mailer.sent], ['Ana@Example.org'])
        rows = [c.args[2] for c in req.call_args_list if c.args[0].endswith('/digest_deliveries')]
        self.assertEqual(rows, [{'response_id': 'a1', 'email': 'ana@example.org', 'opportunity_keys': ['o1'],
                                 'message_id': mailer.sent[0]['Message-ID']}])

    def test_sending_needs_gmail_settings(self):
        with mock.patch.object(sd, 'settings', return_value={}), mock.patch('builtins.print'):
            with self.assertRaises(ValueError):
                sd.run(send=True, db=self.db, mailer=FakeMailer(), pairs=self.pairs)


class InboxTests(unittest.TestCase):
    def test_reads_unsubscribe_senders(self):
        box = mock.Mock()
        box.search.return_value = ('OK', [b'1 2 3'])
        box.fetch.side_effect = [
            ('OK', [(b'1', b'From: Ana Lee <Ana@Example.org>\r\n\r\n')]),
            ('OK', [(b'2', b'From: technovationpilot@gmail.com\r\n\r\n')]),   # our own: ignored
            ('OK', [(b'3', b'From: nobody\r\n\r\n')])]
        found = sd.inbox_unsubscribes('technovationpilot@gmail.com', 'pw', lambda host: box, TODAY)
        self.assertEqual(found, {'ana@example.org'})
        box.select.assert_called_with('INBOX', readonly=True)
        self.assertIn('"unsubscribe"', box.search.call_args.args)

    def test_mailer_logs_in_and_pauses(self):
        server = mock.Mock()
        waits = []
        with sd.Mailer('u@gmail.com', 'pw', lambda *a, **k: server, waits.append) as m:
            m.send('one')
            m.send('two')
        server.login.assert_called_with('u@gmail.com', 'pw')
        self.assertEqual(waits, [sd.PAUSE])
        server.quit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
