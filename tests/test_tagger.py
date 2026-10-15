import copy
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from alumni_tagger.core import (ROOT, load_taxonomy, select_inputs, schema, validate,
                                tag_profile, fingerprint, ValidationError)
from alumni_tagger.clients import SupabaseClient, ServiceError, json_request
from alumni_tagger.worker import run_once


class TaggerTests(unittest.TestCase):
    def setUp(self):
        self.tax = load_taxonomy()
        self.empty = {k: [] for k in self.tax['categories']}
        self.answers = {'skills': 'I use Python for analysis.'}
        self.valid = copy.deepcopy(self.empty)
        self.valid['skills'] = [{'tag': 'Python', 'source_field': 'skills', 'quote': 'I use Python'}]

    def response(self, data):
        return {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(data)}]}], 'usage': {'input_tokens': 10}}

    def test_valid_evidence(self):
        self.assertEqual(validate(self.valid, self.answers, self.tax)['skills'], ['Python'])

    def test_unknown_tag_rejected(self):
        self.valid['skills'][0]['tag'] = 'Made-up skill'
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_wrong_category_source_rejected(self):
        self.valid['skills'][0]['source_field'] = 'interests'
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_invented_quote_rejected(self):
        self.valid['skills'][0]['quote'] = 'I use Java'
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_blank_quote_rejected(self):
        self.valid['skills'][0]['quote'] = ''
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_duplicate_tag_rejected(self):
        self.valid['skills'] *= 2
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_missing_category_rejected(self):
        del self.valid['industry']
        with self.assertRaises(ValidationError): validate(self.valid, self.answers, self.tax)

    def test_private_identity_fields_excluded(self):
        inputs = select_inputs({'email': 'secret@example.com', 'full_name': 'Name',
            'birthdate': '2000-01-01', '_original_items': ['secret'], 'skills': 'Python'}, self.tax)
        self.assertEqual(set(inputs), {'current_role','since_technovation','project','interests','skills','story_angles'})
        self.assertNotIn('secret', json.dumps(inputs))

    def test_structured_answers_are_not_converted_to_text(self):
        with self.assertRaises(ValidationError): select_inputs({'skills': ['Python']}, self.tax)

    def test_oversized_input_rejected(self):
        with self.assertRaises(ValidationError): select_inputs({'skills': 'x'*12001}, self.tax)

    def test_blank_skips_api(self):
        def never(_): self.fail('Blank profile should not call API')
        self.assertEqual(tag_profile({}, 'test', never)['tags'], self.empty)

    def test_payload_schema_and_privacy(self):
        def fake(payload):
            self.assertFalse(payload['store'])
            self.assertTrue(payload['text']['format']['strict'])
            self.assertNotIn('secret@example.com', payload['input'])
            return self.response(self.valid)
        result = tag_profile({**self.answers, 'email':'secret@example.com'}, 'test', fake)
        self.assertEqual(result['tags']['skills'], ['Python'])
        self.assertEqual(result['usage']['input_tokens'], 10)

    def test_refusal_rejected(self):
        def fake(_): return {'status':'completed','output':[{'type':'message','content':[{'type':'refusal'}]}]}
        with self.assertRaises(ValidationError): tag_profile(self.answers, 'test', fake)

    def test_incomplete_rejected(self):
        with self.assertRaises(ValidationError):
            tag_profile(self.answers, 'test', lambda _: {'status':'incomplete'})

    def test_invalid_json_rejected(self):
        def fake(_): return {'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'not json'}]}]}
        with self.assertRaises(ValidationError): tag_profile(self.answers, 'test', fake)

    def test_career_stage_single(self):
        self.valid['career_stage'] = [
            {'tag': t, 'source_field':'current_role','quote':'student and working'}
            for t in ['Student', 'Student + Working']]
        with self.assertRaises(ValidationError):
            validate(self.valid, {**self.answers,'current_role':'student and working'}, self.tax)

    def test_fingerprint_changes_with_input_model_or_taxonomy(self):
        base = fingerprint(self.answers, self.tax, 'test')
        self.assertNotEqual(base, fingerprint({'skills':'Java'}, self.tax, 'test'))
        self.assertNotEqual(base, fingerprint(self.answers, self.tax, 'different'))
        changed = copy.deepcopy(self.tax); changed['version'] = 'changed'
        self.assertNotEqual(base, fingerprint(self.answers, changed, 'test'))

    def test_all_expected_labels_in_taxonomy(self):
        samples = json.loads((ROOT/'data/sample_profiles.json').read_text())
        for sample in samples:
            select_inputs(sample['answers'], self.tax)
            for category, values in sample['expected'].items():
                self.assertTrue(set(values) <= set(self.tax['categories'][category]['tags']))

    def test_form_mapping_covers_all_llm_inputs(self):
        fields = json.loads((ROOT/'data/form_fields.json').read_text())
        for category in self.tax['categories'].values():
            self.assertTrue(set(category['source_fields']) <= set(fields))


class ClientTests(unittest.TestCase):
    def test_secret_header(self):
        db = SupabaseClient({'SUPABASE_URL':'https://test.supabase.co','SUPABASE_KEY':'sb_secret_fake'})
        self.assertNotIn('Authorization', db.headers)

    def test_legacy_header(self):
        db = SupabaseClient({'SUPABASE_URL':'https://test.supabase.co','SUPABASE_KEY':'fake.jwt'})
        self.assertEqual(db.headers['Authorization'], 'Bearer fake.jwt')

    def test_wrong_host_rejected(self):
        for url in ['http://test.supabase.co','https://evil.com','https://test.supabase.co@evil.com','https://test.supabase.co/rest']:
            with self.assertRaises(ValueError):
                SupabaseClient({'SUPABASE_URL':url,'SUPABASE_KEY':'fake'})

    def test_publishable_key_rejected(self):
        with self.assertRaises(ValueError):
            SupabaseClient({'SUPABASE_URL':'https://test.supabase.co','SUPABASE_KEY':'sb_publishable_fake'})

    @patch('alumni_tagger.clients.request.build_opener')
    @patch('alumni_tagger.clients.time.sleep')
    def test_auth_failure_not_retried_or_leaked(self, sleep, build):
        build.return_value.open.side_effect = HTTPError('https://example.com',401,'secret body',{},None)
        with self.assertRaises(ServiceError) as caught:
            json_request('https://example.com',{}, {},retries=1)
        self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(build.return_value.open.call_count, 1)
        sleep.assert_not_called()

    @patch('alumni_tagger.clients.request.build_opener')
    @patch('alumni_tagger.clients.time.sleep')
    def test_transient_retry_bounded(self, sleep, build):
        build.return_value.open.side_effect = HTTPError('https://example.com',429,'rate',{},None)
        with self.assertRaises(ServiceError): json_request('https://example.com',{}, {},retries=1)
        self.assertEqual(build.return_value.open.call_count, 2)


class FakeDB:
    def __init__(self, job, saved=True):
        self.job, self.saved, self.calls = job, saved, []
    def rpc(self, name, data):
        self.calls.append((name, data))
        if name == 'claim_alumni_tagging':
            job, self.job = self.job, None
            return [job] if job else []
        return self.saved


class WorkerTests(unittest.TestCase):
    def job(self, answers=None):
        return {'id':'test-id','revision':2,'lease_token':'token','raw_answers':answers or {}}

    def test_completes_blank_without_api(self):
        db = FakeDB(self.job())
        counts = run_once(db, lambda _: self.fail('API called'), 'test', emit=lambda _:None)
        self.assertEqual(counts['completed'], 1)
        completion = db.calls[1][1]
        self.assertEqual(completion['p_revision'], 2)
        self.assertEqual(completion['p_token'], 'token')

    def test_stale_result_discarded(self):
        counts = run_once(FakeDB(self.job(), False), None, 'test', emit=lambda _:None)
        self.assertEqual(counts['stale'], 1)

    def test_failed_validation_marks_failed(self):
        db = FakeDB(self.job({'skills':'Python'}))
        counts = run_once(db, lambda _: {'status':'incomplete'}, 'test', emit=lambda _:None)
        self.assertEqual(counts['failed'], 1)
        self.assertEqual(db.calls[1][0], 'fail_alumni_tagging')

    def test_api_failure_stops_run(self):
        db = FakeDB(self.job({'skills':'Python'}))
        def fail(_): raise ServiceError('OpenAI',401)
        with self.assertRaises(ServiceError): run_once(db, fail, 'test', emit=lambda _:None)
        self.assertEqual(db.calls[-1][0], 'fail_alumni_tagging')

    def test_limit_enforced(self):
        db = FakeDB(self.job())
        run_once(db, None, 'test', limit=1, emit=lambda _:None)
        self.assertEqual(len(db.calls), 2)


if __name__ == '__main__': unittest.main()
