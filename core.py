"""Small SQLite store and HTTP email transport used by this application."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError
from html import escape
from uuid import uuid4

from dotenv import load_dotenv


class Problem(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Settings:
    database: str = '.data/app.sqlite'
    app_url: str = 'http://localhost:8000'
    admin_password: str = ''
    operator_email: str = ''
    mail_mode: str = 'sandbox'
    mail_from: str = ''
    sandbox_token: str = ''
    sandbox_id: str = ''
    production_token: str = ''
    environment: str = 'development'

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        return cls(**{field: os.getenv(variable, default) for field, variable, default in [
            ('database', 'DB_PATH', '.data/app.sqlite'),
            ('app_url', 'APP_URL', 'http://localhost:8000'),
            ('admin_password', 'ADMIN_PASSWORD', ''),
            ('operator_email', 'OPERATOR_EMAIL', ''),
            ('mail_mode', 'MAIL_MODE', 'sandbox'),
            ('mail_from', 'MAIL_FROM', ''),
            ('sandbox_token', 'MAILTRAP_SANDBOX_TOKEN', ''),
            ('sandbox_id', 'MAILTRAP_INBOX_ID', ''),
            ('production_token', 'MAILTRAP_PRODUCTION_TOKEN', ''),
            ('environment', 'APP_ENV', 'development'),
        ]})

    def origin(self):
        parsed = urlsplit(self.app_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.username:
            raise Problem('APP_URL must be an HTTP(S) origin without a path.', 503)
        return self.app_url.rstrip('/')

    def authorize(self, header: str):
        if len(self.admin_password) < 16:
            raise Problem('Set ADMIN_PASSWORD to a random value of at least 16 characters.', 503)
        expected = ('admin:' + self.admin_password).encode()
        try:
            scheme, token = header.split(' ', 1)
            supplied = base64.b64decode(token, validate=True) if scheme.lower() == 'basic' else b''
        except (ValueError, TypeError):
            supplied = b''
        if not hmac.compare_digest(hashlib.sha256(supplied).digest(), hashlib.sha256(expected).digest()):
            raise Problem('Operator sign-in required.', 401)

    def check_origin(self, origin: str | None):
        if origin is not None and origin != self.origin():
            raise Problem('Open the form at the configured APP_URL.', 403)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class Mailer:
    def __init__(self, settings: Settings, http_open=None):
        self.settings = settings
        self.http_open = http_open or build_opener(NoRedirect()).open

    def send(self, payload: dict):
        s = self.settings
        if s.mail_mode not in ('sandbox', 'production', 'log'):
            raise Problem('MAIL_MODE must be sandbox, production, or log.')
        if s.mail_mode == 'log' and s.environment != 'development':
            raise Problem('Log mode is available only with APP_ENV=development.')
        from email_validator import validate_email, EmailNotValidError
        try:
            validate_email(payload['to'], check_deliverability=False)
            if s.mail_mode != 'log':
                validate_email(s.mail_from, check_deliverability=False)
        except EmailNotValidError as error:
            raise Problem('Configure valid sender and recipient addresses.') from error
        message = EmailMessage(policy=SMTP)
        message['From'] = s.mail_from or 'preview@example.com'
        message['To'] = payload['to']
        message['Subject'] = payload['subject']
        message['Message-ID'] = payload['message_id']
        message.set_content(payload['text'])
        if payload.get('html'):
            message.add_alternative(payload['html'], subtype='html')
        if payload.get('inline'):
            part=payload['inline']
            message.get_payload()[-1].add_related(base64.b64decode(part['data']), maintype='image', subtype='png', cid='<resize-preview>', filename=part['name'])
        if payload.get('attachment'):
            attachment = payload['attachment']
            major, minor = attachment['type'].split('/')
            message.add_attachment(base64.b64decode(attachment['data']), maintype=major, subtype=minor, filename=attachment['name'])
        if s.mail_mode == 'log':
            folder = Path(s.database).parent / 'mail-preview'
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            filename = folder / (uuid4().hex + '.eml')
            with os.fdopen(os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
                handle.write(message.as_bytes())
            return 'logged'
        token = s.production_token if s.mail_mode == 'production' else s.sandbox_token
        if not token:
            raise Problem('Email API credentials for the selected mode are missing.')
        if s.mail_mode == 'sandbox' and not (s.sandbox_id.isascii() and s.sandbox_id.isdecimal()):
            raise Problem('Configure the numeric MAILTRAP_INBOX_ID.')
        url = 'https://send.api.mailtrap.io/api/send' if s.mail_mode == 'production' else f'https://sandbox.api.mailtrap.io/api/send/{s.sandbox_id}'
        body = {
            'from': {'email': s.mail_from}, 'to': [{'email': payload['to']}],
            'subject': payload['subject'], 'text': payload['text'],
            'html': payload.get('html') or '<pre>'+escape(payload['text'])+'</pre>',
            'category': 'csv.summary',
            'custom_variables': {'request_id': payload['message_id'].strip('<>').split('@')[0]},
            'headers': {'X-Request-Reference': payload['message_id']},
        }
        attachments=[]
        for key in ['attachment','inline']:
            if payload.get(key):
                part=payload[key]
                attachments.append({'content':part['data'],'filename':part['name'],'type':part['type'],
                    'disposition':'inline' if key=='inline' else 'attachment',
                    **({'content_id':'resize-preview'} if key=='inline' else {})})
        if attachments: body['attachments']=attachments
        request=Request(url,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':f'Bearer {token}'},method='POST')
        try:
            with self.http_open(request, timeout=15) as response:
                result=json.loads(response.read())
        except HTTPError as error:
            failure=Problem(f'Email provider returned HTTP {error.code}.')
            failure.uncertain=error.code>=500 or error.code==408 or error.code<400
            raise failure from error
        except Exception as error:
            failure=Problem('Send result unknown; inspect provider logs before retrying.')
            failure.uncertain=True
            raise failure from error
        if isinstance(result,dict) and result.get('success') is False and not result.get('message_ids'):
            raise Problem('Email provider rejected the message.')
        ids=result.get('message_ids') if isinstance(result,dict) else None
        if not isinstance(result,dict) or result.get('success') is not True or not isinstance(ids,list) or len(ids)!=1 or not isinstance(ids[0],str) or not ids[0].strip():
            failure=Problem('Provider acceptance could not be verified; inspect provider logs.')
            failure.uncertain=True
            raise failure
        return 'accepted'


def attachment(data: bytes, name: str, content_type: str):
    return {'data': base64.b64encode(data).decode(), 'name': name, 'type': content_type}


class Store:
    def __init__(self, settings: Settings, mailer=None):
        self.settings = settings
        self.mailer = mailer or Mailer(settings)
        Path(settings.database).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL,
                    asset BLOB, media_type TEXT, token_hash TEXT UNIQUE, expires REAL);
                CREATE TABLE IF NOT EXISTS outbox(
                    id TEXT PRIMARY KEY REFERENCES requests(id), payload TEXT NOT NULL,
                    status TEXT NOT NULL, error TEXT);
                CREATE TABLE IF NOT EXISTS limits(key TEXT PRIMARY KEY, count INTEGER, expires REAL);
            ''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.settings.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, data, payload, identity, asset=None, media_type=None, raw_token=None):
        identifier = uuid4().hex
        now = time.time()
        payload = {**payload, 'message_id': f'<{identifier}@demo.local>'}
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM limits WHERE expires <= ?', (now,))
            for value, limit in [(f'client:{identity}', 12), (f'recipient:{payload["to"].lower()}', 3)]:
                key = hashlib.sha256(value.encode()).hexdigest()
                row = db.execute('SELECT count FROM limits WHERE key=?', (key,)).fetchone()
                if row and row['count'] >= limit:
                    raise Problem('Too many requests. Try again after the one-hour window.', 429)
                db.execute('INSERT INTO limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now + 3600))
            token_hash = hashlib.sha256(raw_token.encode()).hexdigest() if raw_token else None
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?)', (identifier, json.dumps(data), now, asset, media_type, token_hash, now + 3600 if raw_token else None))
            db.execute('INSERT INTO outbox VALUES (?,?,?,NULL)', (identifier, json.dumps(payload), 'pending'))
        self.deliver(identifier)
        return self.result(identifier)

    def deliver(self, identifier):
        with self.db() as db:
            changed = db.execute("UPDATE outbox SET status='sending',error=NULL WHERE id=? AND status IN ('pending','failed')", (identifier,)).rowcount
            if not changed:
                return
            payload = json.loads(db.execute('SELECT payload FROM outbox WHERE id=?', (identifier,)).fetchone()['payload'])
        try:
            status = self.mailer.send(payload)
            if status not in ('accepted', 'logged'):
                raise RuntimeError('Invalid transport result')
            error = None
        except Exception as failure:
            status = 'unknown' if getattr(failure, 'uncertain', False) else 'failed'
            error = 'Send result unknown; inspect provider logs.' if status == 'unknown' else 'Sending failed; review configuration and retry explicitly.'
        with self.db() as db:
            db.execute('UPDATE outbox SET status=?,error=? WHERE id=?', (status, error, identifier))

    def result(self, identifier):
        with self.db() as db:
            row = db.execute('SELECT id,status FROM outbox WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise Problem('Request not found.', 404)
        return {'id': row['id'], 'email_status': row['status']}

    def records(self):
        with self.db() as db:
            return [dict(row) for row in db.execute('SELECT r.id,r.created,o.status,o.error FROM requests r JOIN outbox o ON r.id=o.id ORDER BY r.created DESC LIMIT 50')]

    def image(self, raw_token):
        with self.db() as db:
            row = db.execute('SELECT asset,media_type FROM requests WHERE token_hash=? AND expires>?', (hashlib.sha256(raw_token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise Problem('The image link is invalid or has expired.', 410)
        return bytes(row['asset']), row['media_type']

    def retry(self):
        with self.db() as db:
            ids = [row['id'] for row in db.execute("SELECT id FROM outbox WHERE status IN ('pending','failed')")]
        for identifier in ids:
            self.deliver(identifier)
        return len(ids)
