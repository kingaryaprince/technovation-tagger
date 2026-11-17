"""Source adapters feed the same opportunity table and Claude tagging queue."""
import unittest
from datetime import date
from pathlib import Path
from alumni_tagger import job_import, usajobs, pathways, opportunities
from test_job_import import listing, README, NOW
from test_pathways import HTML, URL


class SourceIntegrationTests(unittest.TestCase):
    def test_pathways_queues_empty_tags_with_review_flags(self):
        item = pathways.normalize(HTML, URL, date(2026, 11, 16))
        self.assertEqual(item['row']['tags'], [])
        self.assertTrue(item['suggested_tags'])
        self.assertEqual(item['row']['status'], 'pending')
        self.assertNotIn('tags', item['row']['missing_fields'])
        self.assertIn('type_review', item['row']['missing_fields'])

    def test_all_source_fields_are_compatible_with_shared_tagger(self):
        inputs = [job_import.to_row(listing()), pathways.normalize(HTML, URL, date(2026, 11, 16))['row']]
        taxonomy = opportunities.opportunity_taxonomy(opportunities.load_taxonomy())
        for row in inputs:
            seen = []
            def ask(values):
                seen.append(values)
                return {c: [] for c in taxonomy['categories']}
            self.assertEqual(opportunities.tag_opportunity(row, ask, taxonomy), [])
            self.assertEqual(set(seen[0]), set(opportunities.SOURCE_FIELDS))

    def test_simplify_posted_date_is_retained(self):
        self.assertIn('Posted: 2027-01-14.', job_import.to_row(listing())['description'])
        rows, _, _ = job_import.plan_readme(README)
        row = next(r for r in rows if r['title'].startswith('Software Intern'))
        self.assertIn('Posted (as listed): Nov 3, 2026.', row['description'])

    def test_single_collection_schedule(self):
        root = Path(__file__).resolve().parents[1] / '.github/workflows'
        for filename in ['import-job-listings.yml', 'import-usajobs.yml']:
            self.assertNotIn('  schedule:', (root / filename).read_text())
        workflow = (root / 'collect-opportunities.yml').read_text()
        for source in ['simplify', 'pathways', 'usajobs']:
            self.assertIn(source, workflow)
        self.assertIn('collection_schedule', workflow)
