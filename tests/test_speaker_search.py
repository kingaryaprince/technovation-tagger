"""Speaker search command (Week 7). No network calls: Supabase is faked."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from alumni_tagger import __main__ as cli
from alumni_tagger import search
from alumni_tagger.core import ROOT, load_taxonomy

SAMPLES = json.loads((ROOT / 'data/sample_profiles.json').read_text())
FORM_FIELDS = json.loads((ROOT / 'data/form_fields.json').read_text())

ROWS = [
    {'rank': 1, 'full_name': 'Maya Patel', 'match_count': 2, 'total_selected': 2,
     'availability': 'Available now', 'missing_tags': []},
    {'rank': 2, 'full_name': 'Jessica Tran', 'match_count': 1, 'total_selected': 2,
     'availability': None, 'missing_tags': [{'category': 'audiences', 'tag': 'Parents'}]},
]


class FakeDB:
    def __init__(self, result):
        self.calls, self.result = [], result

    def rpc(self, name, payload):
        self.calls.append((name, payload))
        return self.result


class ParseTagsTests(unittest.TestCase):
    def test_category_equals_tag(self):
        self.assertEqual(search.parse_tags(['audiences=Students', ' skills = Python ']),
                         [{'category': 'audiences', 'tag': 'Students'},
                          {'category': 'skills', 'tag': 'Python'}])

    def test_tag_may_contain_equals_and_slashes(self):
        self.assertEqual(search.parse_tags(['interests=AI/Machine Learning'])[0]['tag'], 'AI/Machine Learning')

    def test_rejects_bad_input(self):
        for bad in ([], None, ['Students'], ['audiences='], ['=Students'], ['audience=Students']):
            with self.assertRaises(ValueError):
                search.parse_tags(bad)

    def test_rejects_more_than_fifty(self):
        with self.assertRaises(ValueError):
            search.parse_tags([f'skills=Tag{i}' for i in range(51)])


class CategoryCoverageTests(unittest.TestCase):
    def test_every_llm_category_is_searchable(self):
        self.assertTrue(set(load_taxonomy()['categories']) <= set(search.CATEGORIES))

    def test_form_categories_exist_in_form_fields(self):
        derived = {'deep_experience', 'moderate_experience', 'emerging_interest'}
        llm = set(load_taxonomy()['categories'])
        for category in set(search.CATEGORIES) - derived - llm:
            self.assertIn(category, FORM_FIELDS, category)

    def test_sql_function_knows_the_same_categories(self):
        sql = (ROOT / 'sql/005_speaker_search.sql').read_text()
        for category in search.CATEGORIES:
            self.assertIn(f"'{category}'", sql, category)


class SearchCallTests(unittest.TestCase):
    def test_calls_rpc_with_defaults(self):
        db = FakeDB(ROWS)
        picks = [{'category': 'audiences', 'tag': 'Students'}]
        self.assertEqual(search.search(db, picks), ROWS)
        self.assertEqual(db.calls, [('ranked_speakers_search',
                                     {'p_tags': picks, 'p_limit': 5, 'p_speakers_only': True})])

    def test_limit_bounds(self):
        for bad in (0, 51):
            with self.assertRaises(ValueError):
                search.search(FakeDB([]), [{'category': 'skills', 'tag': 'Python'}], limit=bad)

    def test_format_results(self):
        text = search.format_results(ROWS)
        self.assertIn('1. Maya Patel  2/2 tags  (Available now)', text)
        self.assertIn('2. Jessica Tran  1/2 tags  (availability not given)', text)
        self.assertIn('missing: Parents', text)
        self.assertIn('No one matches', search.format_results([]))

    def test_format_options(self):
        rows = [{'category': 'audiences', 'tag': 'Students', 'people': 3},
                {'category': 'skills', 'tag': 'Python', 'people': 2}]
        self.assertEqual(search.format_options(rows, 'skills'), 'skills\n  Python (2)')
        self.assertIn('No tags yet', search.format_options([]))
        with self.assertRaises(ValueError):
            search.format_options(rows, 'nope')


class CommandTests(unittest.TestCase):
    def run_cli(self, argv, db):
        out = io.StringIO()
        with mock.patch.object(cli, 'settings', return_value={}), \
             mock.patch.object(cli, 'SupabaseClient', return_value=db), \
             mock.patch.object(cli, 'OpenAIClient', side_effect=AssertionError('no OpenAI')), \
             redirect_stdout(out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_search_command(self):
        db = FakeDB(ROWS)
        code, out = self.run_cli(['search', '--tag', 'audiences=Students', '--tag', 'skills=Python',
                                  '--limit', '3', '--all'], db)
        self.assertEqual(code, 0)
        self.assertIn('1. Maya Patel', out)
        self.assertEqual(db.calls[0][1]['p_limit'], 3)
        self.assertFalse(db.calls[0][1]['p_speakers_only'])

    def test_search_json(self):
        code, out = self.run_cli(['search', '--tag', 'skills=Python', '--json'], FakeDB(ROWS))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)[0]['full_name'], 'Maya Patel')

    def test_search_bad_tag_is_an_error_not_a_call(self):
        db = FakeDB(ROWS)
        code, _ = self.run_cli(['search', '--tag', 'Students'], db)
        self.assertEqual(code, 1)
        self.assertEqual(db.calls, [])

    def test_filter_options_command(self):
        db = FakeDB([{'category': 'audiences', 'tag': 'Students', 'people': 4}])
        code, out = self.run_cli(['filter-options'], db)
        self.assertEqual(code, 0)
        self.assertEqual(db.calls, [('speaker_filter_options', {})])
        self.assertIn('Students (4)', out)


class SampleSpeakerAnswerTests(unittest.TestCase):
    def test_every_sample_has_form_answers(self):
        for s in SAMPLES:
            if not s.get('email'):
                continue
            extra = s['speaker_answers']
            for key in extra:
                self.assertIn(key, FORM_FIELDS, f"{s['id']}: {key}")
            self.assertTrue(extra['availability'] and extra['speaking_types'])
            if 'Not Interested' not in extra['speaking_types']:
                self.assertTrue(extra.get('audiences'), s['id'])

    def test_speaker_answers_never_reach_the_model(self):
        from alumni_tagger.core import select_inputs
        for s in SAMPLES:
            if s.get('speaker_answers'):
                picked = select_inputs({**s['answers'], **s['speaker_answers']}, load_taxonomy())
                self.assertEqual(set(picked) & set(s['speaker_answers']), set())

    def test_load_samples_sends_speaker_answers(self):
        db = FakeDB(None)
        cli.ingest_samples(db, SAMPLES)
        first = db.calls[0][1]['p_answers']
        self.assertEqual(first['availability'], SAMPLES[0]['speaker_answers']['availability'])
        self.assertEqual(first['project'], SAMPLES[0]['answers']['project'])



class SampleFileMatchesSupabaseTests(unittest.TestCase):
    """sql/008 loaded the sample profiles into Supabase; data/sample_profiles.json must hold
    the same answers, so load-samples and sample-report work on the same people."""

    def test_sample_file_matches_sql_008(self):
        import re
        sql = (ROOT / 'sql/008_replace_sample_profiles.sql').read_text()
        rows = re.findall(r"\('(sample-\d\d@example\.com)', '((?:[^']|'')*)', '(?:[^']|'')*',\n"
                          r"   '((?:[^']|'')*)'::jsonb,\n   '((?:[^']|'')*)'::jsonb\)", sql)
        self.assertEqual(len(rows), 20)
        by_email = {s['email']: s for s in SAMPLES if s.get('email')}
        for email, name, raw, tags in rows:
            raw, tags = json.loads(raw.replace("''", "'")), json.loads(tags.replace("''", "'"))
            s = by_email[email]
            self.assertEqual(name.replace("''", "'"), s['full_name'])
            sent = {**s['answers'], **s['speaker_answers'], 'full_name': s['full_name'],
                    'email': s['email'], '_sample_id': s['id']}
            self.assertEqual(raw, sent, email)
            for cat, expected in s['expected'].items():
                if cat == 'interests':  # 008 also holds the since/impact interests
                    self.assertEqual(set(tags[cat]), set(expected) | set(
                        s['context_tags']['interests_from_since_and_impact']), email)
                else:
                    self.assertEqual(tags[cat], expected, f'{email} {cat}')

    def test_expected_tags_use_the_finalized_taxonomy(self):
        tax = load_taxonomy()['categories']
        for s in SAMPLES:
            for cat, tags in s['expected'].items():
                self.assertTrue(set(tags) <= set(tax[cat]['tags']), f"{s['id']} {cat}")
            for tag in s.get('context_tags', {}).get('interests_from_since_and_impact', []):
                self.assertIn(tag, tax['interests']['tags'], s['id'])

if __name__ == '__main__':
    unittest.main()
