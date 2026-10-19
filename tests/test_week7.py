"""Tests for the Oct 13 additions: full sample set, consistency scoring, load-samples,
sample-report, temperature, continuous watch mode. No network calls."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from alumni_tagger import __main__ as cli
from alumni_tagger.clients import ServiceError
from alumni_tagger.core import ROOT, load_taxonomy, tag_profile
from alumni_tagger.scoring import compare, consistency, summarize, markdown

TAX = load_taxonomy()
SAMPLES = json.loads((ROOT / 'data/sample_profiles.json').read_text())


def fake_api_from(answer_tags, calls=None):
    """Return a fake OpenAI call that answers with the given tags, quoting real source text."""
    def call(payload):
        if calls is not None:
            calls.append(payload)
        inputs = json.loads(payload['input'])
        out = {}
        for cat, spec in TAX['categories'].items():
            field = next((f for f in spec['source_fields'] if inputs.get(f)), None)
            tags = answer_tags(cat) if field else []
            out[cat] = [{'tag': t, 'source_field': field, 'quote': inputs[field][:12]} for t in tags]
        return {'status': 'completed', 'usage': {},
                'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(out)}]}]}
    return call


class SampleSetTests(unittest.TestCase):
    def test_all_twenty_pdf_profiles_present(self):
        pdf = [s for s in SAMPLES if s.get('email')]
        self.assertEqual(len(pdf), 20)
        self.assertEqual(len({s['email'] for s in pdf}), 20)
        for s in pdf:
            self.assertTrue(s['email'].startswith('sample-') and s['email'].endswith('@example.com'))
            self.assertTrue(s['answers']['current_role'] and s['answers']['project'])

    def test_paraphrased_answers_mostly_avoid_label_wording(self):
        # The point of the samples is to test meaning, not string matching.
        hits = total = 0
        for s in SAMPLES:
            for cat in ('interests', 'skills', 'story_angles'):
                text = s['answers'].get(cat, '').lower()
                for tag in s['expected'].get(cat, []):
                    total += 1
                    hits += tag.lower() in text
        self.assertLess(hits / total, 0.35, f'{hits}/{total} labels appear verbatim')


class TemperatureTests(unittest.TestCase):
    def test_gpt41_gets_temperature_zero(self):
        calls = []
        tag_profile({'skills': 'Python'}, 'gpt-4.1-mini', fake_api_from(lambda c: [], calls), TAX)
        self.assertEqual(calls[0]['temperature'], 0)

    def test_reasoning_models_skip_temperature(self):
        for model in ('gpt-5-mini', 'o4-mini'):
            calls = []
            tag_profile({'skills': 'Python'}, model, fake_api_from(lambda c: [], calls), TAX)
            self.assertNotIn('temperature', calls[0])


class ScoringTests(unittest.TestCase):
    def test_compare(self):
        c = compare({'skills': ['Python', 'SQL']}, {'skills': ['Python', 'Java'], 'interests': ['X']})
        self.assertEqual(c, {'skills': {'tp': ['Python'], 'missing': ['SQL'], 'extra': ['Java']}})

    def test_consistency_detects_changes(self):
        self.assertEqual(consistency([{'a': ['x']}, {'a': ['x']}]), {})
        self.assertIn('a', consistency([{'a': ['x']}, {'a': ['x', 'y']}]))

    def test_summary_and_markdown(self):
        profiles = [{'id': 'p1', 'expected': {'skills': ['Python']},
                     'runs': [{'skills': ['Python']}, {'skills': ['Python', 'SQL']}]}]
        s = summarize(profiles)
        self.assertEqual(s['per_category']['skills']['tp'], 2)
        self.assertEqual(s['per_category']['skills']['fp'], 1)
        self.assertEqual(s['consistency']['unstable_profiles'][0]['id'], 'p1')
        md = markdown(s, 'T', 2, 1)
        self.assertIn('| skills | 67% | 100% |', md)
        self.assertIn('p1 / skills', md)


class EvaluateEndToEndTests(unittest.TestCase):
    def test_perfect_fake_model_scores_100_and_is_stable(self):
        expected = {s['id']: s['expected'] for s in SAMPLES}
        current = {}

        def answer(cat):
            return expected[current['id']].get(cat, [])

        api = fake_api_from(answer)
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()):
            out = Path(d) / 'eval.json'
            real = cli.tag_profile

            def tracking(answers, model, api_call, taxonomy=None):
                current['id'] = next(s['id'] for s in SAMPLES if s['answers'] is answers)
                return real(answers, model, api_call, taxonomy)
            with mock.patch.object(cli, 'tag_profile', tracking):
                cli.evaluate(SAMPLES, api, 'gpt-4.1-mini', out, runs=2)
            report = json.loads(out.read_text())
            self.assertEqual(report['summary']['overall']['fp'], 0)
            self.assertEqual(report['summary']['overall']['fn'], 0)
            self.assertEqual(report['summary']['consistency']['identical_category_share'], 1.0)
            self.assertTrue(out.with_suffix('.md').exists())


class FakeDB:
    def __init__(self, rows=None):
        self.calls, self.rows = [], rows or []

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return 'id'

    def select(self, query):
        self.query = query
        return self.rows


class LoadSamplesTests(unittest.TestCase):
    def test_load_samples_uses_form_rpc(self):
        db = FakeDB()
        n = cli.ingest_samples(db, SAMPLES)
        self.assertEqual(n, 20)
        name, payload = db.calls[0]
        self.assertEqual(name, 'ingest_alumni_intake')
        self.assertEqual(payload['p_email'], 'sample-01@example.com')
        self.assertEqual(payload['p_answers']['_sample_id'], SAMPLES[0]['id'])
        self.assertIn('current_role', payload['p_answers'])

    def test_sample_report_scores_saved_tags(self):
        s = SAMPLES[0]
        rows = [{'email': s['email'], 'tagging_status': 'completed', 'tags': s['expected'],
                 'prompt_version': 'v3', 'model': 'm'},
                {'email': SAMPLES[1]['email'], 'tagging_status': 'pending', 'tags': None,
                 'prompt_version': None, 'model': None}]
        db = FakeDB(rows)
        with tempfile.TemporaryDirectory() as d, redirect_stdout(io.StringIO()) as out:
            cli.sample_report(db, SAMPLES, Path(d) / 'r.json')
            report = json.loads((Path(d) / 'r.json').read_text())
        self.assertIn('email=like.sample-*%40example.com', db.query)
        self.assertEqual(report['summary']['overall']['fn'], 0)
        self.assertEqual(report['not_completed'], [SAMPLES[1]['id'] + ': pending'])

    def test_sample_report_with_nothing_done_explains(self):
        with self.assertRaisesRegex(ValueError, 'load-samples'):
            cli.sample_report(FakeDB([]), SAMPLES, '/dev/null')


class WatchModeTests(unittest.TestCase):
    def _run(self, side_effects):
        cfg = {'OPENAI_API_KEY': 'k', 'OPENAI_MODEL': 'gpt-4.1-mini',
               'SUPABASE_URL': 'https://t.supabase.co', 'SUPABASE_KEY': 'sb_secret_x'}
        with mock.patch.object(cli, 'settings', return_value=cfg), \
             mock.patch.object(cli, 'run_once', side_effect=side_effects), \
             mock.patch.object(cli.time, 'sleep', side_effect=[None, KeyboardInterrupt]), \
             redirect_stdout(io.StringIO()) as out:
            code = cli.main(['worker', '--watch'])
        return code, out.getvalue()

    def test_keeps_going_after_transient_error_and_jobs(self):
        code, out = self._run([{'completed': 1, 'failed': 0, 'stale': 0},
                               {'completed': 0, 'failed': 0, 'stale': 0},
                               ServiceError('OpenAI', 503),
                               {'completed': 0, 'failed': 0, 'stale': 0}])
        self.assertEqual(code, 130)  # stopped by Ctrl+C, not by a job limit
        self.assertIn('"completed": 1', out)
        self.assertIn('Retrying', out)

    def test_stops_on_bad_key(self):
        code, _ = self._run([ServiceError('Supabase', 401)])
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
