"""HTTP boundary for the separate public community, restricted to pinned staging."""
import hmac
import os
import re
import threading
import time
from datetime import datetime,timedelta
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from community import CommunityStore,TAIPEI
from community_login import LoginStore,ORIGIN,COOKIE,FLOW,cookie,set_cookie

ROOT=Path('/var/data/lotto-lab')
_limits={}
_lock=threading.Lock()

def limited(key):
    now=time.monotonic()
    with _lock:
        if len(_limits)>2000:
            for old in list(_limits):
                if now-_limits[old][0]>60:del _limits[old]
            if len(_limits)>2000:return True
        start,count=_limits.get(key,(now,0))
        if now-start>60:start,count=now,0
        _limits[key]=(start,count+1)
        return count>=30

def default_date():
    now=datetime.now(TAIPEI)
    day=now.date()+timedelta(days=int((now.hour,now.minute)>=(20,25)))
    while day.weekday()==6:day+=timedelta(days=1)
    return day.isoformat()

def handle(handler,method,enabled,mounted,admins,sync_results=None):
    parsed=urlparse(handler.path);path=parsed.path
    if not (path.startswith('/api/community/') or path.startswith('/auth/line/')):return False
    def reply(data,status=200,headers=None):handler.send_json(data,status=status,extra_headers=headers)
    def redirect(url,cookies=()):
        handler.send_response(303);handler.send_header('Location',url)
        handler.send_header('Cache-Control','no-store');handler.send_header('Content-Length','0')
        handler.send_header('Referrer-Policy','no-referrer')
        for value in cookies:handler.send_header('Set-Cookie',value)
        handler.end_headers()
    if not enabled or not mounted:
        reply({'error':'交流區僅在指定 Staging 持久化服務啟用'},503);return True
    if limited(handler.client_key()):reply({'error':'請稍後一分鐘再試'},429);return True
    ready=bool(os.environ.get('LINE_LOGIN_CHANNEL_SECRET')) and os.environ.get('LINE_LOGIN_ENABLED')=='1'
    login=LoginStore(ROOT/'community_auth.sqlite3',os.environ.get('LINE_LOGIN_CHANNEL_SECRET',''))
    store=CommunityStore(ROOT/'community.sqlite3')
    session=login.session(cookie(handler.headers,COOKIE)) if ready else None
    member=session['member'] if session else ''
    admin=member in admins if member else False
    query=parse_qs(parsed.query)
    def param(name,default=''):
        values=query.get(name,[default])
        if len(values)!=1:raise ValueError('重複參數')
        return values[0]
    try:
        if method=='GET':
            if path in ('/api/community/history','/api/community/leaderboard'):
                if path.endswith('/history') and not member:reply({'error':'請先登入 LINE'},401);return True
                synced=False
                if sync_results:
                    try:sync_results(store);synced=True
                    except Exception:pass
                if path.endswith('/history'):
                    rows=store.member_history(member,int(param('before','0')))
                    reply(dict(posts=rows,nextBefore=rows[-1]['id'] if len(rows)==30 else None,syncOk=synced));return True
                reply(dict(rows=store.leaderboard(),syncOk=synced));return True
            if path=='/api/community/bans':
                if not admin:reply({'error':'僅管理員可操作'},403);return True
                reply({'rows':store.ban_list()});return True
            if path=='/api/community/session':
                reply(dict(authenticated=bool(session),loginReady=ready,alias=store.alias(member),admin=admin,csrf=session['csrf'] if session else None,defaultDate=default_date()));return True
            if path=='/api/community/reports':
                if not admin:reply({'error':'僅管理員可操作'},403);return True
                reply({'reports':store.reports()});return True
            if path=='/api/community/board':
                parent=param('parent')
                rows=store.board_feed(member=member,before=int(param('before','0')),category=param('category'),parent_id=int(parent) if parent else None)
                reply(dict(posts=rows,nextBefore=rows[-1]['id'] if len(rows)==30 else None));return True
            if path=='/api/community/feed':
                day=param('date',default_date());before=int(param('before','0'))
                if before<0:raise ValueError('頁碼錯誤')
                rows=store.feed(day,member,before)
                reply(dict(stats=store.snapshot(day),posts=rows,nextBefore=rows[-1]['id'] if len(rows)==30 else None));return True
            if path=='/auth/line/start':
                if not ready:reply({'error':'LINE 登入尚在設定，請先瀏覽交流區'},503);return True
                url,browser=login.begin();redirect(url,[set_cookie(FLOW,browser,600)]);return True
            if path=='/auth/line/callback':
                if not ready:raise ValueError('LINE 登入尚未啟用')
                if param('error'):raise ValueError('LINE 登入未完成')
                token=login.finish(param('state'),cookie(handler.headers,FLOW),param('code'))
                old=cookie(handler.headers,COOKIE)
                login.logout(old)
                redirect('/community.html',[set_cookie(COOKIE,token,86400),set_cookie(FLOW,'',0)]);return True
        if method=='POST':
            if not session:reply({'error':'請先登入 LINE'},401);return True
            if (handler.headers.get('Origin')!=ORIGIN or not hmac.compare_digest(handler.headers.get('X-CSRF-Token',''),session['csrf'])):
                reply({'error':'請重新載入頁面後再操作'},403);return True
            if handler.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('需要 JSON')
            body=handler.read_json_body()
            if not isinstance(body,dict):raise ValueError('資料格式錯誤')
            if path=='/api/community/logout':
                login.logout(cookie(handler.headers,COOKIE));reply({'ok':True},headers={'Set-Cookie':set_cookie(COOKIE,'',0)});return True
            if store.is_banned(member):reply({'error':'此帳號已暫停發言，仍可瀏覽及登出'},403);return True
            if path=='/api/community/ban':
                if not admin:reply({'error':'僅管理員可操作'},403);return True
                store.ban_alias(member,body.get('alias'),body.get('active'),body.get('reason'),admins)
                reply({'ok':True});return True
            if path=='/api/community/profile':
                if body.get('consent') is not True:raise ValueError('請確認公開暱稱與投稿規則')
                reply({'alias':store.register(member,body.get('alias'))});return True
            if not store.alias(member):raise ValueError('請先設定公開暱稱')
            for key in ('reason','body','title'):
                text=body.get(key,'')
                if isinstance(text,str) and re.search(r'https?://|www\.',text,re.I):raise ValueError('測試期間請勿張貼外部連結')
            if path=='/api/community/board':
                reply(store.board_write(member,body.get('body'),body.get('requestKey'),title=body.get('title',''),category=body.get('category','chat'),parent_id=body.get('parentId')));return True
            if path=='/api/community/share':reply(store.share(member,body.get('drawDate',''),body.get('numbers'),body.get('reason')));return True
            if path=='/api/community/comment':
                if type(body.get('pickId')) is not int:raise ValueError('投稿編號錯誤')
                reply({'id':store.comment(member,body['pickId'],body.get('body'))});return True
            if path=='/api/community/report':store.report(member,body.get('kind'),body.get('id'),body.get('reason'));reply({'ok':True});return True
            if path=='/api/community/hide':
                if not admin:reply({'error':'僅管理員可操作'},403);return True
                store.hide(member,body.get('kind'),body.get('id'),body.get('reason'));reply({'ok':True});return True
        reply({'error':'找不到此功能'},404)
    except (ValueError,TypeError) as exc:
        if path=='/auth/line/callback':redirect('/community.html?login=failed',[set_cookie(FLOW,'',0)])
        else:reply({'error':str(exc) if path=='/api/community/board' and isinstance(exc,ValueError) else '資料不符規則，請檢查日期、暱稱、號碼與文字；也可能已截止或操作過於頻繁。'},400)
    except Exception:
        # OAuth upstream bodies, tokens, authorization codes and user IDs are never logged.
        if path=='/auth/line/callback':redirect('/community.html?login=failed',[set_cookie(FLOW,'',0)])
        else:reply({'error':'服務暫時無法使用，請稍後再試'},503)
    return True
