"""Email each alum their opportunity digest (top matches from the opportunities table).

Run:  python3 -m alumni_tagger.send_digest                         (preview: who would get what)
      python3 -m alumni_tagger.send_digest --test-to you@x.com     (send copies to YOU only)
      python3 -m alumni_tagger.send_digest --send --only a@x.com   (really send, one alum)
      python3 -m alumni_tagger.send_digest --send                  (really send to everyone due)

Nothing is emailed without --send or --test-to.
  --test-to EMAILS   sends every planned digest to these addresses instead of the alumni, with
                     "[TEST for Name]" in the subject. Nothing is recorded, so real sends still happen.
  --only EMAILS      limits real sends to these alumni.
  --max N            most emails in one run (default 50).
  --min-days N       skip anyone who got a digest in the last N days (default 13).

Each digest is built by alumni_tagger/digest.py (the same email as the preview). Opportunities
an alum was already sent are left out of their next digest; alumni with no new matches get
nothing. Every email has an unsubscribe link (a mailto to the sending address, also in the
List-Unsubscribe header). Before sending, the inbox is checked for emails with "unsubscribe" in
the subject, and those senders are added to digest_unsubscribes and skipped from then on.
Each real send is saved to digest_deliveries right away. Needs sql/011_digest_sending.sql.

Sends through Gmail. Settings (.env.local, config.local.json or GitHub secrets):
  DIGEST_FROM_EMAIL       the Gmail address, e.g. technovationpilot@gmail.com
  DIGEST_EMAIL_PASSWORD   that account's 16-character app password (not its normal password)
"""
from __future__ import annotations

import argparse
import imaplib
import smtplib
import time
from datetime import date, datetime, timedelta, timezone
from email import message_from_bytes
from email.utils import parseaddr
from urllib import parse

from . import digest
from .clients import ServiceError, SupabaseClient, json_request, require, settings

SMTP_HOST, SMTP_PORT = 'smtp.gmail.com', 465
IMAP_HOST = 'imap.gmail.com'
DEFAULT_MAX = 50
DEFAULT_MIN_DAYS = 13
PAUSE = 1.0                # seconds between emails
INBOX_DAYS = 90            # how far back to look for unsubscribe requests


# ---------- who gets what ----------

def opp_key(opp):
    return str(opp.get('id') or opp.get('url') or opp.get('title') or '')


def clean_emails(value):
    emails = [e.strip().lower() for e in (value or '').split(',') if e.strip()]
    bad = [e for e in emails if '@' not in e or ' ' in e]
    if bad:
        raise ValueError('Not an email address: ' + ', '.join(bad))
    return emails


def plan(pairs, unsubscribed, deliveries, now, min_days=DEFAULT_MIN_DAYS, only=None):
    """[(alum, opportunities to send, why skipped or '')] for every alum, in order."""
    by_alum = {}
    for d in deliveries:
        by_alum.setdefault(d['response_id'], []).append(d)
    out = []
    for alum, opps in pairs:
        email = (alum.get('email') or '').strip().lower()
        past = by_alum.get(alum.get('id'), [])
        sent_keys = {k for d in past for k in (d.get('opportunity_keys') or [])}
        fresh = [o for o in opps if opp_key(o) not in sent_keys]
        last = max((parse_time(d['sent_at']) for d in past), default=None)
        if not email or '@' not in email:
            why = 'no email address'
        elif only is not None and email not in only:
            why = 'not in --only'
        elif email in unsubscribed:
            why = 'unsubscribed'
        elif last and now - last < timedelta(days=min_days):
            why = f'got one {(now - last).days} day(s) ago'
        elif not opps:
            why = 'no matching opportunities'
        elif not fresh:
            why = 'no new matches since the last digest'
        else:
            why = ''
        out.append((alum, fresh if not why else [], why))
    return out


def parse_time(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


# ---------- Supabase ----------

def load_unsubscribed(db):
    return {r['email'] for r in db.select('digest_unsubscribes?select=email') or []}


def save_unsubscribed(db, emails):
    if not emails:
        return
    headers = {**db.headers, 'Prefer': 'return=minimal,resolution=ignore-duplicates'}
    json_request(db.url + '/rest/v1/digest_unsubscribes?on_conflict=email', headers,
                 [{'email': e} for e in sorted(emails)], service='Supabase')


def load_deliveries(db, ids):
    if not ids:
        return []
    found = []
    ids = sorted(ids)
    for i in range(0, len(ids), 100):
        id_list = ','.join(ids[i:i + 100])
        found += db.select('digest_deliveries?select=response_id,opportunity_keys,sent_at'
                           f'&response_id=in.({parse.quote(id_list, safe=",-")})') or []
    return found


def record_delivery(db, alum, opps, message_id):
    json_request(db.url + '/rest/v1/digest_deliveries', {**db.headers, 'Prefer': 'return=minimal'},
                 {'response_id': alum['id'], 'email': alum['email'].strip().lower(),
                  'opportunity_keys': [opp_key(o) for o in opps], 'message_id': message_id},
                 service='Supabase')


# ---------- Gmail ----------

def inbox_unsubscribes(user, password, imap_factory=imaplib.IMAP4_SSL, today=None):
    """Senders of recent inbox emails whose subject contains "unsubscribe"."""
    since = ((today or date.today()) - timedelta(days=INBOX_DAYS)).strftime('%d-%b-%Y')
    box = imap_factory(IMAP_HOST)
    try:
        box.login(user, password)
        box.select('INBOX', readonly=True)
        status, data = box.search(None, 'SINCE', since, 'SUBJECT', '"unsubscribe"')
        found = set()
        for num in (data[0].split() if status == 'OK' and data and data[0] else []):
            status, parts = box.fetch(num, '(BODY.PEEK[HEADER.FIELDS (FROM)])')
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), b'')
            addr = parseaddr(message_from_bytes(raw).get('From', ''))[1].strip().lower()
            if '@' in addr and addr != user.lower():
                found.add(addr)
        return found
    finally:
        try:
            box.logout()
        except Exception:
            pass


class Mailer:
    def __init__(self, user, password, smtp_factory=smtplib.SMTP_SSL, sleep=time.sleep):
        self.user, self.password, self.smtp_factory, self.sleep = user, password, smtp_factory, sleep
        self.server, self.sent = None, 0

    def __enter__(self):
        self.server = self.smtp_factory(SMTP_HOST, SMTP_PORT, timeout=60)
        self.server.login(self.user, self.password)
        return self

    def send(self, msg):
        if self.sent:
            self.sleep(PAUSE)
        self.server.send_message(msg)
        self.sent += 1

    def __exit__(self, *exc):
        try:
            self.server.quit()
        except Exception:
            pass


# ---------- run ----------

def run(send=False, test_to=None, only=None, max_emails=DEFAULT_MAX, min_days=DEFAULT_MIN_DAYS,
        check_inbox=True, today=None, now=None, db=None, mailer=None, pairs=None):
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    config = settings()
    emailing = send or bool(test_to)
    if emailing:
        require(config, 'DIGEST_FROM_EMAIL', 'DIGEST_EMAIL_PASSWORD')
    # A preview works without the Gmail settings; it just can't check the inbox then.
    check_inbox = check_inbox and bool(config.get('DIGEST_FROM_EMAIL') and config.get('DIGEST_EMAIL_PASSWORD'))
    sender = config.get('DIGEST_FROM_EMAIL') or digest.FROM_ADDRESS
    digest.UNSUBSCRIBE_EMAIL = sender if config.get('DIGEST_FROM_EMAIL') else ''
    db = db or SupabaseClient(config)

    if check_inbox:
        new = inbox_unsubscribes(sender, config['DIGEST_EMAIL_PASSWORD'], today=today)
        save_unsubscribed(db, new)
        if new:
            print(f'Unsubscribe requests in the inbox: {len(new)} (saved)')
    unsubscribed = load_unsubscribed(db)
    pairs = pairs if pairs is not None else digest.load_supabase()
    deliveries = load_deliveries(db, {a['id'] for a, _ in pairs if a.get('id')})
    plans = plan(pairs, unsubscribed, deliveries, now, min_days, set(only) if only else None)
    due = [(a, o) for a, o, why in plans if not why][:max_emails]

    for alum, opps, why in plans:
        name = alum.get('full_name') or '(no name)'
        print(f'  {"SEND" if not why else "skip"}  {name} <{alum.get("email") or "?"}>: '
              + (f'{len(opps)} opportunit{"y" if len(opps) == 1 else "ies"}' if not why else why))
    waiting = sum(1 for _, _, why in plans if not why)
    print(f'{waiting} alumni due a digest' + (f' (sending the first {max_emails})' if waiting > max_emails else ''))

    if not emailing:
        print('Preview only: nothing was sent. Add --test-to YOUR_EMAIL to send yourself copies, '
              'or --send to email alumni.')
        return 0
    if not due:
        print('Nothing to send.')
        return 0
    mailer = mailer or Mailer(sender, config['DIGEST_EMAIL_PASSWORD'])
    sent = 0
    with mailer:
        for alum, opps in due:
            if test_to:
                for to in test_to:
                    name = alum.get('full_name') or alum.get('email')
                    mailer.send(digest.build_email(alum, opps, today, sender, to=to,
                                                   subject_prefix=f'[TEST for {name}] '))
                    sent += 1
                continue
            msg = digest.build_email(alum, opps, today, sender)
            mailer.send(msg)
            record_delivery(db, alum, opps, msg['Message-ID'])   # saved at once: no double sends
            sent += 1
    print(f'Sent {sent} email(s)' + (' to the test address(es); nothing recorded.' if test_to else '.'))
    return 0


def main():
    parser = argparse.ArgumentParser(description='Email alumni their opportunity digest.')
    parser.add_argument('--send', action='store_true', help='Really email the alumni')
    parser.add_argument('--test-to', default='', help='Send copies only to these addresses (comma-separated)')
    parser.add_argument('--only', default='', help='Only these alumni emails (comma-separated)')
    parser.add_argument('--max', type=int, default=DEFAULT_MAX, help='Most emails this run (default 50)')
    parser.add_argument('--min-days', type=int, default=DEFAULT_MIN_DAYS,
                        help='Skip alumni who got a digest this many days ago or less (default 13)')
    parser.add_argument('--no-inbox', action='store_true', help="Don't check the inbox for unsubscribes")
    args = parser.parse_args()
    try:
        if args.send and args.test_to:
            raise ValueError('Use --send or --test-to, not both')
        if not 1 <= args.max <= 500:
            raise ValueError('--max must be 1-500 (Gmail allows about 500 emails a day)')
        raise SystemExit(run(args.send, clean_emails(args.test_to), clean_emails(args.only) or None,
                             args.max, args.min_days, not args.no_inbox))
    except (ServiceError, ValueError) as exc:
        raise SystemExit(f'Stopped: {exc}')
    except smtplib.SMTPAuthenticationError:
        raise SystemExit('Stopped: Gmail refused the login. Check DIGEST_FROM_EMAIL and that '
                         'DIGEST_EMAIL_PASSWORD is the 16-character app password, without spaces.')
    except (smtplib.SMTPException, imaplib.IMAP4.error, OSError) as exc:
        raise SystemExit(f'Stopped: email problem ({type(exc).__name__}): {exc}')


if __name__ == '__main__':
    main()
