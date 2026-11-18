"""Import job listings from a GitHub job-list repo into the Supabase opportunities table.

Run:  python3 -m alumni_tagger.job_import --dry-run            (preview, no writes)
      python3 -m alumni_tagger.job_import --limit 50           (add at most 50 new listings)
      python3 -m alumni_tagger.job_import                      (add every open listing)
      python3 -m alumni_tagger.job_import --repo vanshb03/Summer2027-Internships
      python3 -m alumni_tagger.job_import --repo negarprh/Canadian-Tech-Internships-2027
Options: --status active (new rows go live without review), --max-age-days 60,
         --term "Summer 2028", --format auto|listings|readme.
Default repo: SimplifyJobs/Summer2027-Internships.

Two repo formats are read; --format auto (the default) picks listings when the repo has it:
  listings  .github/scripts/listings.json, the format SimplifyJobs uses and others copy
            (e.g. vanshb03). Has ids, open/closed flags, terms and posting dates.
  readme    a Markdown table in README.md with Company, Role, Location and an apply link
            (e.g. negarprh, zshah101, most hand-kept lists). A row marked 🔒 is closed;
            "↳" repeats the company above. HTML tables and other layouts aren't read.

Links: every saved url is the employer's own posting, with tracking parameters removed.
A link through a redirect service (go.vansh.nyc, bit.ly, ...) is followed to the employer's
page when the listing is first added. A link that only leads to a list's own site
(simplify.jobs, zapply.jobs), or that can't be resolved, is skipped.

From the repo name: term ("Summer2027" -> "Summer 2027"; filters the listings format only,
and a name without a season and year keeps every listing), type (internship if the name
contains "Intern", else job) and source (simplify_summer2027 for SimplifyJobs, otherwise
<owner>_<term or repo name>), so each repo's rows are tracked separately.

date_posted is the day the list says the listing was posted (listings format only; README
lists have no reliable dates, so it stays null). Needs sql/010_date_posted.sql.

Rows are matched on (source, source_id). New listings are inserted newest first with
--status (default pending). Existing rows keep their status and tags; only their listing
text is refreshed. Listings the repo marks closed, or that drop off a README, are expired.
"""
import argparse
import hashlib
import re
import socket
import time
import unicodedata
from dataclasses import dataclass
from urllib import error, parse, request

from .clients import NoRedirect, ServiceError, SupabaseClient, json_request, settings

DEFAULT_REPO = 'SimplifyJobs/Summer2027-Internships'
RAW_URL = 'https://raw.githubusercontent.com/{repo}/HEAD/{path}'   # HEAD = default branch
LISTINGS_PATH = '.github/scripts/listings.json'
FORMATS = ('auto', 'listings', 'readme')
STATUSES = ('pending', 'active')
EXPIRED = 'expired'
# Sites that only show a list's own page, not the employer's posting. Links there are skipped.
BLOCKED_HOSTS = ('simplify.jobs', 'zapply.jobs')
# Link shorteners that redirect to the employer's posting. Followed when a listing is added.
REDIRECT_HOSTS = ('go.vansh.nyc', 'redirect.cvrve.me', 'bit.ly', 'tinyurl.com', 'lnkd.in',
                  'rebrand.ly', 't.co', 'ow.ly', 'shorturl.at')
# Query parameters a list adds to employer links. Everything else is kept, because
# some job boards need their own parameters (e.g. Greenhouse's gh_jid) to open the posting.
TRACKING_PARAMS = {'ref', 'source', 'src'}
TRACKING_VALUES = {'simplify'}
# Listing columns the importer owns; refreshed on existing rows when the list changes them.
# date_posted needs sql/010_date_posted.sql.
CONTENT_FIELDS = ['type', 'title', 'organization', 'location', 'remote', 'description', 'url',
                  'date_posted']
BATCH_SIZE = 500
PAGE_SIZE = 1000


@dataclass(frozen=True)
class Feed:
    repo: str            # owner/name on GitHub
    term: str | None     # e.g. 'Summer 2027'; None keeps every listing
    source: str          # opportunities.source for this repo's rows
    type: str            # opportunities.type; 'internship' needs sql/009_internship_type.sql
    format: str = 'auto'

    @property
    def label(self):
        return ' '.join(filter(None, [self.term, 'internships' if self.type == 'internship' else 'jobs']))


def slug(text):
    return re.sub(r'[^a-z0-9]', '', text.lower())


def feed_for(repo=DEFAULT_REPO, term=None, fmt='auto'):
    """Work out term, source and type from a repo name like SimplifyJobs/Summer2027-Internships."""
    if not re.fullmatch(r'[\w-][\w.-]*/[\w-][\w.-]*', repo or ''):
        raise ValueError(f'--repo must look like owner/name, got {repo!r}')
    if fmt not in FORMATS:
        raise ValueError(f'--format must be one of {", ".join(FORMATS)}')
    owner, name = repo.split('/', 1)
    if term is None:
        match = re.search(r'(Summer|Fall|Winter|Spring)[-_ ]?(\d{4})', name, re.IGNORECASE)
        term = f'{match.group(1).title()} {match.group(2)}' if match else None
    term = term or None
    prefix = 'simplify' if owner.lower() == 'simplifyjobs' else slug(owner)
    source = prefix + '_' + slug(term or name)
    return Feed(repo, term, source, 'internship' if 'intern' in name.lower() else 'job', fmt)


DEFAULT_FEED = feed_for()


# ---------- links ----------

def on_host(host, hosts):
    return any(host == h or host.endswith('.' + h) for h in hosts)


def clean_url(url):
    """The link without tracking parameters, or None if it's unusable or only leads to a list's site."""
    parts = parse.urlsplit((url or '').strip())
    host = (parts.hostname or '').lower()
    if parts.scheme not in ('http', 'https') or not host or on_host(host, BLOCKED_HOSTS):
        return None
    query = [(k, v) for k, v in parse.parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_')
             and not (k.lower() in TRACKING_PARAMS and v.lower() in TRACKING_VALUES)]
    return parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                             parse.urlencode(query), parts.fragment))


def needs_resolving(url):
    return on_host((parse.urlsplit(url).hostname or '').lower(), REDIRECT_HOSTS)


def resolve_link(url, hops=3):
    """Follow a shortener to the employer's page, or None if it doesn't lead to one."""
    opener = request.build_opener(NoRedirect)
    for _ in range(hops):
        if not needs_resolving(url):
            return clean_url(url)
        req = request.Request(url, method='HEAD', headers={'User-Agent': 'job-list-import'})
        try:
            with opener.open(req, timeout=15):
                return None                       # a shortener page that didn't redirect
        except error.HTTPError as exc:
            location = exc.headers.get('Location')
            exc.close()
            if exc.code not in (301, 302, 303, 307, 308) or not location:
                return None
            url = parse.urljoin(url, location)
        except (error.URLError, TimeoutError, socket.timeout, ValueError):
            return None
    return None


# ---------- listings.json format ----------

def wanted(listing, max_age_days=None, now=None, feed=DEFAULT_FEED):
    if not listing.get('is_visible', True):
        return False
    if feed.term:
        if 'terms' in listing:
            if feed.term not in (listing.get('terms') or []):
                return False
        elif listing.get('season') and listing['season'] != feed.term.split()[0]:
            return False                          # e.g. vanshb03: season 'Summer', no year
    if max_age_days is not None:
        age = (now or time.time()) - (listing.get('date_posted') or 0)
        if age > max_age_days * 86400:
            return False
    return True


def describe(listing):
    """Plain-text description built only from listing facts, for people and the tagger."""
    locations = '; '.join(listing.get('locations') or []) or 'Not listed'
    lines = [f"{listing['title'].strip()} at {listing['company_name'].strip()}."]
    if listing.get('terms'):
        lines.append(f'Term: {", ".join(listing["terms"])}.')
    lines.append(f'Location: {locations}.')
    if listing.get('category'):
        lines.append(f"Category: {listing['category']}.")
    if listing.get('degrees'):
        lines.append(f'Degrees: {", ".join(listing["degrees"])}.')
    if listing.get('sponsorship') and listing['sponsorship'] != 'Other':
        lines.append(f"Sponsorship: {listing['sponsorship']}.")
    if posted_day(listing.get('date_posted')):
        lines.append(f"Posted: {posted_day(listing['date_posted'])}.")
    return '\n'.join(lines)


def posted_day(timestamp):
    """'YYYY-MM-DD' (UTC) for a listing's date_posted Unix time, or None if it has none."""
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        return None
    return time.strftime('%Y-%m-%d', time.gmtime(timestamp))


def make_row(feed, source_id, title, company, locations, description, url, date_posted=None):
    return {
        'source': feed.source,
        'source_id': source_id,
        'type': feed.type,
        'title': title,
        'organization': company,
        'location': '; '.join(locations) or None,
        'remote': any('remote' in loc.lower() for loc in locations),
        'description': description,
        'url': url,
        'date_posted': date_posted,
    }


def to_row(listing, feed=DEFAULT_FEED):
    """One opportunities row from a listings.json entry, or None without a usable link."""
    url = clean_url(listing.get('url'))
    if not url or not (listing.get('title') or '').strip() or not listing.get('id'):
        return None
    return make_row(feed, str(listing['id']), listing['title'].strip(),
                    (listing.get('company_name') or '').strip(), listing.get('locations') or [],
                    describe(listing), url, posted_day(listing.get('date_posted')))


def plan(listings, max_age_days=None, now=None, feed=DEFAULT_FEED):
    """Rows for open listings (newest first), ids of closed ones, and a count of skipped ones."""
    rows, closed, skipped = {}, set(), 0
    for listing in sorted(listings, key=lambda x: x.get('date_posted') or 0, reverse=True):
        if not wanted(listing, max_age_days, now, feed):
            continue
        if not listing.get('active'):
            if listing.get('id'):
                closed.add(str(listing['id']))
            continue
        row = to_row(listing, feed)
        if row is None:
            skipped += 1
            continue
        rows.setdefault(row['source_id'], row)   # newest copy wins if a list repeats an id
    return list(rows.values()), sorted(closed - rows.keys()), skipped


# ---------- README table format ----------

CELL_SPLIT = re.compile(r'(?<!\\)\|')
IMAGE = re.compile(r'!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>', re.IGNORECASE)
LINK = re.compile(r'\]\((https?://[^)\s]+)\)|href="(https?://[^"]+)"', re.IGNORECASE)
CLOSED_MARK = '🔒'
SAME_COMPANY = '↳'


def cell_text(cell):
    """Readable text of a Markdown/HTML table cell: link text kept, markup and emoji dropped."""
    text = IMAGE.sub('', cell)
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)     # [text](url) -> text
    text = re.sub(r'<[^>]+>', '', text).replace('&amp;', '&').replace('&nbsp;', ' ')
    text = text.replace('**', '').replace('__', '').replace('\\|', '|')
    text = ''.join(ch for ch in text if ch == SAME_COMPANY
                   or unicodedata.category(ch) not in ('So', 'Sk', 'Cf', 'Cn', 'Co'))
    return ' '.join(text.split())


def cell_link(cell):
    for match in LINK.finditer(IMAGE.sub('', cell)):
        return match.group(1) or match.group(2)
    return None


def split_row(line):
    """Cells of one Markdown table row; a '|' escaped as '\\|' stays inside its cell."""
    cells = [c.strip() for c in CELL_SPLIT.split(line.strip())]
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def column(headers, *names):
    for i, header in enumerate(headers):
        if any(n in header for n in names):
            return i
    return None


def readme_tables(text):
    """(headers, rows) for each Markdown table that has Company and Role columns."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) - 1:
        if lines[i].lstrip().startswith('|') and re.fullmatch(r'\s*\|?[\s:|-]+\|?\s*', lines[i + 1]) \
                and '-' in lines[i + 1]:
            headers = [cell_text(h).lower() for h in split_row(lines[i])]
            rows = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append(split_row(lines[i]))
                i += 1
            if column(headers, 'company') is not None and \
                    column(headers, 'role', 'title', 'position') is not None:
                yield headers, rows
        else:
            i += 1


def plan_readme(text, feed=DEFAULT_FEED):
    """Rows for open README listings (in README order) and counts of skipped and closed ones."""
    rows, skipped, closed, tables = {}, 0, 0, 0
    for headers, table in readme_tables(text):
        tables += 1
        c_company, c_role = column(headers, 'company'), column(headers, 'role', 'title', 'position')
        c_location = column(headers, 'location')
        c_link = column(headers, 'apply', 'application', 'link')
        company = ''
        for cells in table:
            def get(i):
                return cells[i] if i is not None and i < len(cells) else ''
            name = cell_text(get(c_company))
            company = company if name in ('', SAME_COMPANY) else name
            if CLOSED_MARK in ' '.join(cells):
                closed += 1
                continue
            title = cell_text(get(c_role))
            link = cell_link(get(c_link)) if c_link is not None else None
            link = link or next(filter(None, map(cell_link, cells)), None)
            url = clean_url(link)
            if not (company and title and url):
                skipped += 1
                continue
            locations = [p.strip() for p in re.split(r'<\s*/?\s*br\s*/?\s*>|;', get(c_location), flags=re.IGNORECASE) if p.strip()]
            locations = [cell_text(p) for p in locations if cell_text(p)]
            description = '\n'.join([f'{title} at {company}.',
                                     f'Location: {"; ".join(locations) or "Not listed"}.',
                                     f'Listed in: github.com/{feed.repo}.'])
            source_id = hashlib.sha1(url.encode()).hexdigest()[:20]
            rows.setdefault(source_id, make_row(feed, source_id, title, company, locations,
                                                description, url))
    if not tables:
        raise ValueError(f'No job table (with Company and Role columns) found in {feed.repo} README')
    return list(rows.values()), skipped, closed


# ---------- fetching ----------

def fetch_text(url):
    req = request.Request(url, headers={'User-Agent': 'job-list-import'})
    try:
        with request.build_opener(NoRedirect).open(req, timeout=60) as response:
            return response.read().decode('utf-8')
    except error.HTTPError as exc:
        exc.close()
        raise ServiceError('GitHub', exc.code) from None
    except (error.URLError, TimeoutError, socket.timeout, UnicodeError):
        raise ServiceError('GitHub') from None


def load(feed, max_age_days=None):
    """(format used, open rows, closed ids or None, skipped count, total listings)."""
    if feed.format in ('auto', 'listings'):
        try:
            listings = json_request(RAW_URL.format(repo=feed.repo, path=LISTINGS_PATH),
                                    {'Accept': 'application/json'}, method='GET',
                                    service='GitHub', retries=2)
        except ServiceError as exc:
            if not (feed.format == 'auto' and exc.status == 404):
                raise
        else:
            if not isinstance(listings, list):
                raise ServiceError('GitHub', 'unexpected listings format')
            rows, closed, skipped = plan(listings, max_age_days, feed=feed)
            return 'listings', rows, closed, skipped, len(listings)
    rows, skipped, closed = plan_readme(fetch_text(RAW_URL.format(repo=feed.repo, path='README.md')), feed)
    # README lists have no ids for closed rows: anything imported before that is no longer
    # open in the README gets expired (closed=None tells changes() to work that out).
    return 'readme', rows, None, skipped, len(rows) + skipped + closed


# ---------- database ----------

def existing_rows(db, feed=DEFAULT_FEED):
    """Rows this importer saved before for this repo, keyed by source_id."""
    found, offset = {}, 0
    columns = ','.join(['source_id', 'status'] + CONTENT_FIELDS)
    while True:
        page = db.select(f'opportunities?select={columns}&source=eq.{feed.source}'
                         f'&order=source_id&limit={PAGE_SIZE}&offset={offset}') or []
        found.update((r['source_id'], r) for r in page)
        if len(page) < PAGE_SIZE:
            return found
        offset += PAGE_SIZE


def changes(rows, closed, existing, limit=None, resolve=resolve_link):
    """New rows to insert (links resolved), existing rows whose text changed, ids to expire,
    and how many new listings were dropped because their link didn't lead to the employer."""
    for row in rows:
        old = existing.get(row['source_id'])
        if old and needs_resolving(row['url']):
            row['url'] = old['url']               # resolved when it was first imported
    new, unresolved = [], 0
    for row in rows:
        if row['source_id'] in existing:
            continue
        if limit is not None and len(new) >= limit:
            break
        if needs_resolving(row['url']):
            final = resolve(row['url'])
            if not final:
                unresolved += 1
                continue
            row['url'] = final
        new.append(row)
    changed = [r for r in rows if r['source_id'] in existing
               and any(existing[r['source_id']].get(f) != r[f] for f in CONTENT_FIELDS)]
    if closed is None:
        open_ids = {r['source_id'] for r in rows}
        closed = [i for i in existing if i not in open_ids]
    expire = sorted(i for i in closed if i in existing and existing[i].get('status') != EXPIRED)
    return new, changed, expire, unresolved


def row_filter(source_ids, feed=DEFAULT_FEED):
    ids = ','.join('"' + i.replace('"', '') + '"' for i in source_ids)
    return 'source=eq.' + feed.source + '&source_id=in.(' + parse.quote(ids, safe=',-') + ')'


def write(db, new, changed, expire, status, feed=DEFAULT_FEED):
    headers = {**db.headers, 'Prefer': 'return=minimal'}
    table = db.url + '/rest/v1/opportunities'
    for i in range(0, len(new), BATCH_SIZE):
        batch = [{**r, 'status': status, 'tags': [], 'missing_fields': []}
                 for r in new[i:i + BATCH_SIZE]]
        json_request(table, headers, batch, service='Supabase')
    for row in changed:
        json_request(table + '?' + row_filter([row['source_id']], feed), headers,
                     {f: row[f] for f in CONTENT_FIELDS}, method='PATCH', service='Supabase')
    for i in range(0, len(expire), 100):
        json_request(table + '?' + row_filter(expire[i:i + 100], feed), headers,
                     {'status': EXPIRED}, method='PATCH', service='Supabase')


def run(max_age_days=None, limit=None, status='pending', dry_run=False, feed=DEFAULT_FEED):
    db = SupabaseClient(settings())
    fmt, rows, closed, skipped, total = load(feed, max_age_days)
    if fmt == 'readme' and max_age_days is not None:
        print('Note: --max-age-days is ignored for README lists (they have no reliable dates).')
    new, changed, expire, unresolved = changes(rows, closed, existing_rows(db, feed), limit)
    print(f'{feed.repo} ({fmt}): {total} listings; {len(rows)} open {feed.label}, '
          f'{skipped} skipped (no usable link). Source: {feed.source}, type: {feed.type}')
    print(f'To add: {len(new)} (status {status}); to update: {len(changed)}; '
          f'to expire: {len(expire)}; links that did not lead to the employer: {unresolved}')
    if dry_run:
        for row in new[:5]:
            print(f"  {row['organization']} | {row['title']} | {row['url']}")
        print('Dry run: nothing written.')
        return 0
    write(db, new, changed, expire, status, feed)
    print('Done.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Import listings from a GitHub job-list repo.')
    parser.add_argument('--repo', default=DEFAULT_REPO,
                        help=f'GitHub owner/name (default {DEFAULT_REPO})')
    parser.add_argument('--format', default='auto', choices=FORMATS,
                        help='auto (default): listings.json if the repo has one, else the README table')
    parser.add_argument('--term', default=None,
                        help='Only this term, e.g. "Summer 2028" (default: from the repo name; "" = all)')
    parser.add_argument('--limit', type=int, default=None, help='Most new listings to add this run')
    parser.add_argument('--max-age-days', type=int, default=None,
                        help='Only listings posted in the last N days (listings format only)')
    parser.add_argument('--status', choices=STATUSES, default='pending',
                        help='Status for newly added rows (default pending)')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    args = parser.parse_args()
    try:
        feed = feed_for(args.repo, args.term, args.format)
        raise SystemExit(run(args.max_age_days, args.limit, args.status, args.dry_run, feed))
    except (ServiceError, ValueError) as exc:
        raise SystemExit(f'Stopped: {exc}')


if __name__ == '__main__':
    main()
