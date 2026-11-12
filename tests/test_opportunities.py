"""Tests for opportunity autotagging with Claude, against the real opportunities schema.
No network calls."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

from alumni_tagger import opportunities as opp
from alumni_tagger.core import load_taxonomy

TAX = opp.opportunity_taxonomy(load_taxonomy())
ROW = {'id': 'r1', 'type': 'internship', 'title': 'Data Science Intern',
       'description': 'Data Science Intern at Acme.\nCategory: AI/ML/Data.'}


def evidence(picks):
    """The model's answer: {category: [(tag, field, quote)]}, empty for the rest."""
    return {c: [{'tag': t, 'source_field': f, 'quote': q} for t, f, q in picks.get(c, [])]
            for c in TAX['categories']}


def fake_client(stop_reason='end_turn', text=None, calls=None):
    def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        body = text if text is not None else json.dumps(evidence({}))
        return NS(stop_reason=stop_reason, content=[NS(type='thinking'), NS(type='text', text=body)])
    return NS(beta=NS(messages=NS(create=create)))


class QueryTests(unittest.TestCase):
    def test_selects_empty_tags_and_skips_expired(self):
        self.assertIn('tags=eq.%7B%7D', opp.UNTAGGED)
        self.assertIn('status=neq.expired', opp.UNTAGGED)


class TagTests(unittest.TestCase):
    def test_tags_with_quotes_and_drops_catchalls(self):
        ask = lambda inputs: evidence({'interests': [('Job Searching', 'type', 'internship')],
                                       'industry': [('Other', 'title', 'Intern')]})
        self.assertEqual(opp.tag_opportunity(ROW, ask, TAX), ['Job Searching'])

    def test_quote_not_in_listing_is_rejected(self):
        ask = lambda inputs: evidence({'interests': [('Job Searching', 'type', 'full-time role')]})
        with self.assertRaises(opp.ValidationError):
            opp.tag_opportunity(ROW, ask, TAX)


class ClaudeTests(unittest.TestCase):
    def test_request_shape(self):
        calls = []
        ask = opp.claude_asker(fake_client(calls=calls), 'claude-opus-5-5', TAX)
        self.assertEqual(ask({'title': 'x'}), evidence({}))
        req = calls[0]
        self.assertEqual(req['model'], 'claude-opus-5-5')
        self.assertEqual(req['output_config']['format']['type'], 'json_schema')
        self.assertEqual(req['system'][0]['cache_control'], {'type': 'ephemeral'})
        self.assertIn('Taxonomy:', req['system'][0]['text'])
        self.assertEqual(req['messages'], [{'role': 'user', 'content': '{"title": "x"}'}])
        self.assertEqual(req['fallbacks'], 'default')
        self.assertNotIn('temperature', req)
        self.assertNotIn('thinking', req)

    def test_refusal_and_cutoff_save_nothing(self):
        for reason in ['refusal', 'max_tokens']:
            ask = opp.claude_asker(fake_client(stop_reason=reason), 'm', TAX)
            with self.assertRaises(opp.ValidationError):
                ask({'title': 'x'})

    def test_bad_json(self):
        ask = opp.claude_asker(fake_client(text='not json'), 'm', TAX)
        with self.assertRaises(opp.ValidationError):
            ask({'title': 'x'})


class RunTests(unittest.TestCase):
    def test_saves_tags_and_version_only(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = [ROW]
        sent = []
        answer = json.dumps(evidence({'interests': [('Job Searching', 'type', 'internship')]}))
        with mock.patch.object(opp, 'settings', return_value={'ANTHROPIC_MODEL': 'claude-opus-5-5'}), \
                mock.patch.object(opp, 'SupabaseClient', return_value=db), \
                mock.patch.object(opp, 'claude_client', return_value=fake_client(text=answer)), \
                mock.patch.object(opp, 'json_request', lambda *a, **k: sent.append((a, k))), \
                mock.patch('builtins.print'):
            self.assertEqual(opp.run(limit=5), 0)
        self.assertTrue(db.select.call_args[0][0].startswith(opp.UNTAGGED))
        (url, _, body), kwargs = sent[0]
        self.assertTrue(url.endswith('/rest/v1/opportunities?id=eq.r1'))
        self.assertEqual(kwargs['method'], 'PATCH')
        self.assertEqual(body, {'tags': ['Job Searching'], 'taxonomy_version': TAX['version']})

    def test_missing_key_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            opp.claude_client({'ANTHROPIC_API_KEY': ''})
        self.assertIn('ANTHROPIC_API_KEY', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
