"""Bounded Pathways HTML collector. Preview by default; --write explicitly imports pending rows."""
import argparse
from datetime import date, datetime
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib import error, parse, request, robotparser
from zoneinfo import ZoneInfo

from .clients import SupabaseClient, ServiceError, json_request, settings
from .core import ROOT, load_taxonomy

BASE = 'https://www.pathwaystoscience.org/'
START = BASE + 'programs.aspx?submit=y&link=deadlineapproaching'
AGENT = 'TechnovationOpportunityResearch/0.1'


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.heading = [], [], []
        self.skip = 0
        self.anchor = None
        self.label = []
        self.in_heading = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if self.skip:
            return
        if tag in ('br', 'p', 'div', 'h1', 'h2', 'li'):
            self.parts.append('\n')
        if tag == 'h1':
            self.in_heading = True
        if tag == 'a':
            self.anchor = dict(attrs).get('href')
            self.label = []

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)
        if tag == 'h1':
            self.in_heading = False
        if tag == 'a' and self.anchor:
            self.links.append((self.anchor, ' '.join(self.label).strip()))
            self.anchor = None
        if tag in ('p', 'div', 'h1', 'h2', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if self.skip:
            return
        self.parts.append(data)
        if self.anchor:
            self.label.append(data.strip())
        if self.in_heading:
            self.heading.append(data.strip())

    @property
    def text(self):
        return '\n'.join(' '.join(line.split()) for line in ''.join(self.parts).splitlines() if line.strip())


def source_url(url):
    """I only fetch discovered pages on this source, never arbitrary links in descriptions."""
    p = parse.urlsplit(parse.urljoin(BASE, url))
    if p.scheme != 'https' or p.hostname != 'www.pathwaystoscience.org' or p.username or p.password or p.port:
        raise ValueError('Unexpected source URL')
    return parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ''))


class Fetcher:
    def __init__(self, cache, delay=2):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay, self.last = max(2, delay), 0
        self.robots = robotparser.RobotFileParser()
        try:
            text = self._get(BASE + 'robots.txt')
        except error.HTTPError as exc:
            if exc.code != 404:
                raise ValueError('Cannot check source robots rules') from None
            text = ''
        self.robots.parse(text.splitlines())
        self.delay = max(self.delay, self.robots.crawl_delay(AGENT) or 0)

    def _get(self, url):
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()
        # Redirects are rejected; a changed source must be inspected before following it.
        from .clients import NoRedirect
        opener = request.build_opener(NoRedirect)
        with opener.open(request.Request(url, headers={'User-Agent': AGENT}), timeout=25) as response:
            if 'html' not in response.headers.get('Content-Type', '') and not url.endswith('robots.txt'):
                raise ValueError('Unexpected non-HTML source response')
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('Source page exceeds 2 MB limit')
            return body.decode('utf-8', 'replace')

    def __call__(self, url):
        url = source_url(url)
        if not self.robots.can_fetch(AGENT, url):
            raise ValueError('Source robots rules disallow this page')
        path = self.cache / (hashlib.sha256(url.encode()).hexdigest() + '.html')
        if path.exists() and time.time() - path.stat().st_mtime < 86400:
            return path.read_text(encoding='utf-8')
        text = self._get(url)
        path.write_text(text, encoding='utf-8')
        return text


def discover(html, url):
    links, next_page = [], None
    for href, label in Page(html).links:
        target = source_url(parse.urljoin(url, href)) if href.startswith(('programhub.aspx', 'programs.aspx', BASE)) else None
        if not target:
            continue
        parts = parse.urlsplit(target)
        query = parse.parse_qs(parts.query)
        if parts.path == '/programhub.aspx' and query.get('sort') and 'subsort' not in query:
            if target not in links:
                links.append(target)
        if 'next page' in label.lower() and parts.path == '/programs.aspx':
            next_page = target
    return links, next_page


def section(text, label):
    labels = ['Academic Level:', 'Description:', 'Application Deadline:', 'Application Deadline(s):',
              'Program Dates:', 'Participating Institution(s):', 'Program Materials:',
              'This Program can be Described by:', 'Academic Disciplines:', 'Keywords:']
    start = text.find(label)
    if start < 0:
        return ''
    start += len(label)
    ends = [text.find(x, start) for x in labels if text.find(x, start) >= 0]
    return text[start:min(ends) if ends else len(text)].strip()


def parse_date(value):
    for fmt in ('%m/%d/%Y', '%Y-%m-%d', '%B %d, %Y', '%b %d, %Y'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    return None


def auto_tags(text):
    """Free baseline: explicit topic phrases, filtered through the shared taxonomy."""
    rules = {'AI/Machine Learning': r'\b(machine learning|artificial intelligence)\b',
             'Software Engineering': r'\b(software engineering|software development)\b',
             'Python': r'\bpython\b', 'Data Analysis': r'\b(data analysis|statistical analysis)\b',
             'Healthcare': r'\b(public health|healthcare|clinical research)\b',
             'Research Methods': r'\b(research experience|research methods|research training)\b',
             'Academia/Research': r'\b(research|postdoctoral)\b',
             'Mentorship': r'\b(mentoring|mentorship)\b',
             'Environment/Sustainability': r'\b(sustainability|environmental science|ecology)\b',
             'Hardware/Robotics': r'\b(robotics|robotic systems)\b'}
    allowed = {tag for cat in load_taxonomy()['categories'].values() for tag in cat['tags']}
    evidence = {tag: m.group() for tag, pattern in rules.items()
                if tag in allowed and (m := re.search(pattern, text, re.I))}
    return sorted(evidence), evidence


def normalize(html, url, today):
    """I keep uncertain fields for review and never invent rolling deadlines or remote work."""
    page = Page(html)
    title = ' '.join(page.heading).strip()
    description = section(page.text, 'Description:')
    if not title or not description or len(description) > 12000:
        raise ValueError('Missing or oversized program title/description')
    organizations = [label for href, label in page.links
                     if parse.urlsplit(href).path.lower().endswith('institution.aspx') and label]
    organization = organizations[0] if organizations else None
    application = next((parse.urljoin(url, href) for href, label in page.links
                        if label.lower() in ('program website', 'learn more and apply!')
                        and parse.urlsplit(parse.urljoin(url, href)).scheme in ('http', 'https')), None)
    deadline_text = section(page.text, 'Application Deadline:') or section(page.text, 'Application Deadline(s):')
    deadline = parse_date(deadline_text)
    dates = re.findall(r'\d{1,2}/\d{1,2}/\d{4}', section(page.text, 'Program Dates:'))
    end = parse_date(dates[-1]) if dates else None
    expired = bool((deadline and deadline < today) or (end and end < today))
    text = title + '\n' + description
    tags, evidence = auto_tags(text)
    paid = bool(re.search(r'\b(stipend|paid internship|paid research|salary)\b', text, re.I))
    work = bool(re.search(r'\b(internship|postdoctoral|research fellowship|paid research)\b', text, re.I))
    degree = bool(re.search(r'\b(master[’\']?s degree|graduate degree|tuition|scholarship)\b', title, re.I))
    kind = 'job' if paid and work and not degree else None
    remote = True if re.search(r'\b(fully remote|entirely remote|fully virtual)\b', text, re.I) else None
    missing = [key for key, absent in [('type', kind is None), ('organization', organization is None),
               ('deadline', deadline is None), ('location', True), ('remote', remote is None)] if absent]
    if kind:
        missing.append('type_review')  # Paid programs still need a staff classification check.
    query = parse.parse_qs(parse.urlsplit(url).query)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', query.get('sort', [''])[0]):
        raise ValueError('Unexpected source identifier')
    row = dict(title=title, type=kind, organization=organization,
               description=description, url=application or url, location=None, remote=remote,
               deadline=deadline.isoformat() if deadline else None, rolling=False if deadline else None, tags=[],
               source='pathways_to_science', source_id=query['sort'][0],
               status='expired' if expired else 'pending', missing_fields=missing,
               taxonomy_version=load_taxonomy()['version'])
    return {'row': row, 'suggested_tags': tags, 'source_url': url, 'program_end': end.isoformat() if end else None,
            'tagging_method': 'explicit-keyword-baseline-v1', 'tag_evidence': evidence,
            'review': ['Verify current intake and eligibility at the original program website.']}


def collect(fetch, start, limit, pages, today):
    results, failures, seen_pages, seen_details = [], [], set(), set()
    current = start
    while current and len(seen_pages) < pages and len(results) + len(failures) < limit:
        if current in seen_pages:
            break
        seen_pages.add(current)
        links, following = discover(fetch(current), current)
        if not links:
            raise ValueError('No program links found; source markup may have changed')
        for url in links:
            if url in seen_details:
                continue
            seen_details.add(url)
            try:
                results.append(normalize(fetch(url), url, today))
            except (ValueError, error.URLError, TimeoutError) as exc:
                failures.append({'source_url': url, 'error': type(exc).__name__})
            if len(results) + len(failures) >= limit:
                break
        current = following
    return {'candidates': results, 'failures': failures, 'results_pages': len(seen_pages)}


def import_candidates(candidates, db):
    counts = dict(inserted=0, duplicates=0, skipped=0, expired=0)
    for candidate in candidates:
        row = candidate['row']
        if row['status'] == 'expired':
            # Only expire this source's own row, never a manual listing with the same URL.
            query = 'opportunities?select=id,status&source=eq.pathways_to_science&source_id=eq.' + parse.quote(row['source_id'], safe='')
            for old in db.select(query) or []:
                if old['status'] != 'expired':
                    json_request(db.url + '/rest/v1/opportunities?id=eq.' + parse.quote(old['id'])
                                 + '&source=eq.pathways_to_science', db.headers,
                                 {'status': 'expired'}, method='PATCH', service='Supabase')
                    counts['expired'] += 1
            counts['skipped'] += 1
            continue
        if row['type'] is None:
            counts['skipped'] += 1
            continue
        # No upsert: replays must never reset a staff decision or overwrite a manual listing.
        try:
            json_request(db.url + '/rest/v1/opportunities', {**db.headers, 'Prefer': 'return=minimal'},
                         row, service='Supabase')
            counts['inserted'] += 1
        except ServiceError as exc:
            if exc.status != 409:
                raise
            # Verify the collision belongs to the same listing, rather than swallow every conflict.
            query = 'opportunities?select=id&or=' + parse.quote(
                f'(url.eq."{row["url"].replace(chr(34), "")}",and(source.eq.pathways_to_science,source_id.eq."{row["source_id"]}"))', safe='')
            if not db.select(query):
                raise
            counts['duplicates'] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, default=3)
    parser.add_argument('--pages', type=int, default=2)
    parser.add_argument('--start-url', default=START)
    parser.add_argument('--out', type=Path, default=ROOT / 'output/pathways-preview.json')
    parser.add_argument('--dry-run', action='store_true', help='Explicit preview; cannot combine with --write')
    parser.add_argument('--write', action='store_true', help='Import reviewable, non-expired candidates as pending')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 30 or not 1 <= args.pages <= 5:
        parser.error('Use limit 1..30 and pages 1..5 for a bounded run')
    if args.write and args.dry_run:
        parser.error('Choose --write or --dry-run')
    today = datetime.now(ZoneInfo('America/Los_Angeles')).date()
    report = collect(Fetcher(ROOT / 'output/pathways-cache'), source_url(args.start_url), args.limit, args.pages, today)
    report['as_of'] = today.isoformat()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    if args.write:
        report['import'] = import_candidates(report['candidates'], SupabaseClient(settings()))
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'candidates': len(report['candidates']), 'failures': len(report['failures']),
                      'expired': sum(x['row']['status']=='expired' for x in report['candidates']),
                      'output': str(args.out), 'import': report.get('import')}))
    if report['failures']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
