"""Speaker Bureau search: a small password-protected web server.

What it does
  * Shows a sign-in page. After the staff password is entered it serves the search page.
  * Holds the Supabase secret key, so the key never reaches anyone's browser.
  * Calls the same database functions the command-line tool uses (ranked_speakers_search and
    speaker_filter_options) and looks up the travel-radius place on OpenStreetMap.

It needs no extra packages: only Python's standard library. Settings come from environment
variables (on Render these are typed into the dashboard, never saved in GitHub):
  STAFF_PASSWORD         the shared staff password (use a long one: 4 random words is good)
  SUPABASE_URL           e.g. https://abcdefgh.supabase.co
  SUPABASE_SERVICE_KEY   the service_role key (a secret: never put it in the repo)
  GEOCODER_USER_AGENT    optional: "YourApp/1.0 (you@example.org)" if place lookups are refused
  PORT                   set automatically by Render
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_HTML = (HERE / 'static' / 'app.html').read_text(encoding='utf-8')
ALUMNI_HTML = (HERE / 'static' / 'alumni.html').read_text(encoding='utf-8')

def _setting(name):
    """Read a setting and tidy it. Spaces, line breaks and quote marks pasted by accident are removed,
    including ones in the middle (keys and web addresses never contain any). The password is only
    trimmed at the ends, because a password may contain spaces."""
    value = os.environ.get(name, '').strip().strip('"\'').strip()
    return value if name == 'STAFF_PASSWORD' else ''.join(value.split())


PASSWORD = _setting('STAFF_PASSWORD')
SUPABASE_URL = _setting('SUPABASE_URL').rstrip('/')
if SUPABASE_URL and not SUPABASE_URL.startswith(('http://', 'https://')):
    SUPABASE_URL = 'https://' + SUPABASE_URL
SUPABASE_KEY = _setting('SUPABASE_SERVICE_KEY')
GEOCODER_URL = 'https://nominatim.openstreetmap.org/search'
GEOCODER_USER_AGENT = os.environ.get('GEOCODER_USER_AGENT') or 'TechnovationAlumniHub-speaker-search/1.0'
GEOCODER_BASE = os.environ.get('GEOCODER_URL') or GEOCODER_URL  # only changed by the automated tests

SESSION_SECONDS = 12 * 3600
MAX_BODY = 64 * 1024
COOKIE = 'sb_session'
CATEGORIES = (
    'industry', 'career_stage', 'project_domain', 'project_built_with', 'project_approach',
    'interests', 'skills', 'story_angles', 'speaking_types', 'audiences', 'expertise_areas',
    'story_formats', 'opportunities', 'interview_formats', 'interview_experience', 'regions',
    'speaker_interest', 'availability', 'languages', 'deep_experience', 'moderate_experience',
    'emerging_interest')


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- sessions and sign-in
def _key():
    return hashlib.sha256(('speaker-bureau-session:' + PASSWORD).encode()).digest()


def make_token(now=None):
    expires = str(int((now or time.time()) + SESSION_SECONDS))
    return expires + '.' + hmac.new(_key(), expires.encode(), hashlib.sha256).hexdigest()


def token_ok(token, now=None):
    try:
        expires, sig = (token or '').split('.', 1)
        good = hmac.new(_key(), expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, good) and int(expires) > (now or time.time())
    except (ValueError, AttributeError):
        return False


def password_ok(candidate):
    a = hashlib.sha256((candidate or '').encode()).digest()
    b = hashlib.sha256(PASSWORD.encode()).digest()
    return hmac.compare_digest(a, b)


_fail_lock = threading.Lock()
_fails = {}      # who -> [times of recent wrong passwords]
_all_fails = []  # every recent wrong password, from anyone
WINDOW, PER_WHO, IN_TOTAL = 15 * 60, 5, 40


def _recent(times, now):
    return [t for t in times if now - t < WINDOW]


def locked_out(who, now=None):
    now = now or time.time()
    with _fail_lock:
        _fails[who] = _recent(_fails.get(who, []), now)
        _all_fails[:] = _recent(_all_fails, now)
        return len(_fails[who]) >= PER_WHO or len(_all_fails) >= IN_TOTAL


def record_fail(who, now=None):
    now = now or time.time()
    with _fail_lock:
        _fails.setdefault(who, []).append(now)
        _all_fails.append(now)


# ---------------------------------------------------------------- Supabase and place lookup
def rpc(name, params):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ApiError(503, 'The server is missing its Supabase settings')
    headers = {'apikey': SUPABASE_KEY, 'Content-Type': 'application/json'}
    if not SUPABASE_KEY.startswith('sb_'):
        # The older service_role key is a long token starting "eyJ" and is also sent as a Bearer token.
        # Newer "sb_secret_..." keys must go in the apikey header only.
        headers['Authorization'] = 'Bearer ' + SUPABASE_KEY
    request = urllib.request.Request(
        SUPABASE_URL + '/rest/v1/rpc/' + name, data=json.dumps(params).encode(), method='POST', headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print('Supabase error', e.code, name, e.read()[:300].decode('utf-8', 'replace'), file=sys.stderr)
        hint = {401: ' - the key was refused, check SUPABASE_SERVICE_KEY', 403: ' - the key was refused, check SUPABASE_SERVICE_KEY',
                404: ' - the search function was not found, check SUPABASE_URL, and that 005_speaker_search.sql (and alumni_directory.sql for All Alumni) was run'}.get(e.code, '')
        raise ApiError(502, 'The database returned error %d%s' % (e.code, hint)) from None
    except OSError as e:  # network problems: wrong address, no connection, certificate trouble
        print('Could not reach Supabase for', name, '-', type(e).__name__, getattr(e, 'reason', ''), file=sys.stderr)
        raise ApiError(502, 'Could not reach the database - check SUPABASE_URL') from None
    except ValueError:  # something in a setting is not allowed in a web request. Never print it: it may hold the key.
        print('A Supabase setting has an invalid character, so the request was not sent. Re-enter SUPABASE_URL and SUPABASE_SERVICE_KEY.', file=sys.stderr)
        raise ApiError(502, 'A Supabase setting has an invalid character - re-enter SUPABASE_URL and SUPABASE_SERVICE_KEY') from None


_options_cache = {'at': 0, 'rows': None}


def filter_options():
    if _options_cache['rows'] is None or time.time() - _options_cache['at'] > 300:
        _options_cache['rows'] = rpc('speaker_filter_options', {}) or []
        _options_cache['at'] = time.time()
    return _options_cache['rows']


_alumni_cache = {'at': 0, 'rows': None}


def alumni_directory():
    # Everyone, not only speakers. Cached for 5 minutes so each page load does not hit the database.
    if _alumni_cache['rows'] is None or time.time() - _alumni_cache['at'] > 300:
        _alumni_cache['rows'] = rpc('alumni_directory', {}) or []
        _alumni_cache['at'] = time.time()
    return _alumni_cache['rows']


_geo_lock = threading.Lock()
_geo_cache = {}
_geo_last = [0.0]


def geocode(place):
    """(lat, lng) for "City, State, Country", or None if OpenStreetMap does not know it."""
    key = ' '.join(place.lower().split())
    with _geo_lock:
        if key in _geo_cache:
            return _geo_cache[key]
        wait = 1.1 - (time.time() - _geo_last[0])  # OpenStreetMap allows about one lookup per second
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.time()
        url = GEOCODER_BASE + '?' + urllib.parse.urlencode({'q': place, 'format': 'json', 'limit': 1})
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': GEOCODER_USER_AGENT}), timeout=15) as r:
                data = json.loads(r.read().decode('utf-8'))
            point = (float(data[0]['lat']), float(data[0]['lon'])) if data else None
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            raise ApiError(502, 'Could not reach the place lookup (OpenStreetMap)') from None
        if len(_geo_cache) < 2000:
            _geo_cache[key] = point
        return point


def run_search(body):
    tags = body.get('tags', [])
    if not isinstance(tags, list) or len(tags) > 50:
        raise ApiError(400, 'Pick at most 50 tags')
    picks = []
    for t in tags:
        if (not isinstance(t, dict) or t.get('category') not in CATEGORIES or not isinstance(t.get('tag'), str)
                or not 1 <= len(t['tag'].strip()) <= 200):
            raise ApiError(400, 'Unknown tag category or empty tag')
        picks.append({'category': t['category'], 'tag': t['tag'].strip()})
    place = body.get('place') or ''
    if not isinstance(place, str) or len(place) > 200:
        raise ApiError(400, 'That place name is too long')
    place = place.strip()
    radius = body.get('radius', 100)
    if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not 1 <= radius <= 5000:
        raise ApiError(400, 'Use a radius of 1-5000 miles')
    include_virtual = body.get('virtual', True)
    if not isinstance(include_virtual, bool):
        raise ApiError(400, 'virtual must be true or false')
    params = {'p_tags': picks, 'p_limit': 50, 'p_speakers_only': True}
    place_found = True
    if place:
        point = geocode(place)
        if point:
            params.update({'p_lat': point[0], 'p_lng': point[1], 'p_radius_miles': radius,
                           'p_place_label': place, 'p_include_virtual': include_virtual})
        else:
            place_found = False
    if not picks and place_found and not place:
        return {'rows': [], 'placeFound': True}
    if not picks and not place_found:
        return {'rows': [], 'placeFound': False}
    return {'rows': rpc('ranked_speakers_search', params) or [], 'placeFound': place_found}


# ---------------------------------------------------------------- pages
LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Speaker Bureau sign in</title>
<style nonce="__NONCE__">
:root { color-scheme: light dark; --bg:#f4f1fb; --card:#fff; --ink:#1c1248; --soft:#4d4670; --line:rgba(28,18,72,.25); --accent:#6f52c8; --err:#a3201f; }
@media (prefers-color-scheme: dark) { :root { --bg:#130e2a; --card:#1d1740; --ink:#ece8fb; --soft:#b4add8; --line:rgba(236,232,251,.4); --accent:#9b84ec; --err:#ff9b97; } }
* { box-sizing: border-box; } body { margin:0; min-height:100vh; display:grid; place-items:center; padding:16px; background:var(--bg); color:var(--ink); font:16px/1.5 system-ui, -apple-system, 'Segoe UI', sans-serif; }
main { width:100%; max-width:400px; background:var(--card); border-radius:24px; padding:32px; }
h1 { margin:0 0 4px; font:400 30px/1.15 Georgia, serif; } p { margin:0 0 20px; color:var(--soft); font-size:14.5px; }
label { display:block; font-weight:700; font-size:14px; margin-bottom:6px; }
input { width:100%; min-height:48px; padding:0 14px; border-radius:12px; border:1px solid var(--line); background:transparent; color:var(--ink); font:inherit; }
button { margin-top:16px; width:100%; min-height:48px; border:0; border-radius:12px; background:var(--accent); color:#fff; font-weight:700; font-size:15px; font-family:inherit; cursor:pointer; }
:focus-visible { outline:3px solid var(--accent); outline-offset:2px; } .err { color:var(--err); font-weight:600; margin:12px 0 0; }
</style></head><body><main>
<h1>Speaker Bureau</h1><p>Technovation Alumni Hub, staff only.</p>
<form method="post" action="/login">
<label for="pw">Staff password</label>
<input id="pw" name="password" type="password" autocomplete="current-password" required autofocus>
__ERROR__
<button type="submit">Sign in</button></form></main></body></html>"""


def csp(nonce):
    return ("default-src 'none'; script-src 'nonce-%s'; style-src 'nonce-%s' https://fonts.googleapis.com; "
            "style-src-attr 'unsafe-inline'; font-src https://fonts.gstatic.com; img-src 'self' data:; "
            "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'" % (nonce, nonce))


# ---------------------------------------------------------------- request handler
class Handler(BaseHTTPRequestHandler):
    server_version = 'SpeakerBureau'
    sys_version = ''

    def log_message(self, fmt, *args):  # one short line per request, no bodies
        sys.stderr.write('%s %s\n' % (self.command, self.path.split('?')[0]))

    def who(self):
        forwarded = (self.headers.get('X-Forwarded-For') or '').split(',')[-1].strip()
        return forwarded or self.client_address[0]

    def secure(self):
        return (self.headers.get('X-Forwarded-Proto') or '').lower() == 'https'

    def authed(self):
        for part in (self.headers.get('Cookie') or '').split(';'):
            name, _, value = part.strip().partition('=')
            if name == COOKIE:
                return token_ok(value)
        return False

    def send(self, status, body, ctype, extra=None, nonce=None):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        if nonce:
            self.send_header('Content-Security-Policy', csp(nonce))
        if self.secure():
            self.send_header('Strict-Transport-Security', 'max-age=31536000')
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, payload, extra=None):
        self.send(status, json.dumps(payload), 'application/json; charset=utf-8', extra)

    def html(self, status, template, **swaps):
        nonce = secrets.token_urlsafe(16)
        page = template
        for k, v in swaps.items():
            page = page.replace(k, v)
        self.send(status, page.replace('__NONCE__', nonce), 'text/html; charset=utf-8', nonce=nonce)

    def read_body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY:
            raise ApiError(413, 'Request too large')
        return self.rfile.read(length)

    def cookie_header(self, value, max_age):
        flags = 'Path=/; HttpOnly; SameSite=Strict; Max-Age=%d' % max_age + ('; Secure' if self.secure() else '')
        return ('Set-Cookie', '%s=%s; %s' % (COOKIE, value, flags))

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/healthz':
            return self.send(200, 'ok', 'text/plain')
        if path == '/favicon.ico':
            return self.send(204, b'', 'image/x-icon')
        if path == '/api/options':
            return self.api(lambda: {'options': filter_options()})
        if path == '/api/alumni':
            return self.api(lambda: {'alumni': alumni_directory()})
        if path == '/alumni':
            if self.authed():
                return self.html(200, ALUMNI_HTML)
            return self.html(200, LOGIN_PAGE, __ERROR__='')
        if path == '/':
            if self.authed():
                return self.html(200, APP_HTML)
            return self.html(200, LOGIN_PAGE, __ERROR__='')
        self.send(404, 'Not found', 'text/plain')

    def do_POST(self):
        path = self.path.split('?')[0]
        try:
            if path == '/login':
                return self.login()
            if path == '/logout':
                self.read_body()
                return self.json(200, {'ok': True}, [self.cookie_header('', 0)])
            if path == '/api/search':
                if (self.headers.get('Content-Type') or '').split(';')[0].strip() != 'application/json':
                    raise ApiError(415, 'Send JSON')
                raw = self.read_body()
                try:
                    body = json.loads(raw.decode('utf-8'))
                except ValueError:
                    raise ApiError(400, 'Could not read that request') from None
                if not isinstance(body, dict):
                    raise ApiError(400, 'Could not read that request')
                return self.api(lambda: run_search(body))
            self.send(404, 'Not found', 'text/plain')
        except ApiError as e:
            self.json(e.status, {'error': e.message})

    def login(self):
        raw = self.read_body().decode('utf-8', 'replace')
        candidate = (urllib.parse.parse_qs(raw).get('password') or [''])[0]
        who = self.who()
        if locked_out(who):
            return self.html(429, LOGIN_PAGE, __ERROR__='<p class="err" role="alert">Too many wrong passwords. Wait 15 minutes and try again.</p>')
        if password_ok(candidate):
            self.send_response(303)
            self.send_header('Location', '/')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            k, v = self.cookie_header(make_token(), SESSION_SECONDS)
            self.send_header(k, v)
            self.end_headers()
            return
        record_fail(who)
        time.sleep(0.5)
        self.html(401, LOGIN_PAGE, __ERROR__='<p class="err" role="alert">That password is not right.</p>')

    def api(self, fn):
        if not self.authed():
            return self.json(401, {'error': 'Please sign in'})
        try:
            self.json(200, fn())
        except ApiError as e:
            self.json(e.status, {'error': e.message})
        except Exception:  # never show internals to the browser
            import traceback
            traceback.print_exc()
            self.json(500, {'error': 'Something went wrong on the server'})


def main():
    if len(PASSWORD) < 10:
        sys.exit('Set STAFF_PASSWORD to something at least 10 characters long.')
    if not SUPABASE_URL or not SUPABASE_KEY:
        print('Warning: SUPABASE_URL or SUPABASE_SERVICE_KEY is not set, so searches will fail.', file=sys.stderr)
    else:
        print('Supabase settings found: URL', SUPABASE_URL, '| key starts', SUPABASE_KEY[:6], '| key length', len(SUPABASE_KEY), file=sys.stderr)
    port = int(os.environ.get('PORT', '8000'))
    print('Speaker Bureau search listening on port', port, file=sys.stderr)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()


if __name__ == '__main__':
    main()
