"""Return destinations must not weaken OIDC state, expiry or browser binding."""
import sqlite3
import time
from urllib.parse import parse_qs, urlencode, urlparse

import pytest
import community_http as http
from community_login import LoginStore, DEFAULT_RETURN, FLOW, COOKIE, digest, safe_return_target
from test_community_login import Handler, fake_post


class RedirectHandler(Handler):
    def __init__(self,path,headers=None):
        self.path=path;self.headers=headers or {};self.response_headers=[]
    def send_response(self,status):self.status=status
    def send_header(self,name,value):self.response_headers.append((name,value))
    def end_headers(self):pass
    @property
    def location(self):return dict(self.response_headers)['Location']


@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(http,'ROOT',tmp_path);http._limits.clear()
    monkeypatch.setenv('LINE_LOGIN_ENABLED','1');monkeypatch.setenv('LINE_LOGIN_CHANNEL_SECRET','test')
    return LoginStore(tmp_path/'community_auth.sqlite3','test')


def begin(store,target='/member.html#history'):
    url,browser=store.begin(target)
    return parse_qs(urlparse(url).query),browser


def callback(q,browser,**params):
    return RedirectHandler('/auth/line/callback?'+urlencode({'state':q['state'][0],**params}),{'Cookie':FLOW+'='+browser})


@pytest.mark.parametrize('target',['https://evil.test/','//evil.test','/\\evil.test',
    '/member.html?next=https://evil.test','/member.html#evil','%2Fmember.html','/member.html\r\nLocation: https://evil.test',None])
def test_return_destinations_are_exact_allowlist(store,target):
    assert safe_return_target(target)==DEFAULT_RETURN
    q,browser=begin(store,target)
    assert store.take_return_target(q['state'][0],browser)==DEFAULT_RETURN


def test_return_target_requires_original_browser_and_is_one_time(store):
    q,browser=begin(store)
    assert store.take_return_target(q['state'][0],'another-browser')==DEFAULT_RETURN
    assert store.take_return_target('forged-state',browser)==DEFAULT_RETURN
    assert store.take_return_target(q['state'][0],browser)=='/member.html#history'
    assert store.take_return_target(q['state'][0],browser)==DEFAULT_RETURN


def test_success_returns_member_page_and_invalidates_old_session(store,monkeypatch):
    q,browser=begin(store)
    with store.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest('old'),'old-member','csrf',time.time()+60))
    finish=LoginStore.finish
    monkeypatch.setattr(LoginStore,'finish',lambda self,state,browser,code:finish(self,state,browser,code,post=fake_post(q)))
    h=callback(q,browser,code='local-test-code',next='https://evil.test')
    h.headers['Cookie']+='; '+COOKIE+'=old'
    assert http.handle(h,'GET',True,True,set())
    assert h.status==303 and h.location=='/member.html#history'
    assert any(n=='Set-Cookie' and v.startswith(COOKIE+'=') and 'HttpOnly' in v and 'Secure' in v for n,v in h.response_headers)
    assert store.session('old') is None
    with store.connect() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==1


@pytest.mark.parametrize('failure',['cancel','expired','upstream'])
def test_failures_return_to_origin_without_creating_session(store,monkeypatch,failure):
    q,browser=begin(store,'/numbers.html#share')
    if failure=='expired':
        with store.connect() as db:db.execute('UPDATE flows SET expires=?',(time.time()-1,))
    if failure=='upstream':
        def fail(*args,**kwargs):raise RuntimeError('private upstream content')
        monkeypatch.setattr(LoginStore,'finish',fail)
    h=callback(q,browser,**({'error':'access_denied'} if failure=='cancel' else {'code':'test'}))
    http.handle(h,'GET',True,True,set())
    assert h.status==303 and h.location=='/numbers.html?login=failed#share'
    assert 'private' not in str(h.response_headers)
    with store.connect() as db:assert db.execute('SELECT count(*) FROM sessions').fetchone()[0]==0


def test_unknown_callback_destination_is_not_trusted(store):
    h=RedirectHandler('/auth/line/callback?error=denied&next=%2Fmember.html')
    http.handle(h,'GET',True,True,set())
    assert h.location=='/community.html?login=failed'


def test_http_begin_preserves_hash_and_authorization_lifetime(store):
    h=RedirectHandler('/auth/line/start?'+urlencode({'next':'/member.html#check'}))
    http.handle(h,'GET',True,True,set())
    q=parse_qs(urlparse(h.location).query)
    browser=next(v.split(';')[0].split('=',1)[1] for n,v in h.response_headers if n=='Set-Cookie')
    assert q['code_challenge_method']==['S256']
    assert store.take_return_target(q['state'][0],browser)=='/member.html#check'
    with store.connect() as db:
        expiry=db.execute('SELECT expires FROM flows').fetchone()[0]
        assert 590<expiry-time.time()<=600


def test_existing_auth_database_sessions_survive_upgrade(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE flows(state TEXT PRIMARY KEY,browser TEXT,nonce TEXT,verifier TEXT,expires REAL);'
            'CREATE TABLE sessions(token TEXT PRIMARY KEY,member TEXT,csrf TEXT,expires REAL);')
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest('existing'),'existing-member','csrf',time.time()+600))
    store=LoginStore(path,'test')
    assert store.session('existing')['member']=='existing-member'
    q,browser=begin(store)
    assert store.take_return_target(q['state'][0],browser)=='/member.html#history'
