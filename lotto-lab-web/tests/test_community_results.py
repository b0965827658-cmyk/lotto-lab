from datetime import datetime
import json,time
import pytest
from community import CommunityStore,TAIPEI
from community_login import LoginStore,COOKIE,ORIGIN,digest
from test_community_login import Handler
import community_http as http

def draw():return dict(game='tw539',date='2026-09-22',period='115000230',numbers=[1,2,6,7,8],bonus=[],sourceUrl='https://api.taiwanlottery.com/TLCAPIWeB/Lottery/LastNumber')

def test_official_idempotency_conflicts_and_history(tmp_path,monkeypatch):
    s=CommunityStore(tmp_path/'c.sqlite');s.register('private-id','會員甲')
    s.share('private-id','2026-09-22',[1,2,3,4,5],'reason',now=datetime(2026,9,22,12,tzinfo=TAIPEI))
    monkeypatch.setattr(CommunityStore,'clock',staticmethod(lambda now:datetime(2026,9,23,12,tzinfo=TAIPEI)))
    assert s.member_history('private-id')[0]['matched'] is None
    assert s.record_official(draw()) and not s.record_official(draw())
    with pytest.raises(ValueError):s.record_official(dict(draw(),numbers=[3,4,5,6,7]))
    with pytest.raises(ValueError):s.record_official(dict(draw(),sourceUrl='https://third-party.test/results'))
    with pytest.raises(ValueError):s.record_official(dict(draw(),game='ca-fantasy5'))
    assert s.member_history('private-id')[0]['matched']==[1,2]
    assert s.member_history('other')==[]
    s.hide('admin','pick',1,'reason')
    assert s.leaderboard()[0]['draws']==1
    assert 'private-id' not in json.dumps(s.leaderboard())
    s.ban_alias('admin','會員甲',True,'spam')
    assert s.is_banned('private-id') and s.leaderboard()==[]
    s.ban_alias('admin','會員甲',False,'reviewed')
    assert not s.is_banned('private-id') and s.leaderboard()
    with s.connect() as db:
        assert db.execute('SELECT count(*) FROM official_results').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM ban_audit').fetchone()[0]==2

def test_bans_and_history_http_permissions(tmp_path,monkeypatch):
    monkeypatch.setattr(http,'ROOT',tmp_path);http._limits.clear()
    monkeypatch.setenv('LINE_LOGIN_ENABLED','1');monkeypatch.setenv('LINE_LOGIN_CHANNEL_SECRET','test')
    login=LoginStore(tmp_path/'community_auth.sqlite3','test')
    with login.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest('token'),'member','csrf',time.time()+600))
    s=CommunityStore(tmp_path/'community.sqlite3');s.register('member','會員甲')
    h=Handler();h.path='/api/community/history';h.headers={}
    http.handle(h,'GET',True,True,{'admin'});assert h.result[0]==401
    h.headers={'Cookie':COOKIE+'=token','Origin':ORIGIN,'Content-Type':'application/json','X-CSRF-Token':'csrf'}
    h.path='/api/community/ban';h.body={'alias':'會員甲','active':True,'reason':'test'}
    http.handle(h,'POST',True,True,{'admin'});assert h.result[0]==403
    s.ban_alias('admin','會員甲',True,'reason')
    h.path='/api/community/board';h.body={'body':'blocked','requestKey':'test-key-12345678'}
    http.handle(h,'POST',True,True,{'admin'});assert h.result[0]==403
    h.path='/api/community/history'
    http.handle(h,'GET',True,True,{'admin'});assert h.result[0]==200
    with pytest.raises(ValueError):s.ban_alias('admin','會員甲',True,'reason',{'member'})

def test_poll_window():
    import server
    assert server.line_notification_poll_seconds(datetime(2026,9,22,20,30,tzinfo=TAIPEI))==30
    assert server.line_notification_poll_seconds(datetime(2026,9,22,21,29,tzinfo=TAIPEI))==30
    assert server.line_notification_poll_seconds(datetime(2026,9,22,19,0,tzinfo=TAIPEI))==server.LINE_NOTIFICATION_LOOP_INTERVAL_SECONDS
