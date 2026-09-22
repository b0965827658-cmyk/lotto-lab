import json,time
from urllib.parse import parse_qs,urlparse
import pytest
from community_login import LoginStore,CHANNEL,ORIGIN,COOKIE,digest
import community_http as http
from community import CommunityStore
from test_community import NOW

def flow(store):
    url,browser=store.begin();q=parse_qs(urlparse(url).query)
    assert q['scope']==['openid'] and q['code_challenge_method']==['S256']
    return q,browser

def fake_post(q):
    def post(endpoint,values):
        if endpoint=='token':
            assert values['code_verifier'] and values['redirect_uri']==ORIGIN+'/auth/line/callback'
            return {'id_token':'fake'}
        return dict(iss='https://access.line.me',aud=CHANNEL,sub='private-member',exp=time.time()+600,nonce=q['nonce'][0])
    return post

def test_login_browser_binding_replay_and_logout(tmp_path):
    store=LoginStore(tmp_path/'auth.sqlite','secret');q,browser=flow(store)
    with pytest.raises(ValueError):store.finish(q['state'][0],'other-browser','code')
    token=store.finish(q['state'][0],browser,'code',post=fake_post(q))
    assert store.session(token)['member']=='private-member'
    with pytest.raises(ValueError):store.finish(q['state'][0],browser,'code',post=fake_post(q))
    assert not store.session('forged')
    store.logout(token);assert not store.session(token)

@pytest.mark.parametrize('field,value',[('aud','other'),('iss','evil'),('nonce','wrong'),('exp',0),('sub','')])
def test_bad_claims_denied(tmp_path,field,value):
    store=LoginStore(tmp_path/'auth.sqlite','secret');q,browser=flow(store);original=fake_post(q)
    def post(endpoint,values):
        result=original(endpoint,values)
        if endpoint=='verify':result[field]=value
        return result
    with pytest.raises(ValueError):store.finish(q['state'][0],browser,'code',post=post)
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0

class Handler:
    path='/api/community/share'
    headers={}
    body={}
    def client_key(self):return 'test'
    def send_json(self,data,status=200,extra_headers=None):self.result=(status,data)
    def read_json_body(self):return self.body

def test_http_gate_csrf_and_spoofed_identity(tmp_path,monkeypatch):
    monkeypatch.setattr(http,'ROOT',tmp_path);http._limits.clear()
    monkeypatch.setenv('LINE_LOGIN_ENABLED','1');monkeypatch.setenv('LINE_LOGIN_CHANNEL_SECRET','test')
    h=Handler()
    http.handle(h,'POST',False,True,set());assert h.result[0]==503
    http.handle(h,'POST',True,False,set());assert h.result[0]==503
    http.handle(h,'POST',True,True,set());assert h.result[0]==401
    login=LoginStore(tmp_path/'community_auth.sqlite3','test')
    with login.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest('token'),'real-member','csrf',time.time()+600))
    h.headers={'Cookie':COOKIE+'=token','Content-Type':'application/json','X-CSRF-Token':'csrf','Origin':'https://evil.test'}
    http.handle(h,'POST',True,True,set());assert h.result[0]==403
    h.headers['Origin']=ORIGIN;h.path='/api/community/profile';h.body={'alias':'測試會員','consent':True,'userId':'attacker'}
    http.handle(h,'POST',True,True,set());assert h.result[0]==200
    store=CommunityStore(tmp_path/'community.sqlite3')
    assert store.alias('real-member')=='測試會員' and store.alias('attacker') is None
    monkeypatch.setattr(CommunityStore,'clock',staticmethod(lambda now:NOW))
    h.path='/api/community/share';h.body={'drawDate':'2026-09-22','numbers':[1,2,3,4,5],'reason':'觀察','userId':'attacker'}
    http.handle(h,'POST',True,True,set());assert h.result[0]==200
    http.handle(h,'POST',True,True,set());assert h.result[1]['changed'] is False
    assert store.feed('2026-09-22','real-member')[0]['mine']
    h.path='/api/community/hide';h.body={'kind':'pick','id':1,'reason':'測試'}
    http.handle(h,'POST',True,True,set());assert h.result[0]==403

def test_public_privacy_hidden_stats_and_report_dedup(tmp_path):
    store=CommunityStore(tmp_path/'community.sqlite');store.register('secret-line-id','公開暱稱')
    pick=store.share('secret-line-id','2026-09-22',[1,2,3,4,5],'<script>not html</script>',now=NOW)
    serialized=json.dumps(store.feed('2026-09-22'),ensure_ascii=False)
    assert 'secret-line-id' not in serialized and '公開暱稱' in serialized
    store.report('reporter','pick',pick['id'],'原因');store.report('reporter','pick',pick['id'],'原因')
    with store.connect() as db:assert db.execute('SELECT COUNT(*) FROM reports').fetchone()[0]==1
    store.hide('admin','pick',pick['id'],'管理理由')
    assert store.feed('2026-09-22')==[] and store.snapshot('2026-09-22')['sampleSize']==0
