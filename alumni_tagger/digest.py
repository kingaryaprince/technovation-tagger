"""Build each alum's opportunity digest email (top five matches). Nothing is sent.

Run:  python3 -m alumni_tagger.digest --sample      (made-up data in data/sample_digest.json)
      python3 -m alumni_tagger.digest --supabase    (real alumni, ranked by the
                                                     top_opportunities_for_alum SQL function)

Writes three files per alum to digest_preview/: .eml (the complete email; double-click to
open it in Apple Mail or Outlook as if it had arrived), .html and .txt, plus
digest_preview/index.html listing every digest. Open index.html in a browser to review them. digest_preview/ holds
real names and emails in --supabase mode, so do not commit it.
"""
import argparse
import json
import unicodedata
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from html import escape
from pathlib import Path
from urllib import parse

from .clients import ServiceError
from .core import ROOT

# Same rules as the top_opportunities_for_alum SQL function (sql/014_better_matching.sql).
MATCH_CATEGORIES = ('interests', 'skills', 'industry')
TOP_N = 5
# A shared tag scores 2, or 1 if it's generic: soft-skill and career labels that fit almost
# any listing. A match needs MIN_SCORE: one specific tag, or two generic ones.
GENERIC_TAGS = frozenset({
    'Career Development', 'Cross-Cultural Communication', 'Facilitation', 'Job Searching',
    'Leadership', 'Mentorship', 'Networking', 'Organization', 'Problem Solving',
    'Public Speaking', 'Teamwork/Collaboration', 'Technical Communication', 'Time Management',
    'Writing/Content Creation'})
MIN_SCORE = 2
# Internships go only to alumni with one of these career stages (or none recorded yet).
INTERNSHIP_STAGES = frozenset({'Student', 'Student + Working'})
SOON_DAYS = 7              # deadlines this close get a "Closes soon" label

# Wording and links. Leave a URL empty to leave that link out.
HUB_NAME = 'Technovation Alumni Hub'
FROM_ADDRESS = 'alumni-hub@example.org'   # placeholder until a real sending address exists
BROWSE_URL = ''            # page listing every opportunity, once one exists
PREFERENCES_URL = ''       # where alumni update their interests (e.g. the intake form)
UNSUBSCRIBE_EMAIL = ''     # set by send_digest to the sending address; adds an unsubscribe link
# Technovation brand colors (from the speaker search mockup).
NAVY = '#1C1248'
PURPLE = '#8C71DF'
LAVENDER = '#A994E7'
LIME = '#D8E482'
CREAM = '#F9F4EB'
MUTED = '#5B547B'
RULE = '#E7E2DA'

OUT_DIR = ROOT / 'digest_preview'
TYPE_LABELS = {'job': 'Job', 'internship': 'Internship', 'workshop': 'Workshop',
               'event': 'Event', 'volunteer': 'Volunteer', 'fellowship': 'Fellowship',
               'scholarship': 'Scholarship', 'mentorship': 'Mentorship'}


# ---------- ranking (sample mode; live mode uses the SQL function) ----------

def alum_match_tags(tags):
    """Flatten the alum's tags JSON to the categories opportunities are matched on."""
    if not isinstance(tags, dict):
        return set()
    return {t for c in MATCH_CATEGORIES for t in (tags.get(c) or []) if isinstance(t, str)}


def tag_points(tag):
    return 1 if tag in GENERIC_TAGS else 2


def eligible(alum, opp):
    """Internships only for students (or alumni whose career stage isn't recorded yet)."""
    if opp.get('type') != 'internship':
        return True
    tags = alum.get('tags') if isinstance(alum.get('tags'), dict) else {}
    stages = [s for s in (tags.get('career_stage') or []) if isinstance(s, str)]
    return not stages or bool(INTERNSHIP_STAGES & set(stages))


def is_open(opp, today):
    """Rolling, no deadline listed, or a deadline that hasn't passed; never expired."""
    if opp.get('status') == 'expired':
        return False
    deadline = opp.get('deadline')
    return bool(opp.get('rolling')) or deadline is None or deadline >= today


def rank(alum, opportunities, today, limit=TOP_N, min_score=MIN_SCORE):
    """Python copy of top_opportunities_for_alum, for sample data."""
    mine = alum_match_tags(alum.get('tags'))
    candidates = []
    for order, opp in enumerate(opportunities):
        if not is_open(opp, today) or not eligible(alum, opp):
            continue
        # Specific tags first, so "Why we picked it" leads with the real reason.
        matched = sorted(set(opp.get('tags') or []) & mine, key=lambda t: (t in GENERIC_TAGS, t))
        score = sum(map(tag_points, matched))
        if score >= min_score:
            deadline = opp.get('deadline')
            candidates.append(((-score, deadline is None, deadline or date.max, order),
                               {**opp, 'score': score, 'matched_tags': matched}))
    candidates.sort(key=lambda pair: pair[0])
    # Within the same score, take turns between sources so one board can't fill the digest.
    turns = {}
    keyed = []
    for key, opp in candidates:
        slot = (opp['score'], opp.get('source'))
        turns[slot] = turns.get(slot, 0) + 1
        keyed.append(((key[0], turns[slot]) + key[1:], opp))
    return [opp for _, opp in sorted(keyed, key=lambda pair: pair[0])][:limit]


# ---------- formatting helpers ----------

def first_name(full_name):
    name = (full_name or '').strip()
    return name.split()[0] if name else 'there'


def safe_url(url):
    """Only real web links make it into an email."""
    url = (url or '').strip()
    parts = parse.urlsplit(url)
    return url if parts.scheme in ('http', 'https') and parts.netloc else ''


def short_date(d):
    return f'{d:%b} {d.day}'


def deadline_text(opp, today):
    d = opp.get('deadline')
    if d is None or (opp.get('rolling') and d < today):
        return 'Rolling: apply anytime', False
    days = (d - today).days
    if days == 0:
        return 'Closes today', True
    if days <= SOON_DAYS:
        return f'Closes soon: {short_date(d)}', True
    return f'Apply by {short_date(d)}', False


def where_text(opp):
    bits = [opp.get('organization') or '']
    if opp.get('remote'):
        bits.append('Remote')
    elif opp.get('location'):
        bits.append(opp['location'])
    return ' · '.join(b for b in bits if b)


def type_label(opp):
    t = (opp.get('type') or '').strip()
    return TYPE_LABELS.get(t.lower(), t.title())


def subject_line(alum, opps):
    n = len(opps)
    what = 'opportunity' if n == 1 else f'{n} opportunities'
    name = (alum.get('full_name') or '').strip()
    return f'{first_name(name)}, your top {what} this week' if name else f'Your top {what} this week'


# ---------- email rendering ----------
# Plain, letter-style layout: one column, no boxes or badges, text links instead of buttons.

SERIF = "Georgia,'Times New Roman',serif"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def intro_text(n):
    if n == 1:
        return ("Here's an opportunity we think you'll like based on your interests and skills, "
                "picked from this week's listings.")
    return (f"Here are {n} opportunities we think you'll like based on your interests and skills, "
            "picked from this week's listings. Your best matches are at the top.")


def render_item(n, opp, today):
    when, urgent = deadline_text(opp, today)
    url = safe_url(opp.get('url'))
    title = escape(opp.get('title') or 'Untitled opportunity')
    if url:
        title = f'<a href="{escape(url)}" style="color:{NAVY};text-decoration:none;">{title}</a>'
    label = type_label(opp)
    meta = ' · '.join(escape(b) for b in [label, where_text(opp)] if b)
    when_html = (f'<span style="background:{PURPLE};color:#ffffff;padding:1px 6px;">{escape(when)}</span>'
                 if urgent else escape(when))
    link = (f'''
        <p style="margin:10px 0 0 0;font-size:15px;">
          <a href="{escape(url)}" style="color:{NAVY};font-weight:700;text-decoration:underline;">View opportunity &rarr;</a></p>'''
            if url else '')
    top_rule = f'border-top:1px solid {RULE};' if n > 1 else ''
    return f'''
    <tr><td style="padding:22px 0;{top_rule}">
        <p style="margin:0;font-family:{SERIF};font-size:20px;line-height:27px;color:{NAVY};">
          <span style="color:{PURPLE};">{n}.</span> {title}</p>
        <p style="margin:6px 0 0 0;font-size:14px;line-height:21px;color:{MUTED};">{meta}</p>
        <p style="margin:4px 0 0 0;font-size:14px;line-height:21px;color:{NAVY};font-weight:600;">{when_html}</p>
        <p style="margin:8px 0 0 0;font-size:14px;line-height:21px;color:{MUTED};">
          Why we picked it: {escape(', '.join(opp['matched_tags']))}</p>{link}
    </td></tr>'''


def render_html(alum, opps, today):
    name = escape(first_name(alum.get('full_name')))
    count = 'one opportunity' if len(opps) == 1 else f'{len(opps)} opportunities'
    items = ''.join(render_item(i, o, today) for i, o in enumerate(opps, 1))
    browse, prefs = safe_url(BROWSE_URL), safe_url(PREFERENCES_URL)
    browse_html = (f'<p style="margin:0 0 16px 0;">Want to see everything that\'s open? '
                   f'<a href="{escape(browse)}" style="color:{NAVY};font-weight:700;">Browse all opportunities</a>.</p>'
                   if browse else '')
    prefs_html = (f'<a href="{escape(prefs)}" style="color:{MUTED};">update your interests</a>'
                  if prefs else 'update your interests in the alumni intake form')
    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>{escape(subject_line(alum, opps))}</title></head>
<body style="margin:0;padding:0;background:{CREAM};">
<div style="display:none;max-height:0;overflow:hidden;">{escape(count.capitalize())} picked for you from this week's listings.</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM};">
  <tr><td align="center" style="padding:24px 12px;">
    <table role="presentation" width="600" cellpadding="0" cellspacing="0"
           style="width:100%;max-width:600px;background:#ffffff;font-family:{SANS};">
      <tr><td style="background:{NAVY};padding:18px 32px;">
        <span style="font-family:{SANS};font-size:13px;font-weight:700;letter-spacing:.14em;color:#ffffff;">TECHNOVATION</span>
        <span style="color:{LAVENDER};padding:0 8px;">|</span>
        <span style="font-family:{SERIF};font-style:italic;font-size:17px;color:#ffffff;">Alumni Hub</span>
      </td></tr>
      <tr><td style="padding:32px 32px 4px 32px;font-size:16px;line-height:25px;color:{NAVY};">
        <p style="margin:0 0 16px 0;font-family:{SERIF};font-size:22px;line-height:30px;">Hi {name},</p>
        <p style="margin:0;">{escape(intro_text(len(opps)))}</p>
      </td></tr>
      <tr><td style="padding:8px 32px 0 32px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font-family:{SANS};">{items}
        </table>
      </td></tr>
      <tr><td style="padding:8px 32px 32px 32px;font-size:16px;line-height:25px;color:{NAVY};">
        <p style="margin:14px 0 16px 0;">Good luck with whatever you go for!</p>
        {browse_html}
        <p style="margin:0;">The Technovation Alumni team</p>
      </td></tr>
    </table>
    <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;">
      <tr><td style="padding:18px 32px 0 32px;font-family:{SANS};font-size:12px;line-height:18px;color:{MUTED};">
        You're getting this because you joined the Technovation alumni community. We pick these
        from the interests and skills in your intake answers. Want different picks? You can {prefs_html}.{unsubscribe_html(alum)}
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>
'''


def unsubscribe_link(alum):
    """mailto: link that asks to stop the digest. Empty when no sending address is set."""
    if not UNSUBSCRIBE_EMAIL:
        return ''
    query = parse.urlencode({'subject': 'unsubscribe',
                             'body': f'Please stop sending the alumni digest to {alum.get("email") or "me"}.'},
                            quote_via=parse.quote)
    return f'mailto:{UNSUBSCRIBE_EMAIL}?{query}'


def unsubscribe_html(alum):
    link = unsubscribe_link(alum)
    if not link:
        return ''
    return (f'<br><br>Don\'t want these emails? <a href="{escape(link)}" style="color:{MUTED};">Unsubscribe</a> '
            f'(or email {escape(UNSUBSCRIBE_EMAIL)} with the subject "unsubscribe").')


def render_text(alum, opps, today):
    lines = ['TECHNOVATION | Alumni Hub', '', f'Hi {first_name(alum.get("full_name"))},', '',
             intro_text(len(opps)), '']
    for i, opp in enumerate(opps, 1):
        lines.append(f'{i}. {opp.get("title") or "Untitled opportunity"}')
        meta = ' · '.join(b for b in [type_label(opp), where_text(opp)] if b)
        if meta:
            lines.append(f'   {meta}')
        lines.append(f'   {deadline_text(opp, today)[0]}')
        lines.append(f'   Why we picked it: {", ".join(opp["matched_tags"])}')
        if safe_url(opp.get('url')):
            lines.append(f'   {safe_url(opp.get("url"))}')
        lines.append('')
    lines.append('Good luck with whatever you go for!')
    if safe_url(BROWSE_URL):
        lines.append(f"Want to see everything that's open? {safe_url(BROWSE_URL)}")
    lines += ['', 'The Technovation Alumni team', '', '--',
              "You're getting this because you joined the Technovation alumni community."]
    if safe_url(PREFERENCES_URL):
        lines.append(f'Update your interests: {safe_url(PREFERENCES_URL)}')
    if UNSUBSCRIBE_EMAIL:
        lines.append(f'Unsubscribe: email {UNSUBSCRIBE_EMAIL} with the subject "unsubscribe"')
    return '\n'.join(lines) + '\n'


def build_email(alum, opps, today, from_address=None, to=None, subject_prefix=''):
    """The complete email: To/From/Subject plus plain-text and HTML versions in one message."""
    sender = from_address or FROM_ADDRESS
    msg = EmailMessage()
    msg['From'] = f'{HUB_NAME} <{sender}>'
    msg['To'] = to or alum.get('email') or ''
    msg['Subject'] = subject_prefix + subject_line(alum, opps)
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain=sender.split('@')[-1])
    if unsubscribe_link(alum):
        msg['List-Unsubscribe'] = f'<{unsubscribe_link(alum)}>'
    msg.set_content(render_text(alum, opps, today))
    msg.add_alternative(render_html(alum, opps, today), subtype='html')
    return msg


def render_index(results, today, mode):
    rows = []
    for r in results:
        alum, opps, file = r['alum'], r['opps'], r['file']
        name = escape(alum.get('full_name') or '(no name)')
        if file:
            eml = file[:-len('.html')] + '.eml'
            status = (f'<a href="{escape(file)}">{escape(r["subject"])}</a> '
                      f'<span style="color:{MUTED}">(<a href="{escape(eml)}">.eml</a>)</span>')
        else:
            status = '<em>No matches. No digest would be sent.</em>'
        rows.append(f'<tr><td>{name}</td><td>{escape(alum.get("email") or "")}</td>'
                    f'<td style="text-align:center">{len(opps)}</td><td>{status}</td></tr>')
    made = sum(1 for r in results if r['file'])
    return f'''<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>Digest preview</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:32px;color:{NAVY}}}
table{{border-collapse:collapse;width:100%;max-width:900px}}td,th{{border-bottom:1px solid #E6E2EF;
padding:8px 10px;text-align:left;font-size:14px}}th{{background:{CREAM}}}a{{color:{PURPLE}}}</style>
</head><body><h1>Digest preview ({escape(mode)} data)</h1>
<p>Built {escape(today.isoformat())}. {made} of {len(results)} alumni would get a digest.
Nothing has been sent.</p>
<table><tr><th>Alum</th><th>Email</th><th>Matches</th><th>Digest</th></tr>{''.join(rows)}</table>
</body></html>
'''


# ---------- data sources ----------

def load_sample(today):
    data = json.loads((ROOT / 'data/sample_digest.json').read_text(encoding='utf-8'))
    opportunities = []
    for opp in data['opportunities']:
        days = opp.get('deadline_in_days')
        opportunities.append({**opp, 'deadline': None if days is None else today + timedelta(days=days)})
    return [(alum, rank(alum, opportunities, today)) for alum in data['alumni']]


def load_supabase():
    from .clients import SupabaseClient, settings
    db = SupabaseClient(settings())
    alumni = db.select('alumni_intake_responses?select=id,full_name,email,tags'
                       '&tags=not.is.null&order=full_name') or []
    results = []
    for alum in alumni:
        rows = db.rpc('top_opportunities_for_alum', {'p_alum_id': alum['id']}) or []
        for row in rows:
            row['deadline'] = date.fromisoformat(row['deadline']) if row.get('deadline') else None
            row['matched_tags'] = row.get('matched_tags') or []
        results.append((alum, rows))
    return results


# ---------- main ----------

def slug(text, fallback):
    plain = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    s = ''.join(c.lower() if c.isalnum() else '-' for c in plain).strip('-')
    return '-'.join(filter(None, s.split('-')))[:40] or fallback


def build(pairs, today, mode, out_dir=OUT_DIR):
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for n, (alum, opps) in enumerate(pairs, 1):
        entry = {'alum': alum, 'opps': opps, 'file': None, 'subject': ''}
        if opps:   # an empty digest is not worth an email
            base = f'{n:03d}-{slug(alum.get("full_name"), "alum")}'
            entry['subject'] = subject_line(alum, opps)
            (out_dir / f'{base}.html').write_text(render_html(alum, opps, today), encoding='utf-8')
            (out_dir / f'{base}.txt').write_text(
                f'To: {alum.get("email") or ""}\nSubject: {entry["subject"]}\n\n'
                + render_text(alum, opps, today), encoding='utf-8')
            (out_dir / f'{base}.eml').write_bytes(bytes(build_email(alum, opps, today)))
            entry['file'] = f'{base}.html'
        results.append(entry)
    (out_dir / 'index.html').write_text(render_index(results, today, mode), encoding='utf-8')
    return results


def main():
    parser = argparse.ArgumentParser(description='Build opportunity digest emails (does not send).')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--sample', action='store_true', help='Use data/sample_digest.json')
    source.add_argument('--supabase', action='store_true', help='Use real alumni from Supabase')
    args = parser.parse_args()
    today = date.today()
    try:
        pairs = load_sample(today) if args.sample else load_supabase()
    except (ValueError, OSError, ServiceError) as exc:   # missing keys, bad config, Supabase errors
        raise SystemExit(f'Stopped: {exc}')
    results = build(pairs, today, 'sample' if args.sample else 'Supabase')
    for r in results:
        name = r['alum'].get('full_name') or '(no name)'
        print(f'  {name}: {len(r["opps"])} match(es)' + ('' if r['file'] else ', skipped'))
    print(f'Open {OUT_DIR / "index.html"} to review. Nothing was sent.')


if __name__ == '__main__':
    main()
