"""Community analytics for authenticated submissions, isolated from official draws.

No network, LINE calls, or public write endpoint. Authentication is supplied by
an integration layer; public request bodies must never supply member identity.
"""
import json
import sqlite3
import re
from collections import Counter
from datetime import date, datetime, time
from itertools import combinations
from pathlib import Path
from zoneinfo import ZoneInfo

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:return super().__exit__(*args)
        finally:self.close()

TAIPEI = ZoneInfo('Asia/Taipei')


def close_time(draw_date):
    day = date.fromisoformat(draw_date)
    if day.isoformat() != draw_date or day.weekday() == 6:
        raise ValueError('請選擇今彩539週一至週六的開獎日期')
    return datetime.combine(day, time(20, 25), TAIPEI)


def validate_numbers(numbers):
    if not isinstance(numbers, list) or len(numbers) != 5 or any(type(n) is not int or not 1 <= n <= 39 for n in numbers) or len(set(numbers)) != 5:
        raise ValueError('請選擇五個不重複的1–39整數')
    return sorted(numbers)


class CommunityStore:
    def __init__(self, path):
        self.path = Path(path)

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10, factory=ClosingConnection)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS board_messages (
                id INTEGER PRIMARY KEY, member TEXT NOT NULL, parent_id INTEGER,
                title TEXT NOT NULL, category TEXT NOT NULL, body TEXT NOT NULL,
                created_at TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
                request_key TEXT NOT NULL, UNIQUE(member,request_key));
            CREATE INDEX IF NOT EXISTS board_parent ON board_messages(parent_id,id);
            CREATE TABLE IF NOT EXISTS members (
                member TEXT PRIMARY KEY, alias TEXT NOT NULL UNIQUE);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY, member TEXT NOT NULL, kind TEXT NOT NULL,
                target INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                UNIQUE(member,kind,target));
            CREATE TABLE IF NOT EXISTS moderation (
                id INTEGER PRIMARY KEY, actor TEXT NOT NULL, kind TEXT NOT NULL,
                target INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS picks (
                id INTEGER PRIMARY KEY, member TEXT NOT NULL, draw_date TEXT NOT NULL,
                numbers TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0,
                UNIQUE(member, draw_date));
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY, pick_id INTEGER NOT NULL, numbers TEXT NOT NULL,
                reason TEXT NOT NULL, saved_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY, pick_id INTEGER NOT NULL, member TEXT NOT NULL,
                body TEXT NOT NULL, created_at TEXT NOT NULL, hidden INTEGER NOT NULL DEFAULT 0);
        ''')
        return db

    @staticmethod
    def identity(member):
        if not isinstance(member, str) or not member.strip():
            raise ValueError('需要已驗證的會員身分')

    @staticmethod
    def clock(now):
        now = now or datetime.now(TAIPEI)
        if now.tzinfo is None:
            raise ValueError('需要含時區的時間')
        return now.astimezone(TAIPEI)

    def share(self, member, draw_date, numbers, reason, *, now=None):
        self.identity(member)
        now = self.clock(now)
        close = close_time(draw_date)
        if not now.date() <= close.date() <= date.fromordinal(now.date().toordinal()+7) or now >= close:
            raise ValueError('本期已鎖定，或日期超出未來七天範圍')
        numbers = validate_numbers(numbers)
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1000:
            raise ValueError('請填寫1–1000字分享理由')
        reason = reason.strip()
        stamp, encoded = now.isoformat(), json.dumps(numbers)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT id,numbers,reason,hidden FROM picks WHERE member=? AND draw_date=?',(member,draw_date)).fetchone()
            if row and row[3]:
                raise ValueError('此投稿已由管理員隱藏')
            if row and row[1:3] == (encoded,reason):
                return {'id':row[0], 'changed':False}
            if row:
                pick_id=row[0]
                db.execute('UPDATE picks SET numbers=?,reason=?,updated_at=? WHERE id=?',(encoded,reason,stamp,pick_id))
            else:
                pick_id=db.execute('INSERT INTO picks(member,draw_date,numbers,reason,created_at,updated_at) VALUES(?,?,?,?,?,?)',(member,draw_date,encoded,reason,stamp,stamp)).lastrowid
            db.execute('INSERT INTO revisions(pick_id,numbers,reason,saved_at) VALUES(?,?,?,?)',(pick_id,encoded,reason,stamp))
        return {'id':pick_id, 'changed':True}

    def comment(self, member, pick_id, body, *, now=None):
        self.identity(member)
        now=self.clock(now)
        if not isinstance(body,str) or not 1<=len(body.strip())<=500:
            raise ValueError('留言需為1–500字')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM picks WHERE id=? AND hidden=0',(pick_id,)).fetchone():
                raise ValueError('找不到公開投稿')
            previous=db.execute('SELECT created_at FROM comments WHERE member=? ORDER BY id DESC LIMIT 1',(member,)).fetchone()
            if previous and (now-datetime.fromisoformat(previous[0])).total_seconds()<60:
                raise ValueError('請稍後一分鐘再留言')
            return db.execute('INSERT INTO comments(pick_id,member,body,created_at) VALUES(?,?,?,?)',(pick_id,member,body.strip(),now.isoformat())).lastrowid

    def snapshot(self, draw_date):
        close = close_time(draw_date)
        # An empty public read must not create a database or synthetic activity.
        if not self.path.exists():
            rows=[]
        else:
            with sqlite3.connect(self.path.resolve().as_uri()+'?mode=ro',uri=True,factory=ClosingConnection) as db:
                rows=db.execute('SELECT numbers FROM picks WHERE draw_date=? AND hidden=0',(draw_date,)).fetchall()
        picks=[json.loads(row[0]) for row in rows]
        counts=Counter(n for numbers in picks for n in numbers)
        pairs=Counter(pair for numbers in picks for pair in combinations(sorted(numbers),2))
        odd=Counter(sum(n%2 for n in numbers) for numbers in picks)
        size=len(picks)
        return {'game':'tw539','drawDate':draw_date,'closesAt':close.isoformat(),
            'sampleSize':size,'sampleUnit':'會員投稿，每位會員每日期一組',
            'numbers':[{'number':n,'count':counts[n],'share':counts[n]/size if size else 0} for n in range(1,40)],
            'pairs':[{'numbers':list(pair),'count':count} for pair,count in sorted(pairs.items(),key=lambda x:(-x[1],x[0]))[:10]],
            'oddEven':[{'odd':n,'even':5-n,'count':odd[n]} for n in range(6)],
            'notice':'僅為社群選號分布，不是官方獎號、模型推薦或中獎機率。'}


    def register(self, member, alias):
        self.identity(member)
        if not isinstance(alias,str) or not re.fullmatch(r"[\w\u3400-\u9fff -]{2,20}",alias.strip()):
            raise ValueError('公開暱稱需為2–20字，可使用中英文、數字與空格')
        with self.connect() as db:
            existing=db.execute('SELECT alias FROM members WHERE member=?',(member,)).fetchone()
            if existing:
                return existing[0]
            try:
                db.execute('INSERT INTO members VALUES(?,?)',(member,alias.strip()))
            except sqlite3.IntegrityError:
                raise ValueError('此暱稱已有人使用') from None
        return alias.strip()

    def alias(self, member):
        if not member or not self.path.exists():return None
        with self.connect() as db:
            row=db.execute('SELECT alias FROM members WHERE member=?',(member,)).fetchone()
        return row[0] if row else None

    def feed(self, draw_date, member='', before=0):
        close_time(draw_date)
        if not self.path.exists():return []
        with self.connect() as db:
            rows=db.execute("""SELECT p.id,m.alias,p.numbers,p.reason,p.created_at,p.updated_at,p.member
                FROM picks p JOIN members m ON m.member=p.member
                WHERE p.draw_date=? AND p.hidden=0 AND (?=0 OR p.id<?)
                ORDER BY p.id DESC LIMIT 30""",(draw_date,before,before)).fetchall()
            result=[]
            for row in rows:
                comments=db.execute("""SELECT c.id,m.alias,c.body,c.created_at FROM comments c
                    JOIN members m ON m.member=c.member WHERE c.pick_id=? AND c.hidden=0
                    ORDER BY c.id DESC LIMIT 30""",(row[0],)).fetchall()
                revisions=db.execute('SELECT numbers,reason,saved_at FROM revisions WHERE pick_id=? ORDER BY id DESC LIMIT 20',(row[0],)).fetchall()
                result.append(dict(id=row[0],alias=row[1],numbers=json.loads(row[2]),reason=row[3],createdAt=row[4],updatedAt=row[5],mine=row[6]==member,
                    comments=[dict(id=c[0],alias=c[1],body=c[2],createdAt=c[3]) for c in reversed(comments)],
                    revisions=[dict(numbers=json.loads(r[0]),reason=r[1],savedAt=r[2]) for r in revisions]))
        return result

    def report(self, member, kind, target, reason):
        if kind not in ('pick','comment','board'):raise ValueError('檢舉類型錯誤')
        if type(target) is not int or not isinstance(reason,str) or not 1<=len(reason.strip())<=300:raise ValueError('請提供檢舉對象及1–300字理由')
        table={'pick':'picks','comment':'comments','board':'board_messages'}[kind]
        with self.connect() as db:
            if not db.execute(f'SELECT 1 FROM {table} WHERE id=? AND hidden=0',(target,)).fetchone():raise ValueError('找不到內容')
            db.execute('INSERT OR IGNORE INTO reports(member,kind,target,reason,created_at) VALUES(?,?,?,?,?)',(member,kind,target,reason.strip(),self.clock(None).isoformat()))

    def hide(self, actor, kind, target, reason):
        # The HTTP integration must authorize the administrator first.
        if kind not in ('pick','comment','board') or type(target) is not int:raise ValueError('內容類型錯誤')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=300:raise ValueError('請提供管理理由')
        table={'pick':'picks','comment':'comments','board':'board_messages'}[kind]
        with self.connect() as db:
            if not db.execute(f'UPDATE {table} SET hidden=1 WHERE id=? AND hidden=0',(target,)).rowcount:raise ValueError('找不到公開內容')
            db.execute('INSERT INTO moderation(actor,kind,target,reason,created_at) VALUES(?,?,?,?,?)',(actor,kind,target,reason.strip(),self.clock(None).isoformat()))

    def reports(self):
        if not self.path.exists():return []
        with self.connect() as db:
            rows=db.execute('SELECT kind,target,reason,created_at FROM reports ORDER BY id DESC LIMIT 100').fetchall()
        return [dict(kind=r[0],id=r[1],reason=r[2],createdAt=r[3]) for r in rows]

    def board_write(self, member, body, request_key, *, title='', category='chat', parent_id=None, now=None):
        self.identity(member)
        if not self.alias(member):raise ValueError('請先設定公開暱稱')
        if not isinstance(body,str) or not 1<=len(body.strip())<=2000:raise ValueError('內容需為1–2000字')
        if not isinstance(title,str) or len(title.strip())>80:raise ValueError('標題最多80字')
        if category not in ('chat','numbers','question'):raise ValueError('分類錯誤')
        if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',request_key):raise ValueError('請重新載入頁面')
        if parent_id is not None and (type(parent_id) is not int or parent_id<=0):raise ValueError('主題編號錯誤')
        now=self.clock(now);body=body.strip();title=title.strip()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id,body,title,category,parent_id FROM board_messages WHERE member=? AND request_key=?',(member,request_key)).fetchone()
            if old:
                if old[1:]!=(body,title,category,parent_id):raise ValueError('同一次送出內容已變更，請重新送出')
                return dict(id=old[0],changed=False)
            if parent_id is not None and not db.execute('SELECT 1 FROM board_messages WHERE id=? AND parent_id IS NULL AND hidden=0',(parent_id,)).fetchone():raise ValueError('主題已隱藏或不存在')
            previous=db.execute('SELECT created_at FROM board_messages WHERE member=? ORDER BY id DESC LIMIT 1',(member,)).fetchone()
            if previous and (now-datetime.fromisoformat(previous[0])).total_seconds()<30:raise ValueError('請等30秒再發言')
            mid=db.execute('INSERT INTO board_messages(member,parent_id,title,category,body,created_at,request_key) VALUES(?,?,?,?,?,?,?)',(member,parent_id,title,category,body,now.isoformat(),request_key)).lastrowid
        return dict(id=mid,changed=True)

    def board_feed(self, *, member='', before=0, category='', parent_id=None):
        if type(before) is not int or before<0 or category not in ('','chat','numbers','question'):raise ValueError('篩選參數錯誤')
        if parent_id is not None and (type(parent_id) is not int or parent_id<=0):raise ValueError('主題編號錯誤')
        if not self.path.exists():return []
        with self.connect() as db:
            if parent_id is not None and not db.execute('SELECT 1 FROM board_messages WHERE id=? AND parent_id IS NULL AND hidden=0',(parent_id,)).fetchone():raise ValueError('主題已隱藏或不存在')
            rows=db.execute("""SELECT b.id,m.alias,b.title,b.category,b.body,b.created_at,b.member,
                (SELECT COUNT(*) FROM board_messages r WHERE r.parent_id=b.id AND r.hidden=0)
                FROM board_messages b JOIN members m ON b.member=m.member
                WHERE b.hidden=0 AND b.parent_id IS ? AND (?=0 OR b.id<?) AND (?='' OR b.category=?)
                ORDER BY b.id DESC LIMIT 30""",(parent_id,before,before,category,category)).fetchall()
        return [dict(id=r[0],alias=r[1],title=r[2],category=r[3],body=r[4],createdAt=r[5],mine=r[6]==member,replies=r[7]) for r in rows]
