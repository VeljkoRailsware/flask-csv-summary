import base64
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest

from core import Mailer, Problem, Settings, Store, attachment


def test_sandbox_and_live_http_payload_and_attachment(tmp_path):
    from io import BytesIO
    import json
    for mode in ['sandbox','production']:
        captured={}
        def send(request,timeout):
            captured.update(url=request.full_url,headers=request.headers,body=json.loads(request.data),timeout=timeout)
            return BytesIO(b'{"success":true,"message_ids":["id-1"]}')
        settings=Settings(database=str(tmp_path/'app.sqlite'),mail_mode=mode,mail_from='sender@example.com',sandbox_token='sandbox-token',sandbox_id='123',production_token='production-token')
        result=Mailer(settings,send).send({'to':'reader@example.com','subject':'Receipt','text':'<unsafe>','message_id':'<ref@example.com>','attachment':attachment(b'a,b\r\n1,2\r\n','test.csv','text/csv')})
        assert result=='accepted'
        assert captured['url']==('https://send.api.mailtrap.io/api/send' if mode=='production' else 'https://sandbox.api.mailtrap.io/api/send/123')
        assert captured['headers']['Authorization']=='Bearer '+mode+'-token'
        body=captured['body'];assert body['custom_variables']['request_id']=='ref'
        assert body['category'] and body['headers']['X-Request-Reference']=='<ref@example.com>'
        assert '&lt;unsafe&gt;' in body['html']
        assert base64.b64decode(body['attachments'][0]['content'])==b'a,b\r\n1,2\r\n'
        assert body['attachments'][0]['disposition']=='attachment'


def test_missing_credentials_never_open_a_connection():
    def unexpected(*args, **kwargs): raise AssertionError('Network must not be used')
    with pytest.raises(Problem, match='credentials'):
        Mailer(Settings(mail_from='sender@example.com'), unexpected).send({'to':'reader@example.com','subject':'Test','text':'Body','message_id':'<test@example.com>'})


def test_preview_contains_mime_attachment_and_production_refuses_it(tmp_path):
    settings = Settings(database=str(tmp_path/'app.sqlite'), mail_mode='log')
    payload = {'to':'reader@example.com','subject':'Preview','text':'Body','message_id':'<test@example.com>','attachment':attachment(b'content','sample.txt','text/plain')}
    assert Mailer(settings).send(payload) == 'logged'
    message = BytesParser(policy=policy.default).parsebytes(next((tmp_path/'mail-preview').glob('*.eml')).read_bytes())
    assert message['To'] == 'reader@example.com'
    assert list(message.iter_attachments())[0].get_payload(decode=True) == b'content'
    settings.environment = 'production'
    with pytest.raises(Problem, match='development'):
        Mailer(settings).send(payload)


def test_http_errors_malformed_results_and_timeout_remain_visible():
    from io import BytesIO
    from urllib.error import HTTPError
    import json
    settings=Settings(mail_from='sender@example.com',sandbox_token='token',sandbox_id='123')
    payload={'to':'reader@example.com','subject':'Test','text':'Body','message_id':'<ref@example.com>'}
    for status in [400,401,422,429,408,500,503]:
        def send(*args,**kwargs):raise HTTPError('https://provider',status,'error',{},None)
        with pytest.raises(Problem) as error:Mailer(settings,send).send(payload)
        assert error.value.uncertain==(status>=500 or status==408)
    for body in [{},{'success':True},{'success':True,'message_ids':[]},None,{'success':False,'message_ids':['partial']}]:
        with pytest.raises(Problem) as error:Mailer(settings,lambda *a,**kw:BytesIO(json.dumps(body).encode())).send(payload)
        assert error.value.uncertain is True
    def timeout(*args,**kwargs):raise TimeoutError()
    with pytest.raises(Problem) as error:Mailer(settings,timeout).send(payload)
    assert error.value.uncertain is True


def test_email_failure_preserves_request_and_explicit_retry_can_succeed(tmp_path):
    class Transport:
        failed=True
        def send(self,payload):
            if self.failed: raise Problem('missing credential')
            return 'accepted'
    transport=Transport()
    store=Store(Settings(database=str(tmp_path/'app.sqlite')),transport)
    result=store.save({'example':'kept'},{'to':'reader@example.com'},'client')
    assert result['email_status']=='failed'
    assert len(store.records())==1
    transport.failed=False
    assert store.retry()==1
    assert store.result(result['id'])['email_status']=='accepted'
    assert store.retry()==0


def test_unknown_and_interrupted_messages_are_not_retried(tmp_path):
    class Transport:
        calls=0
        def send(self,payload):
            self.calls+=1
            error=Problem('timeout');error.uncertain=True;raise error
    transport=Transport();store=Store(Settings(database=str(tmp_path/'app.sqlite')),transport)
    result=store.save({},{'to':'reader@example.com'},'client')
    assert result['email_status']=='unknown'
    assert store.retry()==0 and transport.calls==1
    with store.db() as db:db.execute("UPDATE outbox SET status='sending'")
    assert store.retry()==0 and transport.calls==1


def test_recipient_limit_is_persistent_and_does_not_save_a_fourth_request(tmp_path):
    class Transport:
        def send(self,payload):return 'accepted'
    settings=Settings(database=str(tmp_path/'app.sqlite'))
    for i in range(3):Store(settings,Transport()).save({},{'to':'reader@example.com'},str(i))
    store=Store(settings,Transport())
    with pytest.raises(Problem) as error:store.save({},{'to':'READER@example.com'},'different-client')
    assert error.value.status==429 and len(store.records())==3


def test_operator_credentials_and_origin_checks():
    settings=Settings(admin_password='a-long-demo-password')
    with pytest.raises(Problem) as error:settings.authorize('')
    assert error.value.status==401
    settings.authorize('Basic '+base64.b64encode(b'admin:a-long-demo-password').decode())
    with pytest.raises(Problem):settings.authorize('Basic invalid@@')
    with pytest.raises(Problem):settings.check_origin('https://untrusted.example')
    settings.check_origin(settings.app_url)
