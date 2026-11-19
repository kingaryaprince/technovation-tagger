import unittest
from datetime import date

from alumni_tagger.digest import rank

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


if __name__ == '__main__':
    unittest.main()
