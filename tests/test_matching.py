import json
import re
import unittest
from datetime import date
from pathlib import Path

from alumni_tagger.digest import GENERIC_TAGS, rank

TODAY = date(2026, 11, 19)
ALUM = {'tags': {'interests': ['AI/Machine Learning', 'Data & Analytics'],
                 'skills': ['Data Analysis'], 'industry': []}}


def opp(title, source, tags, deadline=None, rolling=None, status='pending'):
    return {'title': title, 'source': source, 'tags': tags,
            'deadline': deadline, 'rolling': rolling, 'status': status}


class MatchingTest(unittest.TestCase):
    def test_no_deadline_counts_as_open_but_expired_does_not(self):
        opps = [opp('Data Intern', 'simplify', ['Data Analysis']),
                opp('Closed', 'simplify', ['Data Analysis'], status='expired'),
                opp('Past', 'usajobs', ['Data Analysis'], deadline=date(2026, 1, 1))]
        self.assertEqual([o['title'] for o in rank(ALUM, opps, TODAY)], ['Data Intern'])

    def test_higher_score_wins_and_ties_take_turns_between_sources(self):
        three = ['AI/Machine Learning', 'Data & Analytics', 'Data Analysis']
        opps = [opp(f'VISTA {i}', 'americorps', three, deadline=date(2026, 12, i + 1))
                for i in range(4)]
        opps += [opp('ML Intern', 'simplify', three), opp('DS Intern', 'simplify', three),
                 opp('One tag', 'simplify', ['Data Analysis'])]
        titles = [o['title'] for o in rank(ALUM, opps, TODAY, limit=5)]
        self.assertEqual(titles, ['VISTA 0', 'ML Intern', 'VISTA 1', 'DS Intern', 'VISTA 2'])


class SharedCasesTest(unittest.TestCase):
    """tests/matching_cases.json: the SQL function is checked against the same file."""

    def test_matches_expected(self):
        cases = json.loads((Path(__file__).parent / 'matching_cases.json').read_text())
        opps = [{**o, 'status': o.get('status', 'pending'),
                 'deadline': date.fromisoformat(o['deadline']) if o['deadline'] else None}
                for o in sorted(cases['opportunities'], key=lambda o: -o['created'])]
        for name, tags in cases['alumni'].items():
            got = [[o['title'], o['score'], o['matched_tags']]
                   for o in rank({'tags': tags}, opps, TODAY, limit=10)]
            self.assertEqual(got, cases['expected'][name], name)


class WeightTest(unittest.TestCase):
    def test_generic_tags_are_real_labels(self):
        taxonomy = json.loads((Path(__file__).parent.parent / 'data/taxonomy.json').read_text())
        labels = {t for c in ('interests', 'skills', 'industry')
                  for t in taxonomy['categories'][c]['tags']}
        self.assertEqual(GENERIC_TAGS - labels, set())

    def test_generic_list_matches_sql(self):
        sql = (Path(__file__).parent.parent / 'sql/014_better_matching.sql').read_text()
        block = sql[sql.index('array[', sql.index('GENERIC_TAGS')):sql.index('as generic')]
        self.assertEqual(set(re.findall(r"'([^']+)'", block)), set(GENERIC_TAGS))


if __name__ == '__main__':
    unittest.main()
