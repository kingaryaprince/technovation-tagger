"""Import AmeriCorps national service positions from the public My AmeriCorps listing search
(my.americorps.gov) into the Supabase opportunities table. No key or account is needed.

Run:  python3 -m alumni_tagger.americorps --dry-run      (preview, no writes)
      python3 -m alumni_tagger.americorps --limit 20     (add at most 20 new listings)
      python3 -m alumni_tagger.americorps                (add every open listing)
Options: --areas T,E   service areas to search (default Technology and Education; see AREAS)
         --status active   skip admin review
         --no-details      skip each listing's own page (faster, shorter descriptions)

The site has no API, so this reads its HTML: the search results (10 per page), then each new
listing's page for its duties, benefits, skills and schedule. It waits between requests to
be polite. Contact names, emails and phone numbers on listing pages are never saved.

Rows are matched on (source='americorps', source_id=the listing id). New listings are inserted
with --status (default pending) and empty tags, so the hourly autotag workflow tags them.
Existing rows keep their status and tags; their text and deadline are refreshed. Listings no
longer in the search, or past their deadline, are set to expired. AmeriCorps positions pay a
living stipend and education award, so they are saved as type 'volunteer' (national service).
"""
from __future__ import annotations

import argparse
import html
import re
import time
from datetime import date
from http.cookiejar import CookieJar
from urllib import parse, request

from .clients import ServiceError, SupabaseClient, json_request, settings

BASE = 'https://my.americorps.gov'
START_URL = BASE + '/mp/listing/publicRequestSearch.do'
SEARCH_URL = BASE + '/mp/listing/search.do'
LISTING_URL = BASE + '/mp/listing/viewListing.do?id={}'
SOURCE = 'americorps'
TYPE = 'volunteer'
PER_PAGE = 10
MAX_PAGES = 120            # 1,200 listings; the default search has a few hundred
DELAY = 1.0                # seconds between requests
DESCRIPTION_LIMIT = 3000
FAR_FUTURE_YEARS = 3       # some listings say "open until 4046": treat as rolling
USER_AGENT = 'TechnovationAlumniHub-opportunities/1.0 (student nonprofit project)'
STATUSES = ('pending', 'active')
EXPIRED = 'expired'
# Service-area codes from the site's Advanced Search form.
AREAS = {
    'T': 'Technology', 'E': 'Education', 'EB': 'Entrepreneur/Business', 'CY': 'Children/Youth',
    'CED': 'Community and Economic Development', 'CO': 'Community Outreach', 'H': 'Health',
    'PH': 'Public Health AmeriCorps', 'EV': 'Environment', 'DR': 'Disaster Relief',
    'HG': 'Hunger', 'HLN': 'Homelessness', 'VT': 'Veterans',
}
DEFAULT_AREAS = ('T', 'E')
PROGRAM_TYPES = {'SN': 'AmeriCorps State and National', 'VISTA': 'AmeriCorps VISTA',
                 'NCCC': 'AmeriCorps NCCC', 'NLDR': 'AmeriCorps NCCC Team Leaders',
                 'VLDR': 'AmeriCorps VISTA Leaders'}
FIELDS = ['type', 'title', 'organization', 'location', 'remote', 'description', 'url',
          'deadline', 'rolling', 'missing_fields']
REVIEW_FIELDS = ['title', 'organization', 'location', 'description', 'url', 'deadline']
PAGE_SIZE = 1000
BATCH_SIZE = 500


# ---------- reading HTML ----------

def clean(fragment):
    """HTML fragment -> one line of plain text."""
    no_tags = re.sub(r'<[^>]+>', ' ', fragment or '')
    return ' '.join(html.unescape(no_tags).replace('\xa0', ' ').split())


def parse_day(value):
    """'08/01/2027' -> date(2027, 8, 1); None if missing or unreadable."""
    m = re.search(r'(\d{2})/(\d{2})/(\d{4})', value or '')
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


ROW_RE = re.compile(
    r'viewListing\.do\?fromSearch=true&(?:amp;)?id=(\d+)"[^>]*>(.*?)</a>\s*</td>\s*'
    r'<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>\s*'
    r'(?:<tr[^>]*>\s*<td[^>]*>(.*?)</td>)?', re.S | re.I)


def parse_results(page_html):
    """Search results page -> [{'id', 'title', 'program', 'apply_from', 'apply_to', 'state',
    'summary'}]."""
    out = []
    for lid, title, program, dates, state, summary in ROW_RE.findall(page_html):
        days = re.findall(r'\d{2}/\d{2}/\d{4}', dates)
        out.append({'id': lid, 'title': clean(title), 'program': clean(program),
                    'apply_from': parse_day(days[0]) if days else None,
                    'apply_to': parse_day(days[1]) if len(days) > 1 else None,
                    'state': clean(state), 'summary': clean(summary)})
    return out


def result_count(page_html):
    m = re.search(r'returned\s+([\d,]+)\s+result', page_html, re.I)
    return int(m.group(1).replace(',', '')) if m else 0


def labelled(page_html, label):
    """Text after '<b>Label :</b>' up to the end of its cell."""
    m = re.search(r'<b>\s*' + re.escape(label) + r'\s*:?\s*</b>(.*?)</td>', page_html, re.S | re.I)
    return clean(m.group(1)).strip(' .,') if m else ''


def summary_value(page_html, label):
    """A value from the listing's SUMMARY box: the cell right after the bold label."""
    m = re.search(r'<b>\s*' + re.escape(label) + r'\s*:?\s*</b>\s*</td>(.*?)</tr>\s*(?:<tr[^>]*>(.*?)</tr>)?',
                  page_html, re.S | re.I)
    if not m:
        return ''
    same_row = clean(m.group(1))
    return same_row or clean(m.group(2) or '')


def tidy_list(value):
    """'Stipend , Training .' -> 'Stipend, Training'."""
    parts = [p.strip(' .') for p in value.split(',')]
    return ', '.join(p for p in parts if p)


def parse_listing(page_html):
    """A listing's own page -> extra details. Contact details are deliberately not read."""
    m = re.search(r'class="sub_section_title">(.*?)</span>.*?<tr>\s*<td>\s*&nbsp;\s*</td>\s*</tr>\s*'
                  r'<tr>\s*<td>(.*?)</td>', page_html, re.S | re.I)
    return {
        'about': clean(m.group(2)) if m else '',
        'duties': labelled(page_html, 'Member Duties'),
        'benefits': tidy_list(labelled(page_html, 'Program Benefits')),
        'service_areas': tidy_list(labelled(page_html, 'Service Areas')),
        'skills': tidy_list(labelled(page_html, 'Skills')),
        'schedule': summary_value(page_html, 'Work Schedule'),
        'education': summary_value(page_html, 'Education level'),
        'locations': summary_value(page_html, 'Program Locations'),
        'program_dates': summary_value(page_html, 'Program Start/End Date'),
    }


# ---------- building a row ----------

def is_rolling(apply_to, today):
    return apply_to is None or apply_to.year > today.year + FAR_FUTURE_YEARS


def describe(item, extra):
    program = PROGRAM_TYPES.get(item['program'], item['program'])
    lines = [f"{item['title']} ({program}). AmeriCorps national service position."]
    for label, value in (('About', extra.get('about') or item['summary']),
                         ('Member duties', extra.get('duties')),
                         ('Benefits', extra.get('benefits')),
                         ('Service areas', extra.get('service_areas')),
                         ('Skills', extra.get('skills')),
                         ('Schedule', extra.get('schedule')),
                         ('Program dates', extra.get('program_dates')),
                         ('Education level', extra.get('education'))):
        if value:
            lines.append(f'{label}: {value}')
    text = '\n'.join(lines)
    return text if len(text) <= DESCRIPTION_LIMIT else text[:DESCRIPTION_LIMIT - 1].rstrip() + '…'


def to_row(item, extra, today):
    rolling = is_rolling(item['apply_to'], today)
    location = extra.get('locations') or item['state'] or None
    text = ' '.join(filter(None, [item['summary'], extra.get('about', ''), extra.get('duties', '')])).lower()
    row = {
        'source': SOURCE,
        'source_id': item['id'],
        'type': TYPE,
        'title': item['title'],
        'organization': PROGRAM_TYPES.get(item['program'], item['program'] or 'AmeriCorps'),
        'location': location,
        'remote': bool(re.search(r'\bremote\b|\bvirtual\b', text)),
        'description': describe(item, extra),
        'url': LISTING_URL.format(item['id']),
        'deadline': None if rolling else item['apply_to'].isoformat(),
        'rolling': rolling,
    }
    review = [f for f in REVIEW_FIELDS if not row.get(f)]
    if rolling:
        review = [f for f in review if f != 'deadline']
    row['missing_fields'] = review
    return row


# ---------- fetching ----------

class Site:
    """A cookie-keeping session: the search pages only work inside one."""

    def __init__(self, sleep=time.sleep, opener=None):
        self.sleep = sleep
        self.opener = opener or request.build_opener(request.HTTPCookieProcessor(CookieJar()))
        self.calls = 0

    def get(self, url, form=None, tries=3):
        """One page. Results pages are retried here, since a missing page means a partial search."""
        for attempt in range(tries):
            try:
                return self._get(url, form)
            except ServiceError:
                if attempt == tries - 1 or url != SEARCH_URL:
                    raise
                self.sleep(5 * (attempt + 1))

    def _get(self, url, form=None):
        if self.calls:
            self.sleep(DELAY)
        self.calls += 1
        data = parse.urlencode(form, doseq=True).encode() if form is not None else None
        req = request.Request(url, data=data, headers={'User-Agent': USER_AGENT})
        try:
            with self.opener.open(req, timeout=60) as response:
                return response.read().decode('utf-8', 'replace')
        except Exception as exc:     # network, HTTP or decoding problem: stop, don't half-import
            raise ServiceError('AmeriCorps', getattr(exc, 'code', None)) from None


def listing_page(site, listing_id, tries=3):
    """A listing's own page, trying a few times; None if the site keeps failing, so one slow
    page never stops the whole import."""
    for attempt in range(tries):
        try:
            return site.get(LISTING_URL.format(listing_id))
        except ServiceError:
            if attempt == tries - 1:
                return None
            site.sleep(5 * (attempt + 1))
    return None


def search_form(areas, page):
    """The fields the site's own Advanced Search and paging links send."""
    form = {'criteria.serviceAreas': list(areas), 'criteria.serviceAreasString': ';'.join(areas),
            'criteria.searchAllListings': 'false', 'sortCriteria': '+programName',
            'nsaID': '-1', 'criteria.listingID': '0', 'curPage': str(page)}
    for name in ('programName', 'programState', 'programType', 'educationLevel',
                 'languagesString', 'metroAreasString', 'programStatesString',
                 'programTypesString', 'selectedSkillsString', 'selectedTermsString',
                 'programCode', 'listingIDStr'):
        form['criteria.' + name] = ''
    return form


def fetch_results(site, areas):
    """Every listing the search returns. complete=False if not every page could be read,
    so a partial result never expires listings that are still open."""
    site.get(START_URL)                       # start a session
    first = site.get(SEARCH_URL, search_form(areas, 1))
    total = result_count(first)
    items = parse_results(first)
    pages = min(-(-total // PER_PAGE), MAX_PAGES)
    for page in range(2, pages + 1):
        found = parse_results(site.get(SEARCH_URL, {**search_form(areas, page), 'count': str(total)}))
        if not found:
            break
        items.extend(found)
    seen, unique = set(), []
    for item in items:
        if item['id'] not in seen:
            seen.add(item['id'])
            unique.append(item)
    complete = total > 0 and len(unique) >= total
    return unique, total, complete


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
    if field == 'deadline':
        return (str(old)[:10] if old else None) == new
    if field == 'missing_fields':
        return sorted(old or []) == sorted(new)
    return old == new


def changes(rows, existing, today, complete, limit=None):
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


def parse_areas(value):
    areas = [a.strip().upper() for a in (value or '').split(',') if a.strip()]
    unknown = [a for a in areas if a not in AREAS]
    if not areas or unknown:
        raise ValueError('--areas must be a comma-separated list of: '
                         + ', '.join(f'{k} ({v})' for k, v in AREAS.items()))
    return list(dict.fromkeys(areas))


def run(areas=DEFAULT_AREAS, limit=None, status='pending', dry_run=False, details=True,
        today=None, site=None):
    today = today or date.today()
    site = site or Site()
    items, total, complete = fetch_results(site, areas)
    print(f'AmeriCorps ({", ".join(AREAS[a] for a in areas)}): {total} listings, read {len(items)}')
    open_items = [i for i in items if i['apply_to'] is None or i['apply_to'] >= today]
    skipped = len(items) - len(open_items)
    db = SupabaseClient(settings())
    existing = existing_rows(db)
    # Only new listings need their own page read; existing rows keep their longer text.
    plain = [to_row(i, {}, today) for i in open_items]
    new_order = [r['source_id'] for r in plain if r['source_id'] not in existing]
    if limit is not None:
        new_order = new_order[:limit]
    if dry_run:
        new_order = new_order[:5]       # a preview only opens the pages it prints
    new_ids = set(new_order)
    rows, failed_pages, opened = [], 0, 0
    for item, row in zip(open_items, plain):
        if details and item['id'] in new_ids:
            page = listing_page(site, item['id'])
            opened += 1
            if page is None:
                failed_pages += 1      # keep the short summary; the row is still added
            else:
                row = to_row(item, parse_listing(page), today)
            if opened % 50 == 0:
                print(f'  read {opened} of {len(new_ids)} listing pages')
        elif item['id'] in existing:
            old = existing[item['id']]   # keep the detailed text saved the first time
            row = {**row, 'description': old.get('description') or row['description'],
                   'location': old.get('location') or row['location'],
                   'remote': old.get('remote', row['remote']),
                   'missing_fields': old.get('missing_fields') or row['missing_fields']}
        rows.append(row)
    new, changed, expire = changes(rows, existing, today, complete, limit)
    print(f'{len(rows)} open listings, {skipped} already closed.')
    print(f'To add: {len(new)} (status {status}); to update: {len(changed)}; to expire: {len(expire)}')
    if not complete:
        print('Not every results page was read, so nothing is expired for being missing.')
    if failed_pages:
        print(f'{failed_pages} listing page(s) could not be read; those rows use the short summary.')
    if dry_run:
        for row in new[:5]:
            print(f"  {row['organization']} | {row['title']} | {row['location'] or '?'} | "
                  f"closes {row['deadline'] or 'rolling'} | {row['url']}")
        print('Dry run: nothing written.')
        return 0
    write(db, new, changed, expire, status)
    print('Done.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Import AmeriCorps national service listings.')
    parser.add_argument('--areas', default=','.join(DEFAULT_AREAS),
                        help='Service-area codes, comma-separated (default T,E). '
                             + ', '.join(f'{k}={v}' for k, v in AREAS.items()))
    parser.add_argument('--limit', type=int, default=None, help='Most new listings to add this run')
    parser.add_argument('--status', choices=STATUSES, default='pending',
                        help='Status for newly added rows (default pending)')
    parser.add_argument('--no-details', action='store_true',
                        help="Don't open each new listing's page (faster, shorter descriptions)")
    parser.add_argument('--dry-run', action='store_true', help='Preview without writing')
    args = parser.parse_args()
    try:
        raise SystemExit(run(parse_areas(args.areas), args.limit, args.status, args.dry_run,
                             not args.no_details))
    except (ServiceError, ValueError) as exc:
        raise SystemExit(f'Stopped: {exc}')


if __name__ == '__main__':
    main()
