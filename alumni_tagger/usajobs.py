"""Import federal student and recent-graduate jobs from the USAJOBS Search API into the
Supabase opportunities table.

Run:  python3 -m alumni_tagger.usajobs --dry-run      (preview, no writes)
      python3 -m alumni_tagger.usajobs --limit 20     (add at most 20 new listings)
      python3 -m alumni_tagger.usajobs                (add every open listing)
Options: --paths student,graduates (default both), --status active (skip admin review),
         --keyword "data" (only listings matching a word).

Needs a free key from developer.usajobs.gov. Put both values in .env.local or
config.local.json on your computer, or in GitHub repository secrets for the workflow:
  USAJOBS_API_KEY   the Authorization-Key USAJOBS emailed you
  USAJOBS_EMAIL     the exact email you requested the key with (sent as User-Agent)

Hiring paths (USAJOBS codes):
  student    federal Pathways internships for current students   -> type 'internship'
  graduates  Recent Graduates program jobs                        -> type 'job'
An announcement open to both is saved once, as an internship.

Rows are matched on (source='usajobs', source_id=the USAJOBS announcement id). New listings
are inserted with --status (default pending, so an admin reviews them) and empty tags, so
the hourly autotag workflow tags them. Existing rows keep their status and tags; only their
listing text and deadline are refreshed. Rows no longer in the search (closed or withdrawn)
and rows whose deadline has passed are set to expired. Blank schema fields are listed in
missing_fields so an admin can fill them in.
"""
import argparse
from datetime import date
from urllib import parse

from .clients import ServiceError, SupabaseClient, json_request, require, settings

SEARCH_URL = 'https://data.usajobs.gov/api/search'
SOURCE = 'usajobs'
PATH_TYPES = {'student': 'internship', 'graduates': 'job'}   # first path wins for duplicates
STATUSES = ('pending', 'active')
EXPIRED = 'expired'
RESULTS_PER_PAGE = 500     # the API's maximum
MAX_PAGES = 20             # 10,000 listings; far more than these hiring paths ever have
DESCRIPTION_LIMIT = 3000   # characters; enough for the tagger to quote from
# Columns this importer owns, refreshed on existing rows when USAJOBS changes them.
FIELDS = ['type', 'title', 'organization', 'location', 'remote', 'description', 'url',
          'deadline', 'rolling', 'missing_fields']
# Schema fields an admin should fill in by hand when USAJOBS leaves them blank.
REVIEW_FIELDS = ['title', 'organization', 'location', 'description', 'url', 'deadline']
PAGE_SIZE = 1000
BATCH_SIZE = 500


# ---------- reading one listing ----------

def text(value):
    return ' '.join(str(value).split()) if value not in (None, '') else ''


def details(desc):
    user_area = desc.get('UserArea') or {}
    return user_area.get('Details') or {}


def parse_day(value):
    """'2026-12-01T23:59:59.9970' -> date(2026, 12, 1); None if missing or unreadable."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def pay_text(desc):
    pay = (desc.get('PositionRemuneration') or [{}])[0]
    low, high = pay.get('MinimumRange'), pay.get('MaximumRange')
    if not low:
        return ''
    hourly = pay.get('RateIntervalCode') == 'PH'
    money = '${:,.2f}' if hourly else '${:,.0f}'
    try:
        low = money.format(float(low))
        high = money.format(float(high)) if high else ''
    except ValueError:
        return ''
    per = 'per hour' if hourly else {'PA': 'per year'}.get(pay.get('RateIntervalCode'), '')
    return ' '.join(filter(None, [low + (f' to {high}' if high and high != low else ''), per]))


def locations(desc):
    names = [text(p.get('LocationName')) for p in desc.get('PositionLocation') or []]
    names = [n for n in dict.fromkeys(names) if n]
    if not names and text(desc.get('PositionLocationDisplay')):
        names = [text(desc['PositionLocationDisplay'])]
    return names


def is_remote(desc, places):
    flag = details(desc).get('RemoteIndicator')
    if isinstance(flag, str):
        flag = flag.strip().lower() in ('true', 'yes', 'y')
    return bool(flag) or any('remote' in p.lower() or 'anywhere in the u.s.' in p.lower()
                             for p in places)


def describe(desc, places, path):
    """Plain-text description built only from the announcement, for people and the tagger."""
    title, org = text(desc.get('PositionTitle')), text(desc.get('OrganizationName'))
    dept = text(desc.get('DepartmentName'))
    info = details(desc)
    lines = [f'{title} at {org}' + (f' ({dept})' if dept and dept != org else '') + '.']
    lines.append('Program: ' + ('Pathways internship for current students.' if path == 'student'
                                else 'Recent Graduates program.'))
    lines.append(f'Location: {"; ".join(places) or "Not listed"}.')
    schedule = ', '.join(text(s.get('Name')) for s in desc.get('PositionSchedule') or [] if s.get('Name'))
    if schedule:
        lines.append(f'Schedule: {schedule}.')
    if pay_text(desc):
        lines.append(f'Pay: {pay_text(desc)}.')
    who = info.get('WhoMayApply')
    who = text(who.get('Name') if isinstance(who, dict) else who)
    if who:
        lines.append(f'Who may apply: {who}.')
    if text(info.get('JobSummary')):
        lines.append('Summary: ' + text(info['JobSummary']))
    if text(desc.get('QualificationSummary')):
        lines.append('Qualifications: ' + text(desc['QualificationSummary']))
    joined = '\n'.join(lines)
    return joined if len(joined) <= DESCRIPTION_LIMIT else joined[:DESCRIPTION_LIMIT - 1].rstrip() + '…'


def listing_url(desc):
    for url in [desc.get('PositionURI'), *(desc.get('ApplyURI') or [])]:
        parts = parse.urlsplit(text(url))
        if parts.scheme == 'https' and parts.hostname:
            return text(url)
    return ''


def to_row(item, path):
    """One opportunities row from a SearchResultItem, or None if it has no id or title."""
    desc = item.get('MatchedObjectDescriptor') or {}
    source_id = text(item.get('MatchedObjectId') or desc.get('PositionID'))
    title = text(desc.get('PositionTitle'))
    if not source_id or not title:
        return None
    places = locations(desc)
    row = {
        'source': SOURCE,
        'source_id': source_id,
        'type': PATH_TYPES[path],
        'title': title,
        'organization': text(desc.get('OrganizationName')) or text(desc.get('DepartmentName')),
        'location': '; '.join(places) or None,
        'remote': is_remote(desc, places),
        'description': describe(desc, places, path),
        'url': listing_url(desc) or None,
        'deadline': (lambda d: d.isoformat() if d else None)(parse_day(desc.get('ApplicationCloseDate'))),
        'rolling': False,
    }
    row['missing_fields'] = [f for f in REVIEW_FIELDS if not row.get(f)]
    return row


def plan(results_by_path, today):
    """Open rows (first hiring path wins for an announcement in both) and a count of skipped ones."""
    rows, skipped = {}, 0
    for path, items in results_by_path:
        for item in items:
            row = to_row(item, path)
            if row is None:
                skipped += 1
            elif row['deadline'] and row['deadline'] < today.isoformat():
                skipped += 1                       # already closed
            else:
                rows.setdefault(row['source_id'], row)
    return list(rows.values()), skipped


# ---------- fetching ----------

def credentials(config=None):
    config = config or settings()
    require(config, 'USAJOBS_API_KEY', 'USAJOBS_EMAIL')
    return {'Host': 'data.usajobs.gov', 'User-Agent': config['USAJOBS_EMAIL'],
            'Authorization-Key': config['USAJOBS_API_KEY']}


def search_url(path, page, keyword=None):
    query = {'HiringPath': path, 'ResultsPerPage': RESULTS_PER_PAGE, 'Page': page}
    if keyword:
        query['Keyword'] = keyword
    return SEARCH_URL + '?' + parse.urlencode(query)


def fetch(path, headers, keyword=None, get=json_request):
    """Every open announcement for one hiring path. Raises if a page can't be read, so a
    partial result never expires listings that are still open."""
    items, page, pages = [], 1, 1
    while page <= min(pages, MAX_PAGES):
        data = get(search_url(path, page, keyword), headers, method='GET',
                   service='USAJOBS', retries=2) or {}
        result = data.get('SearchResult')
        if not isinstance(result, dict):
            raise ServiceError('USAJOBS', 'unexpected response')
        items.extend(result.get('SearchResultItems') or [])
        try:
            pages = int((result.get('UserArea') or {}).get('NumberOfPages') or 1)
        except (TypeError, ValueError):
            pages = 1
        page += 1
    complete = pages <= MAX_PAGES
    return items, complete


# ---------- database ----------

def existing_rows(db):
    found, offset = {}, 0
    columns = ','.join(['source_id', 'status'] + FIELDS)
    while True:
        page = db.select(f'opportunities?select={columns}&source=eq.{SOURCE}'
                         f'&order=source_id&limit={PAGE_SIZE}&offset={offset}') or []
        found.update((r['source_id'], r) for r in page)
        if len(page) < PAGE_SIZE:
            return found
        offset += PAGE_SIZE


def same(field, old, new):
    """Whether a saved value matches the new one (Supabase returns dates and lists its own way)."""
    if field == 'deadline':
        return (str(old)[:10] if old else None) == new
    if field == 'missing_fields':
        return sorted(old or []) == sorted(new)
    return old == new


def changes(rows, existing, today, complete, limit=None):
    """New rows to insert, existing rows whose listing changed, and ids to expire."""
    new = [r for r in rows if r['source_id'] not in existing]
    if limit is not None:
        new = new[:limit]
    changed = [r for r in rows if r['source_id'] in existing
               and not all(same(f, existing[r['source_id']].get(f), r[f]) for f in FIELDS)]
    open_ids = {r['source_id'] for r in rows}
    expire = []
    for source_id, old in existing.items():
        if old.get('status') == EXPIRED:
            continue
        gone = complete and source_id not in open_ids
        past = bool(old.get('deadline')) and str(old['deadline'])[:10] < today.isoformat()
        if gone or past:
            expire.append(source_id)
    return new, changed, sorted(expire)


def row_filter(source_ids):
    ids = ','.join('"' + i.replace('"', '') + '"' for i in source_ids)
    return 'source=eq.' + SOURCE + '&source_id=in.(' + parse.quote(ids, safe=',-') + ')'


def write(db, new, changed, expire, status):
    headers = {**db.headers, 'Prefer': 'return=minimal'}
    table = db.url + '/rest/v1/opportunities'
    for i in range(0, len(new), BATCH_SIZE):
        batch = [{**r, 'status': status, 'tags': []} for r in new[i:i + BATCH_SIZE]]
        json_request(table, headers, batch, service='Supabase')
    for row in changed:
        json_request(table + '?' + row_filter([row['source_id']]), headers,
                     {f: row[f] for f in FIELDS}, method='PATCH', service='Supabase')
    for i in range(0, len(expire), 100):
        json_request(table + '?' + row_filter(expire[i:i + 100]), headers,
                     {'status': EXPIRED}, method='PATCH', service='Supabase')


def parse_paths(value):
    paths = [p.strip().lower() for p in (value or '').split(',') if p.strip()]
    unknown = [p for p in paths if p not in PATH_TYPES]
    if not paths or unknown:
        raise ValueError('--paths must be a comma-separated list of: ' + ', '.join(PATH_TYPES))
    return list(dict.fromkeys(paths))


def run(paths=tuple(PATH_TYPES), limit=None, status='pending', dry_run=False, keyword=None,
        today=None):
    today = today or date.today()
    config = settings()
    headers = credentials(config)
    fetched, complete = [], keyword is None    # a keyword search can't tell what closed
    for path in paths:
        items, whole = fetch(path, headers, keyword)
        fetched.append((path, items))
        complete = complete and whole
        print(f'USAJOBS {path}: {len(items)} open announcements')
    rows, skipped = plan(fetched, today)
    db = SupabaseClient(config)
    new, changed, expire = changes(rows, existing_rows(db), today, complete, limit)
    review = sum(1 for r in new if r['missing_fields'])
    print(f'{len(rows)} usable listings, {skipped} skipped (no id/title or already closed).')
    print(f'To add: {len(new)} (status {status}, {review} with blank fields for review); '
          f'to update: {len(changed)}; to expire: {len(expire)}')
    if dry_run:
        for row in new[:5]:
            print(f"  {row['organization']} | {row['title']} | closes {row['deadline'] or '?'} | {row['url']}")
        print('Dry run: nothing written.')
        return 0
    write(db, new, changed, expire, status)
    print('Done.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Import USAJOBS student and recent-graduate listings.')
    parser.add_argument('--paths', default=','.join(PATH_TYPES),
                        help='Hiring paths, comma-separated (default student,graduates)')
    parser.add_argument('--limit', type=int, default=None, help='Most new listings to add this run')
    parser.add_argument('--status', choices=STATUSES, default='pending',
                        help='Status for newly added rows (default pending)')
    parser.add_argument('--keyword', default=None,
                        help='Only listings matching this keyword (closed rows are then not expired)')
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    args = parser.parse_args()
    try:
        raise SystemExit(run(parse_paths(args.paths), args.limit, args.status, args.dry_run,
                             args.keyword))
    except (ServiceError, ValueError) as exc:
        raise SystemExit(f'Stopped: {exc}')


if __name__ == '__main__':
    main()
