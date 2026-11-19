"""Full job-posting text from an employer's hiring site, for opportunities.posting_text.

Job lists like Simplify only give a title, company, location and category, which leaves the
tagger almost nothing to quote. These hiring sites publish each posting as public JSON, so the
importer saves the posting's text and the tagger can quote real requirements from it:

  Workday          {tenant}.wd*.myworkdayjobs.com/{site}/job/...   -> /wday/cxs/... API
  Greenhouse       (job-)boards.greenhouse.io/{board}/jobs/{id}     -> boards-api.greenhouse.io
  Ashby            jobs.ashbyhq.com/{board}/{id}                    -> posting-api job board
  Lever            jobs.lever.co/{company}/{id}                     -> api.lever.co postings
  SmartRecruiters  jobs.smartrecruiters.com/{company}/{id}          -> api.smartrecruiters.com

fetch_posting(url) returns the text, '' when the site says the job is gone (404/410) or the
posting has no text, or None when the link isn't on one of these sites or the site couldn't be
reached (so it's tried again on a later run).
"""
import html
import re
import time
from html.parser import HTMLParser
from urllib import parse

from .clients import ServiceError, json_request

# The tagger refuses any field over 12000 characters (core.select_inputs).
MAX_CHARS = 10000
HEADERS = {'User-Agent': 'job-list-import', 'Accept': 'application/json'}
GONE = {404, 410}
# Seconds between requests to one site. Workday answers 429 (too many requests) to more than
# about one request a second, across all employers, so it gets the widest gap.
GAP = {'workday': 1.5}
DEFAULT_GAP = 0.2
BACKOFF = (5, 15)                      # waits after each 429 before giving up on that job
# After this many jobs in a row on one site end in 429, stop asking that site for the rest of
# the run (its rows are tried again next run) instead of waiting on every job.
GIVE_UP_AFTER = 3


# ---------- HTML to text ----------

BLOCK_TAGS = {'p', 'div', 'br', 'li', 'ul', 'ol', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr',
              'section', 'article', 'header', 'footer', 'table', 'blockquote', 'hr'}
SKIP_TAGS = {'script', 'style', 'noscript'}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == 'li':
            self.parts.append('\n- ')
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(markup):
    """Readable plain text: one line per paragraph or list item ('- ' for items)."""
    parser = _Text()
    parser.feed(markup or '')
    parser.close()
    return tidy(''.join(parser.parts))


def tidy(text):
    lines = (' '.join(line.replace('\xa0', ' ').split()) for line in (text or '').splitlines())
    lines = [line for line in lines if line and line != '-']
    text = '\n'.join(lines)
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind('\n', 0, MAX_CHARS)
    return text[:cut if cut > MAX_CHARS // 2 else MAX_CHARS].rstrip()


# ---------- one function per hiring site: link -> (API url, read text from the JSON) ----------

def _parts(url):
    parts = parse.urlsplit(url)
    return (parts.hostname or '').lower(), [p for p in parts.path.split('/') if p], parts


def workday(host, path, parts):
    # {tenant}.wd5.myworkdayjobs.com/{site}/job/{location}/{slug}; some links add a locale first.
    if not re.fullmatch(r'[\w-]+\.wd\d+\.myworkdayjobs\.com', host) or 'job' not in path:
        return None
    i = path.index('job')
    if i == 0 or i + 1 >= len(path):
        return None
    site = path[i - 1]
    api = (f'https://{host}/wday/cxs/{host.split(".")[0]}/{site}/'
           + '/'.join(path[i:]))
    return api, lambda d: html_text((d.get('jobPostingInfo') or {}).get('jobDescription'))


def greenhouse(host, path, parts):
    if host not in ('boards.greenhouse.io', 'job-boards.greenhouse.io') or 'jobs' not in path:
        return None
    i = path.index('jobs')
    if i == 0 or i + 1 >= len(path) or not path[i + 1].isdigit():
        return None
    api = f'https://boards-api.greenhouse.io/v1/boards/{path[i - 1]}/jobs/{path[i + 1]}'
    # Greenhouse returns the posting as HTML that is itself HTML-escaped.
    return api, lambda d: html_text(html.unescape(d.get('content') or ''))


def ashby(host, path, parts):
    if host != 'jobs.ashbyhq.com' or len(path) < 2:
        return None
    board, job_id = path[0], path[1]
    # Ashby only serves whole boards; the caller caches each board for the run.
    api = f'https://api.ashbyhq.com/posting-api/job-board/{board}'

    def read(d):
        for job in d.get('jobs') or []:
            if job.get('id') == job_id:
                return tidy(job.get('descriptionPlain') or html_text(job.get('descriptionHtml')))
        return ''                                 # not on the board any more
    return api, read


def lever(host, path, parts):
    if host != 'jobs.lever.co' or len(path) < 2:
        return None
    api = f'https://api.lever.co/v0/postings/{path[0]}/{path[1]}'

    def read(d):
        sections = [d.get('descriptionPlain') or html_text(d.get('description'))]
        for item in d.get('lists') or []:
            sections.append((item.get('text') or '') + '\n' + html_text(item.get('content')))
        sections.append(d.get('additionalPlain') or html_text(d.get('additional')))
        return tidy('\n'.join(filter(None, sections)))
    return api, read


def smartrecruiters(host, path, parts):
    if host != 'jobs.smartrecruiters.com' or len(path) < 2:
        return None
    job_id = path[1].split('-')[0]
    if not job_id.isdigit():
        return None
    api = f'https://api.smartrecruiters.com/v1/companies/{path[0]}/postings/{job_id}'

    def read(d):
        sections = ((d.get('jobAd') or {}).get('sections') or {})
        return tidy('\n'.join(
            filter(None, (((sections.get(k) or {}).get('title') or '') + '\n'
                          + html_text((sections.get(k) or {}).get('text'))
                          for k in ('jobDescription', 'qualifications', 'additionalInformation',
                                    'companyDescription')))))
    return api, read


SITES = (workday, greenhouse, ashby, lever, smartrecruiters)


def posting_api(url):
    """(site name, API url, read function) for a link on a supported hiring site, else None."""
    try:
        host, path, parts = _parts(url)
    except ValueError:
        return None
    if parts.scheme != 'https':
        return None
    for site in SITES:
        found = site(host, path, parts)
        if found:
            return (site.__name__, *found)
    return None


def supported(url):
    return posting_api(url) is not None


def site_of(url):
    found = posting_api(url)
    return found[0] if found else None


class PoliteGetter:
    """GET JSON with a gap between requests to the same site, waiting and retrying on 429.
    A site that keeps answering 429 is skipped for the rest of the run. Each site is meant to
    be used from one thread at a time (job_import runs one thread per site)."""

    def __init__(self, sleep=time.sleep, clock=time.monotonic):
        self.sleep, self.clock, self.last = sleep, clock, {}
        self.refusals, self.gave_up = {}, set()

    def __call__(self, api, site):
        if site in self.gave_up:
            raise ServiceError('Hiring site', 429)
        for wait in (*BACKOFF, None):
            gap = GAP.get(site, DEFAULT_GAP) - (self.clock() - self.last.get(site, -1e9))
            if gap > 0:
                self.sleep(gap)
            self.last[site] = self.clock()
            try:
                data = json_request(api, HEADERS, method='GET', service='Hiring site')
                self.refusals[site] = 0
                return data
            except ServiceError as exc:
                if exc.status != 429:
                    raise
                if wait is None:
                    self.refusals[site] = self.refusals.get(site, 0) + 1
                    if self.refusals[site] >= GIVE_UP_AFTER:
                        self.gave_up.add(site)
                        print(f'  {site}: still refusing (too many requests); '
                              'its rows are tried again next run', flush=True)
                    raise
                self.sleep(wait)


def fetch_posting(url, get=None, cache=None):
    """Posting text; '' if the job is gone or has no text; None if unsupported or unreachable.
    `get` is a PoliteGetter shared across calls (one is made if not given); `cache` (a dict)
    shares one download between jobs on the same Ashby board."""
    found = posting_api(url)
    if not found:
        return None
    site, api, read = found
    get = get or PoliteGetter()
    if cache is not None and api in cache:
        data = cache[api]
    else:
        try:
            data = get(api, site) or {}
        except ServiceError as exc:
            data = {} if exc.status in GONE else None
        if cache is not None and data is not None:
            cache[api] = data
    if data is None:
        return None
    try:
        return read(data) or ''
    except (AttributeError, TypeError):
        return ''                                 # unexpected shape: treat as no text
