"""Standard-library HTTP clients. Secrets and response bodies are never logged."""
import json
import os
import socket
import time
from urllib import error, parse, request
from .core import ROOT


class ServiceError(RuntimeError):
    def __init__(self, service, status=None):
        self.service, self.status = service, status
        hint = {401: 'Check the API key.', 403: 'Check key permissions.',
                404: 'Check the model, URL, or SQL migration.',
                429: 'Check API quota/billing or retry later.',
                400: 'Check the request, model support, and database migration.'}.get(status, '')
        super().__init__(f'{service} request failed ({status or "network/timeout"}). {hint}'.strip())


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def json_request(url, headers, payload=None, method='POST', service='API', retries=0):
    opener = request.build_opener(NoRedirect)
    data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    for attempt in range(retries + 1):
        req = request.Request(url, data=data, method=method,
                              headers={**headers, 'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except error.HTTPError as exc:
            status = exc.code
            exc.close()
            if attempt < retries and status in {429, 500, 502, 503, 504}:
                time.sleep(2 ** attempt)
                continue
            raise ServiceError(service, status) from None
        except (error.URLError, TimeoutError, socket.timeout):
            # Do not replay ambiguous timeouts: an API call might already have been billed.
            raise ServiceError(service) from None
        except (ValueError, UnicodeError):
            raise ServiceError(service, 'invalid JSON') from None


def read_env_file(path):
    """NAME=value lines from a .env-style file; other lines are ignored, never executed."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            name, sep, value = line.strip().partition('=')
            if sep and name and not name.startswith('#'):
                values[name.strip().removeprefix('export ').strip()] = value.strip().strip('"\'')
    return values


def settings():
    """Environment variables win, then .env.local, then config.local.json."""
    path = ROOT / 'config.local.json'
    local = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    local.update(read_env_file(ROOT / '.env.local'))
    names = ['OPENAI_API_KEY', 'OPENAI_MODEL', 'SUPABASE_URL', 'SUPABASE_KEY',
             'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'ANTHROPIC_WORKSPACE_ID',
             'USAJOBS_API_KEY', 'USAJOBS_EMAIL', 'OPPORTUNITY_PROVIDER', 'OPPORTUNITY_MODEL']
    values = {k: os.environ.get(k, local.get(k, '')).strip() for k in names}
    values['OPENAI_MODEL'] = values['OPENAI_MODEL'] or 'gpt-4.1-mini'
    values['ANTHROPIC_MODEL'] = values['ANTHROPIC_MODEL'] or 'claude-opus-5-5'
    return values


def require(config, *names):
    missing = [k for k in names if not config.get(k)]
    if missing:
        raise ValueError('Missing ' + ', '.join(missing)
                         + '. Add it to .env.local or run: python3 -m alumni_tagger configure')


class OpenAIClient:
    def __init__(self, config):
        require(config, 'OPENAI_API_KEY')
        self.key = config['OPENAI_API_KEY']

    def __call__(self, payload):
        return json_request('https://api.openai.com/v1/responses',
                            {'Authorization': 'Bearer ' + self.key}, payload,
                            service='OpenAI', retries=1)


class SupabaseClient:
    def __init__(self, config):
        require(config, 'SUPABASE_URL', 'SUPABASE_KEY')
        url = parse.urlsplit(config['SUPABASE_URL'].rstrip('/'))
        if (url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.supabase.co')
                or url.username or url.password or url.path or url.query or url.fragment or url.port):
            raise ValueError('SUPABASE_URL must be https://YOUR-PROJECT.supabase.co')
        self.url = url.geturl()
        key = config['SUPABASE_KEY']
        if key.startswith('sb_publishable_'):
            raise ValueError('Use a server-side secret key, not a publishable key')
        self.headers = {'apikey': key}
        if not key.startswith('sb_'):
            self.headers['Authorization'] = 'Bearer ' + key

    def rpc(self, name, payload):
        return json_request(self.url + '/rest/v1/rpc/' + name, self.headers, payload,
                            service='Supabase')

    def select(self, query):
        return json_request(self.url + '/rest/v1/' + query, self.headers, method='GET',
                            service='Supabase')

    def check(self):
        return json_request(self.url + '/rest/v1/alumni_intake_responses?select=id&limit=1',
                            self.headers, method='GET', service='Supabase')
