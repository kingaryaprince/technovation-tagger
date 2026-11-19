"""Tests for opportunity autotagging with OpenAI or Claude, against the real opportunities schema.
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


def openai_reply(text, status='completed', refusal=False):
    content = [{'type': 'refusal', 'refusal': 'no'}] if refusal else [{'type': 'output_text', 'text': text}]
    return {'status': status, 'output': [{'type': 'reasoning'}, {'type': 'message', 'content': content}]}


class OpenAITests(unittest.TestCase):
    def test_request_shape(self):
        calls = []
        ask = opp.openai_asker(lambda p: calls.append(p) or openai_reply(json.dumps(evidence({}))),
                               'gpt-4.1-nano', TAX)
        self.assertEqual(ask({'title': 'X'}), evidence({}))
        payload = calls[0]
        self.assertEqual(payload['model'], 'gpt-4.1-nano')
        self.assertFalse(payload['store'])
        self.assertEqual(payload['temperature'], 0)
        self.assertTrue(payload['text']['format']['strict'])
        self.assertIn('Taxonomy:', payload['instructions'])
        self.assertEqual(json.loads(payload['input']), {'title': 'X'})

    def test_reasoning_models_get_no_temperature(self):
        calls = []
        ask = opp.openai_asker(lambda p: calls.append(p) or openai_reply(json.dumps(evidence({}))),
                               'gpt-5-nano', TAX)
        ask({'title': 'X'})
        self.assertNotIn('temperature', calls[0])

    def test_bad_responses_are_skipped_not_saved(self):
        for reply in (openai_reply('{}', status='incomplete'), openai_reply('', refusal=True),
                      openai_reply('not json'), None):
            with self.assertRaises(opp.ValidationError):
                opp.openai_asker(lambda p, r=reply: r, 'gpt-4.1-nano', TAX)({'title': 'X'})


class ProviderTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(opp.provider_and_model({}), ('openai', 'gpt-4.1-nano'))
        self.assertEqual(opp.provider_and_model({'OPPORTUNITY_PROVIDER': 'Claude',
                                                 'ANTHROPIC_MODEL': 'claude-haiku-5-5'}),
                         ('claude', 'claude-haiku-5-5'))
        self.assertEqual(opp.provider_and_model({'OPPORTUNITY_MODEL': 'gpt-4.1-mini'}),
                         ('openai', 'gpt-4.1-mini'))

    def test_unknown_provider_and_missing_openai_key(self):
        with self.assertRaises(ValueError):
            opp.provider_and_model({'OPPORTUNITY_PROVIDER': 'gemini'})
        with self.assertRaises(ValueError) as ctx:
            opp.make_asker({'OPENAI_API_KEY': ''}, TAX)
        self.assertIn('OPENAI_API_KEY', str(ctx.exception))


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
        config = {'OPPORTUNITY_PROVIDER': 'claude', 'ANTHROPIC_MODEL': 'claude-opus-5-5'}
        with mock.patch.object(opp, 'settings', return_value=config), \
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

    def test_openai_is_the_default_and_saves_tags(self):
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = [ROW]
        sent = []
        answer = json.dumps(evidence({'interests': [('Job Searching', 'type', 'internship')]}))
        with mock.patch.object(opp, 'settings', return_value={'OPENAI_API_KEY': 'sk-test'}), \
                mock.patch.object(opp, 'SupabaseClient', return_value=db), \
                mock.patch.object(opp, 'OpenAIClient', return_value=lambda payload: openai_reply(answer)), \
                mock.patch.object(opp, 'json_request', lambda *a, **k: sent.append((a, k))), \
                mock.patch('builtins.print'):
            self.assertEqual(opp.run(limit=5), 0)
        self.assertEqual(sent[0][0][2]['tags'], ['Job Searching'])

    def test_reports_backlog_to_github_actions(self):
        import os, tempfile
        db = mock.Mock(url='https://x.supabase.co', headers={'apikey': 'k'})
        db.select.return_value = [ROW, {**ROW, 'id': 'r2'}]
        answer = json.dumps(evidence({'interests': [('Job Searching', 'type', 'internship')]}))
        with tempfile.NamedTemporaryFile('r', suffix='.txt') as out:
            for limit, more in ((2, 'true'), (5, 'false')):
                open(out.name, 'w').close()
                with mock.patch.dict(os.environ, {'GITHUB_OUTPUT': out.name}), \
                        mock.patch.object(opp, 'settings', return_value={'OPENAI_API_KEY': 'sk-test'}), \
                        mock.patch.object(opp, 'SupabaseClient', return_value=db), \
                        mock.patch.object(opp, 'OpenAIClient',
                                          return_value=lambda payload: openai_reply(answer)), \
                        mock.patch.object(opp, 'json_request', lambda *a, **k: None), \
                        mock.patch('builtins.print'):
                    opp.run(limit=limit)
                self.assertEqual(open(out.name).read(), f'tagged=2\nskipped=0\nmore={more}\n')

    def test_missing_key_is_reported(self):
        with self.assertRaises(ValueError) as ctx:
            opp.claude_client({'ANTHROPIC_API_KEY': ''})
        self.assertIn('ANTHROPIC_API_KEY', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
