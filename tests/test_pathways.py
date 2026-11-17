import unittest
from unittest.mock import Mock, patch
from datetime import date
from alumni_tagger.pathways import normalize, discover, auto_tags, collect, source_url, import_candidates

HTML='''<html><h1>Summer Public Health Research</h1><b>Academic Level:</b><br>Undergraduates<br>
<b>Description:</b><br>Paid research with a stipend, clinical research and mentoring.<br>
<b>Application Deadline:</b>January 15, 2027<br><b>Program Dates:</b>6/1/2027 - 8/1/2027<br>
<b>Participating Institution(s):</b><a href="Institution.aspx?sort=Institution&amp;subsort=Example">Example University</a><br>
<b>Program Materials:</b><a href="https://example.org/apply">Program Website</a></html>'''
URL='https://www.pathwaystoscience.org/programhub.aspx?sort=SUM-Example'

class OpportunityTests(unittest.TestCase):
 def test_future(self):
  c=normalize(HTML,URL,date(2026,11,12));r=c['row']
  self.assertEqual(r['status'],'pending');self.assertEqual(r['deadline'],'2027-01-15')
  self.assertEqual(r['organization'],'Example University');self.assertEqual(r['type'],'job')
  self.assertIsNone(r['remote']);self.assertFalse(r['rolling'])
  self.assertEqual(r['url'],'https://example.org/apply');self.assertIn('type_review',r['missing_fields'])
  self.assertEqual(r['tags'],[]);self.assertIn('Healthcare',c['suggested_tags']);self.assertIn('Mentorship',c['suggested_tags'])
 def test_degree_not_job(self):
  h=HTML.replace('Summer Public Health Research','STEM Degree Scholarship').replace('Paid research with a stipend','Paid research internship with a stipend')
  self.assertIsNone(normalize(h,URL,date(2026,11,12))['row']['type'])
 def test_expired(self):
  self.assertEqual(normalize(HTML,URL,date(2028,1,1))['row']['status'],'expired')
 def test_unknown_not_rolling(self):
  r=normalize(HTML.replace('January 15, 2027','Not announced'),URL,date(2026,11,12))['row']
  self.assertIsNone(r['deadline']);self.assertIsNone(r['rolling']);self.assertIn('deadline',r['missing_fields'])
 def test_no_guessed_type(self):
  r=normalize(HTML.replace('Paid research with a stipend','Training'),URL,date(2026,11,12))['row']
  self.assertIsNone(r['type']);self.assertEqual(import_candidates([{'row':r}],None)['skipped'],1)
 def test_expired_not_imported(self):
  r=normalize(HTML,URL,date(2028,1,1));db=Mock();db.select.return_value=[];self.assertEqual(import_candidates([r],db)['skipped'],1)
 def test_expired_updates_only_source_owned_row(self):
  r=normalize(HTML,URL,date(2028,1,1));db=Mock();db.select.return_value=[{'id':'r1','status':'pending'}]
  db.url='https://example.supabase.co';db.headers={}
  with patch('alumni_tagger.pathways.json_request') as send:
   self.assertEqual(import_candidates([r],db)['expired'],1)
  self.assertIn('source=eq.pathways_to_science',send.call_args.args[0])
  self.assertEqual(send.call_args.args[2],{'status':'expired'})
 def test_source_guard(self):
  for u in ['https://evil.example/programhub.aspx','http://www.pathwaystoscience.org/a','https://user:pass@www.pathwaystoscience.org/a']:
   with self.assertRaises(ValueError):source_url(u)
 def test_discovery_dedup(self):
  h='<a href="programhub.aspx?sort=one">A</a><a href="programhub.aspx?sort=one">more</a><a href="programhub.aspx?sort=one&amp;subsort=contacts">Contacts</a><a href="programs.aspx?pos=2">Next page of search results</a>'
  links,nxt=discover(h,'https://www.pathwaystoscience.org/programs.aspx');self.assertEqual(len(links),1);self.assertTrue(nxt.endswith('pos=2'))
 def test_bad_markup(self):
  with self.assertRaises(ValueError):normalize('<h1>Loading</h1>',URL,date.today())
 def test_bounded(self):
  h='<a href="programhub.aspx?sort=one">A</a><a href="programhub.aspx?sort=two">B</a>'
  calls=[]
  def fetch(u):calls.append(u);return HTML if 'programhub' in u else h
  r=collect(fetch,'https://www.pathwaystoscience.org/programs.aspx',1,2,date(2026,11,12))
  self.assertEqual(len(r['candidates']),1);self.assertEqual(len(calls),2)
 def test_invalid_date(self):
  r=normalize(HTML.replace('January 15, 2027','February 30, 2027'),URL,date(2026,11,12))['row'];self.assertIsNone(r['deadline'])

if __name__=='__main__':unittest.main()
