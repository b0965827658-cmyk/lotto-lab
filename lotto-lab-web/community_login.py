"""Staging-only LINE OIDC sessions. No Messaging API calls or profile publication."""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:return super().__exit__(*args)
        finally:self.close()

ORIGIN='https://lotto-lab-candidate-a-staging.onrender.com'
CALLBACK=ORIGIN+'/auth/line/callback'
CHANNEL='2011420222'
COOKIE='__Host-lotto_session'
FLOW='__Host-lotto_login'

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def cookie(headers,name):
    try:
        jar=SimpleCookie();jar.load(headers.get('Cookie',''))
        return jar[name].value if name in jar else ''
    except Exception:return ''

def set_cookie(name,value,age):
    return f'{name}={value}; Path=/; Max-Age={age}; Secure; HttpOnly; SameSite=Lax'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def line_post(endpoint,values):
    # Only fixed LINE token endpoints; credentials may never follow redirects.
    if endpoint not in ('token','verify'):raise ValueError('Invalid endpoint')
    request=urllib.request.Request('https://api.line.me/oauth2/v2.1/'+endpoint,
        data=urlencode(values).encode(),headers={'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'})
    with urllib.request.build_opener(NoRedirect).open(request,timeout=15) as response:
        if response.status!=200 or response.headers.get_content_type()!='application/json':raise ValueError('LINE 驗證失敗')
        raw=response.read(65537)
        if len(raw)>65536:raise ValueError('LINE 回應過大')
        data=json.loads(raw)
        if not isinstance(data,dict):raise ValueError('LINE 驗證格式錯誤')
        return data

class LoginStore:
    def __init__(self,path,secret):self.path=Path(path);self.secret=secret
    def connect(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=10,factory=ClosingConnection)
        db.executescript('''CREATE TABLE IF NOT EXISTS flows(state TEXT PRIMARY KEY,browser TEXT,nonce TEXT,verifier TEXT,expires REAL);
            CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,member TEXT,csrf TEXT,expires REAL);''')
        db.execute('DELETE FROM flows WHERE expires<?',(time.time(),))
        db.execute('DELETE FROM sessions WHERE expires<?',(time.time(),))
        db.commit()
        return db
    def begin(self):
        state=secrets.token_hex(24);browser=secrets.token_urlsafe(32)
        nonce=secrets.token_hex(24);verifier=secrets.token_urlsafe(48)
        with self.connect() as db:db.execute('INSERT INTO flows VALUES(?,?,?,?,?)',(digest(state),digest(browser),nonce,verifier,time.time()+600))
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://access.line.me/oauth2/v2.1/authorize?'+urlencode(dict(response_type='code',client_id=CHANNEL,redirect_uri=CALLBACK,state=state,scope='openid',nonce=nonce,code_challenge=challenge,code_challenge_method='S256')),browser
    def finish(self,state,browser,code,post=line_post):
        if not state or not browser or not code:raise ValueError('登入已失效，請重新登入')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            flow=db.execute('SELECT browser,nonce,verifier,expires FROM flows WHERE state=?',(digest(state),)).fetchone()
            if not flow or flow[3]<=time.time() or not hmac.compare_digest(flow[0],digest(browser)):raise ValueError('登入驗證失敗')
            db.execute('DELETE FROM flows WHERE state=?',(digest(state),))
        tokens=post('token',dict(grant_type='authorization_code',code=code,redirect_uri=CALLBACK,client_id=CHANNEL,client_secret=self.secret,code_verifier=flow[2]))
        token=tokens.get('id_token')
        if not isinstance(token,str) or not token:raise ValueError('缺少登入驗證')
        claims=post('verify',dict(id_token=token,client_id=CHANNEL,nonce=flow[1]))
        if (claims.get('iss')!='https://access.line.me' or claims.get('aud')!=CHANNEL or claims.get('nonce')!=flow[1]
            or type(claims.get('exp')) not in (int,float) or claims['exp']<=time.time()
            or not isinstance(claims.get('sub'),str) or not claims['sub']):raise ValueError('登入驗證失敗')
        session=secrets.token_urlsafe(32)
        with self.connect() as db:db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(session),claims['sub'],secrets.token_urlsafe(32),time.time()+86400))
        return session
    def session(self,token):
        if not token or not self.path.exists():return None
        with self.connect() as db:row=db.execute('SELECT member,csrf FROM sessions WHERE token=? AND expires>?',(digest(token),time.time())).fetchone()
        return dict(member=row[0],csrf=row[1]) if row else None
    def logout(self,token):
        if self.path.exists():
            with self.connect() as db:db.execute('DELETE FROM sessions WHERE token=?',(digest(token),))
