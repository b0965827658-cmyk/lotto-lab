"""Read-only chat commands shared by the LINE webhook and its review UI.

No LINE transport, subscriptions, or public writes live here. Caller identity is
supplied only by a verified webhook source or an authenticated preview session.
"""
import re
import sqlite3
import time
import unicodedata

MENU = ('彩種', '歷史紀錄', '直播專區', '公開留言板', '使用說明')
GAMES = ('539', '威力彩', '大樂透', '六合彩', '三星彩', '四星彩', '天天樂')
ALIASES = dict(zip(GAMES, ('tw539', 'power-lottery', 'lotto-649', 'mark-six', 'daily-3', 'daily-4', 'ca-fantasy5')))
ALIASES.update({'今彩539': 'tw539', 'tw539': 'tw539', '加州天天樂': 'ca-fantasy5', 'fantasy5': 'ca-fantasy5', 'fantasy 5': 'ca-fantasy5', 'mark six': 'mark-six', 'marksix': 'mark-six'})
LIVE = {
    '台彩直播': 'https://www.youtube.com/@48ilottery48/streams',
    '港彩直播': 'https://www.mytvsuper.com/tc/live/82/TVB-Plus/',
}
PENDING = '加州天天樂的聊天室歷史、模型與推播尚未開放。最近一期僅可在指定 Staging 顯示 LotteryUSA 第三方資料，且未經官方核實。'


def claim_event(path, event_id):
    """Deduplicate signed webhook events independently of push subscriptions."""
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 200:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_events (event_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL)')
            return db.execute('INSERT OR IGNORE INTO chat_events VALUES (?, ?)', (event_id, int(time.time()))).rowcount == 1
    finally:
        db.close()


def message(text, commands=MENU, links=()):
    items = [{'type': 'action', 'action': {'type': 'message', 'label': label[:20], 'text': command}}
             for label, command in (x if isinstance(x, tuple) else (x, x) for x in commands)]
    items += [{'type': 'action', 'action': {'type': 'uri', 'label': label[:20], 'uri': uri}} for label, uri in links]
    result = {'type': 'text', 'text': text[:4800]}
    if items:
        result['quickReply'] = {'items': items[:13]}
    return result


def normalize(text):
    return ' '.join(unicodedata.normalize('NFKC', text).strip().split()).lower()


def cursor(text, command):
    match = re.fullmatch(re.escape(command) + r'(?:\s+([1-9][0-9]{0,9}))?', text)
    return int(match[1] or 0) if match else None


def reply(text, *, member='', direct=False, latest, history, store):
    """Build a single LINE message; return None only for legacy command routing."""
    if not isinstance(text, str) or len(text) > 300:
        return message('指令最多 300 字。輸入「使用說明」查看格式。')
    text = normalize(text)
    if text in {'使用說明', '說明', '幫助', 'help', '開始', 'start', '選單'}:
        return message('直接輸入指令，或點下方按鈕：\n'
                       '彩種：選擇開獎查詢\n539／威力彩／六合彩：查看最近一期\n'
                       '歷史紀錄：選擇本人紀錄或開獎歷史\n歷史 539：官方最近 5 期\n'
                       '我的紀錄：本人投稿與已核對命中結果\n'
                       '直播：台彩／港彩播放入口\n公開留言板：閱讀網站公開留言\n'
                       '目前聊天室留言板提供閱讀；此處輸入一般文字不會公開發文。\n'
                       '天天樂最近一期若有顯示，會明確標示第三方且未經官方核實。\n'
                       '查詢不會替你開啟通知；資料以回覆的日期與來源為準。')
    if text in {'彩種', '彩券', '遊戲'}:
        return message('選擇彩種即可在聊天室查詢。\n台彩／港彩採官方資料；天天樂最近一期若有顯示，採 LotteryUSA 第三方資料並標示未經官方核實。', GAMES)
    if text in {'歷史紀錄', '紀錄', '歷史'}:
        return message('要查看哪一種紀錄？\n「我的紀錄」是本人投稿；「歷史 539」等指令查官方開獎。',
                       ('我的紀錄', '歷史 539', '歷史 威力彩', '歷史 大樂透', '歷史六合彩', '選單'))
    if text == '歷史六合彩':
        text = '歷史 六合彩'
    if text in {'直播', '直播專區', '台彩直播', '港彩直播'}:
        choices = tuple(LIVE.items()) if text not in LIVE else ((text, LIVE[text]),)
        lines = ['直播專區｜依節目安排播出，入口不代表目前正在開獎。']
        for label, url in choices:
            lines.append(label + ('（僅香港／澳門地區；其他地區可能無法播放）' if label == '港彩直播' else '（全民 i 彩券官方頻道）'))
            lines.append(url)
        return message('\n'.join(lines), ('選單',), choices)
    if text.startswith(('我的紀錄', '本人紀錄')):
        before = cursor(text, '我的紀錄') if text.startswith('我的紀錄') else cursor(text, '本人紀錄')
        if before is None:
            return message('輸入「我的紀錄」，或使用上一則回覆的「更早紀錄」按鈕。')
        if not direct or not member:
            return message('本人紀錄僅供本人在一對一聊天室查閱，不會顯示在群組。預覽頁需使用已登入的本人帳號。')
        try:
            # Query by the authenticated caller; never by a typed ID or alias.
            rows = store.member_history(member, before)[:5]
        except Exception:
            return message('本人紀錄暫時無法讀取，請稍後再試。')
        if not rows:
            return message('目前沒有這個帳號的投稿紀錄。' if not before else '已沒有更早的本人紀錄。')
        lines = ['你的今彩539投稿紀錄（每頁最多5筆）']
        for row in rows:
            numbers = '、'.join(f'{n:02d}' for n in row['numbers'])
            result = f"命中 {len(row['matched'])} 個" if row.get('matched') is not None else '官方結果待核對'
            lines.append(f"{row['date']}｜{numbers}\n{result}" + ('｜投稿已隱藏' if row.get('hidden') else ''))
        lines.append('命中依已保存的官方結果計算；待核對不等於未命中。')
        commands = [( '更早紀錄', f"我的紀錄 {rows[-1]['id']}")] if len(rows) == 5 else []
        return message('\n'.join(lines), commands + ['歷史紀錄', '選單'])
    if text.startswith(('公開留言板', '留言板', '看回覆')):
        command = next(x for x in ('公開留言板', '留言板', '看回覆') if text.startswith(x))
        value = cursor(text, command)
        if value is None or (command == '看回覆' and not value):
            return message('輸入「公開留言板」閱讀最新主題，或「看回覆 主題編號」。')
        try:
            rows = store.board_feed(before=0 if command == '看回覆' else value,
                                    parent_id=value if command == '看回覆' else None)[:5]
        except Exception:
            return message('留言暫時無法讀取，或主題已隱藏／不存在。')
        if not rows:
            return message('目前沒有可顯示的公開留言。', ('公開留言板', '選單'))
        lines = ['網站公開留言' if command != '看回覆' else f'主題 #{value} 的公開回覆（最近5則）']
        commands = []
        for row in rows:
            body = row['body']
            if len(body) > 480:
                body = body[:480] + '…（內容較長，聊天室顯示摘要）'
            lines.append(f"#{row['id']} {row['alias']}｜{row['createdAt'][:10]}\n{row['title']}\n{body}")
            if command != '看回覆' and row['replies']:
                commands.append((f"#{row['id']} 回覆", f"看回覆 {row['id']}"))
        if len(rows) == 5 and command != '看回覆':
            commands.append(('更早留言', f"公開留言板 {rows[-1]['id']}"))
        return message('\n\n'.join(lines), commands + ['公開留言板', '選單'])
    is_history = text.startswith(('歷史 ', '紀錄 ', '歷史紀錄 '))
    query = text.split(' ', 1)[1] if is_history or text.startswith(('最新 ', '最新開獎 ')) else text
    game = ALIASES.get(query)
    if text in {'最新', '最新開獎'}:
        game = 'tw539'
    if game:
        if game == 'ca-fantasy5':
            if is_history:
                return message(PENDING, ('彩種', '選單'))
            try:
                return message(latest(game), ('彩種', '選單'))
            except Exception:
                return message('加州天天樂第三方資料暫時無法核對，請稍後再試。', ('彩種', '選單'))
        try:
            result = history(game) if is_history else latest(game)
            return message(result, (('開獎歷史', '歷史 ' + next(k for k in GAMES if ALIASES[k] == game)), '彩種', '選單'))
        except Exception:
            return message('官方資料暫時無法核對，請稍後再試。', ('彩種', '選單'))
    if text.startswith(('歷史', '紀錄', '最新')):
        return message('請選擇彩種，例如「539」或「歷史 539」。', GAMES)
    return None
