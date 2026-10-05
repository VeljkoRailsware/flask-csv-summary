import base64
from io import BytesIO
import json

import pytest
from core import Settings, Problem
from app import create_app

class Transport:
    def __init__(self): self.messages=[]
    def send(self,payload): self.messages.append(payload); return 'accepted'

@pytest.fixture
def fixture(tmp_path):
    transport=Transport()
    settings=Settings(database=str(tmp_path/'app.sqlite'),admin_password='test-password-long-enough',operator_email='operator@example.com')
    app=create_app(settings,transport)
    app.config["TESTING"]=True
    client=app.test_client()
    return app,client,transport,settings

AUTH={'Authorization':'Basic '+base64.b64encode(b'admin:test-password-long-enough').decode()}

from app import summarize

def test_summary_counts_decimal_totals_and_ignores_mixed_columns():
    assert summarize(b'item,quantity,amount\nNotebook,2,12.50\nPencil,3,9.00\nCase,1,15.00\n')=={'rows':3,'columns':3,'totals':{'quantity':'6','amount':'36.50'}}
    assert summarize(b'n,mixed\n0.1,2\n0.2,text\n,3\n')['totals']=={'n':'0.3'}

@pytest.mark.parametrize('data',[b'a,a\n1,2\n',b'a,b\n1\n',b'\xff',b'"unterminated',b'a\n'+b'1\n'*1001])
def test_malformed_or_excessive_csv_is_rejected(data):
    with pytest.raises(Problem):summarize(data)

def test_html_summary_and_email_are_consistent_and_escaped(fixture):
    app,client,mail,_=fixture
    response=client.post('/summaries',data={'email':'reader@example.com','csv':(BytesIO(b'<script>alert(1)</script>,name\n12.50,Example\n'),'example.csv')})
    assert response.status_code==201
    html=response.get_data(as_text=True)
    assert '&lt;script&gt;' in html and '<script>alert' not in html
    assert '12.50' in mail.messages[0]['text']
    assert client.get('/admin').status_code==401
    assert client.get('/admin',headers=AUTH).status_code==200

def test_cross_origin_and_bad_email_do_not_create_reports(fixture):
    app,client,mail,_=fixture
    assert client.post('/summaries',headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/summaries',data={'email':'bad','csv':(BytesIO(b'a\n1\n'),'x.csv')}).status_code==400
    assert mail.messages==[]
