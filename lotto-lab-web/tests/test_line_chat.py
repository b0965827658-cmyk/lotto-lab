import base64
import hashlib
import hmac
from http.client import HTTPConnection
import json
import threading
import time
from datetime import timedelta
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

import community_http as http
import line_chat
import server
from community import CommunityStore
from community_login import LoginStore, COOKIE, digest
from line_notifications import LineNotificationStore
from test_community import NOW
from test_community_login import Handler


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(http, 'ROOT', tmp_path)
    http._limits.clear()
    monkeypatch.setattr(server.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('No real network in chat tests'))
    monkeypatch.setattr(server, 'line_latest_reply', lambda game: '官方最近一期 ' + game)
    monkeypatch.setattr(server, 'line_history_reply', lambda game: '官方最近 5 期 ' + game)
    return CommunityStore(tmp_path / 'community.sqlite3')


def response(store, text, member='', direct=False):
    return line_chat.reply(text, member=member, direct=direct, store=store,
                           latest=server.line_latest_reply, history=server.line_history_reply)


def test_command_navigation_stays_in_chat_except_broadcast_links(store):
    for command in (*line_chat.MENU, '歷史', '幫助', '　５３９　', '歷史 ５３９'):
        data = response(store, command)
        assert data['type'] == 'text' and 0 < len(data['text']) <= 5000
        for item in data['quickReply']['items']:
            action = item['action']
            assert len(action['label']) <= 20
            if command == '直播專區':
                assert action['type'] in ('message', 'uri')
            else:
                assert action['type'] == 'message' and 'uri' not in action
        assert len(data['quickReply']['items']) <= 13
    assert 'tw539' in response(store, '歷史 ５３９')['text']
    broadcast = response(store, '港彩直播')
    assert '香港／澳門' in broadcast['text']
    assert 'mytvsuper.com' in broadcast['text']


@pytest.mark.parametrize('command', ['歷史 天天樂', '歷史紀錄 fantasy5'])
def test_fantasy5_history_never_reads_numbers_or_unlocks_other_paths(store, monkeypatch, command):
    monkeypatch.setattr(server, 'line_latest_reply', lambda game: pytest.fail('blocked source fetched'))
    monkeypatch.setattr(server, 'line_history_reply', lambda game: pytest.fail('blocked history fetched'))
    assert response(store, command)['text'] == line_chat.PENDING
    assert server.delivery_is_blocked('ca-fantasy5')


@pytest.mark.parametrize('command', ['天天樂', '最新 加州天天樂'])
def test_fantasy5_latest_defaults_to_blocked(tmp_path, monkeypatch, command):
    store = CommunityStore(tmp_path / 'community.sqlite3')
    monkeypatch.setattr(server, 'fantasy5_line_third_party_enabled', lambda: False)
    monkeypatch.setattr(server.fantasy5_lotteryusa.feed, 'lookup', lambda path: pytest.fail('third-party source fetched'))
    result = line_chat.reply(command, store=store, latest=server.line_latest_reply,
                             history=lambda game: pytest.fail('history fetched'))
    assert result['text'] == server.DELIVERY_BLOCKED_MESSAGE


def test_fantasy5_latest_can_be_explicitly_attributed_without_unlocking_history_or_push(tmp_path, monkeypatch):
    store = CommunityStore(tmp_path / 'community.sqlite3')
    monkeypatch.setattr(server, 'fantasy5_line_third_party_enabled', lambda: True)
    monkeypatch.setattr(server.fantasy5_lotteryusa.feed, 'lookup', lambda path: {
        'ok': True,
        'latest': {
            'date': '2026-10-02',
            'numbers': [1, 6, 7, 11, 26],
            'sourceUrl': 'https://www.lotteryusa.com/california/fantasy-5/year',
        },
    })
    latest = line_chat.reply('天天樂', store=store, latest=server.line_latest_reply,
                             history=lambda game: pytest.fail('history must remain blocked'))
    assert '01、06、07、11、26' in latest['text']
    assert '第三方，未經官方核實' in latest['text']
    assert '歷史、模型、推薦與推播仍未啟用' in latest['text']
    history = line_chat.reply('歷史 天天樂', store=store, latest=server.line_latest_reply,
                              history=lambda game: pytest.fail('history must remain blocked'))
    assert history['text'] == line_chat.PENDING
    assert server.delivery_is_blocked('ca-fantasy5')


def test_history_is_owner_scoped_paginated_and_not_in_groups(store):
    for i in range(7):
        day = NOW.date() + timedelta(days=i)
        if day.weekday() == 6:
            continue
        store.share('owner', day.isoformat(), [1, 2, 3, 4, 5], 'owner only', now=NOW)
    store.share('other', NOW.date().isoformat(), [30, 31, 32, 33, 34], 'private other', now=NOW)
    for direct, member in [(False, 'owner'), (True, '')]:
        result = response(store, '我的紀錄', member, direct)
        assert '一對一' in result['text'] and '01、02' not in result['text']
    first = response(store, '我的紀錄', 'owner', True)
    assert '30、31' not in first['text'] and '待核對' in first['text']
    next_command = first['quickReply']['items'][0]['action']['text']
    assert next_command.startswith('我的紀錄 ')
    second = response(store, next_command, 'owner', True)
    assert '30、31' not in second['text'] and '01、02' in second['text']
    assert '01、02' not in response(store, '我的紀錄 other', 'owner', True)['text']


def test_board_uses_public_store_and_hides_moderated_content(store):
    store.register('private-line-id', '公開暱稱')
    visible = store.board_write('private-line-id', '<script>user text</script>', 'visible-post-key-001', now=NOW)
    hidden = store.board_write('private-line-id', 'hidden content', 'hidden-post-key-002', now=NOW+timedelta(minutes=1))
    store.hide('admin', 'board', hidden['id'], 'test')
    result = response(store, '公開留言板')
    assert 'user text' in result['text'] and 'hidden content' not in result['text']
    assert 'private-line-id' not in json.dumps(result)
    assert response(store, f"看回覆 {hidden['id']}")['text'].endswith('不存在。')
    before = len(store.board_feed())
    assert response(store, '留言 test') is None
    assert len(store.board_feed()) == before


def test_preview_uses_session_not_query_identity_and_never_dispatches_legacy(store, monkeypatch):
    monkeypatch.setenv('LINE_LOGIN_ENABLED', '1')
    monkeypatch.setenv('LINE_LOGIN_CHANNEL_SECRET', 'local-test')
    login = LoginStore(http.ROOT / 'community_auth.sqlite3', 'local-test')
    with login.connect() as db:
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (digest('token'), 'owner', 'csrf', time.time()+600))
    store.share('owner', NOW.date().isoformat(), [1, 2, 3, 4, 5], '測試理由', now=NOW)
    store.share('other', NOW.date().isoformat(), [30, 31, 32, 33, 34], '測試理由', now=NOW)
    monkeypatch.setattr(server, 'line_message_reply', lambda event: pytest.fail('preview invoked legacy commands'))
    h = Handler()
    h.path = '/api/community/chat-preview?' + urlencode({'text':'我的紀錄', 'member':'other', 'userId':'other'})
    h.headers = {}
    http.handle(h, 'GET', True, True, set(), chat_preview=server.line_chat_preview)
    assert '01、02' not in h.result[1]['message']['text']
    h.headers = {'Cookie': COOKIE+'=token'}
    http.handle(h, 'GET', True, True, set(), chat_preview=server.line_chat_preview)
    assert '01、02' in h.result[1]['message']['text'] and '30、31' not in h.result[1]['message']['text']
    h.path = '/api/community/chat-preview?' + urlencode({'text':'通知開啟'})
    http.handle(h, 'GET', True, True, set(), chat_preview=server.line_chat_preview)
    assert h.result[1]['preview'] is True and '唯讀預覽' in h.result[1]['message']['text']
    http.handle(h, 'GET', False, True, set(), chat_preview=server.line_chat_preview)
    assert h.result[0] == 503


def test_native_replies_require_explicit_pinned_staging_enablement(monkeypatch):
    monkeypatch.delenv('LINE_CHAT_COMMANDS_ENABLED', raising=False)
    monkeypatch.setattr(server, 'fantasy5_official_trial_enabled', lambda: True)
    monkeypatch.setattr(server, 'line_notification_persistent_mount_is_verified', lambda root: True)
    assert not server.line_chat_enabled()
    monkeypatch.setenv('LINE_CHAT_COMMANDS_ENABLED', '1')
    assert server.line_chat_enabled()
    monkeypatch.setattr(server, 'fantasy5_official_trial_enabled', lambda: False)
    assert not server.line_chat_enabled()
    monkeypatch.setattr(server, 'fantasy5_official_trial_enabled', lambda: True)
    monkeypatch.setattr(server, 'line_notification_persistent_mount_is_verified', lambda root: False)
    assert not server.line_chat_enabled()


@pytest.mark.parametrize('source', [{'type':'group','userId':'owner','groupId':'g'}, {'type':'room','userId':'owner'}, {'userId':'owner'}, {'type':'user','userId':'owner','groupId':'g'}])
def test_webhook_non_direct_context_cannot_read_personal_history(store, monkeypatch, source):
    monkeypatch.setattr(server, 'line_chat_enabled', lambda: True)
    store.share('owner', NOW.date().isoformat(), [1,2,3,4,5], '測試理由', now=NOW)
    result = server.line_event_reply({'type':'message','source':source,'message':{'type':'text','text':'我的紀錄'}})
    assert '一對一' in result['text'] and '01、02' not in result['text']


def test_signed_duplicate_webhook_replies_once_and_preview_build_keeps_legacy(store, monkeypatch):
    monkeypatch.setattr(server, 'LINE_CHANNEL_SECRET', 'test-secret')
    monkeypatch.setattr(server, 'LINE_CHANNEL_ACCESS_TOKEN', 'test-token')
    monkeypatch.setattr(server, 'LINE_NOTIFICATION_STORE', LineNotificationStore(http.ROOT/'notifications.sqlite3', enabled=False, tester_ids=set()))
    monkeypatch.setattr(server, 'line_chat_enabled', lambda: True)
    replies = []
    monkeypatch.setattr(server, 'line_reply', lambda token, data: replies.append(data))
    event = {'type':'message','webhookEventId':'event-chat-test', 'replyToken':'test', 'source':{'type':'user','userId':'owner'},'message':{'type':'text','text':'彩種'}}
    body = json.dumps({'events':[event]}).encode()
    signature = base64.b64encode(hmac.new(b'test-secret', body, hashlib.sha256).digest()).decode()
    service = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True); thread.start()
    try:
        for sig, status in [('bad', 401), (signature, 200), (signature, 200)]:
            connection = HTTPConnection('127.0.0.1', service.server_port)
            connection.request('POST', '/api/line/webhook', body, {'Content-Type':'application/json','X-Line-Signature':sig})
            result = connection.getresponse(); assert result.status == status; result.read(); connection.close()
        assert len(replies) == 1 and replies[0]['quickReply']['items'][0]['action']['type'] == 'message'
    finally:
        service.shutdown(); service.server_close(); thread.join()
    monkeypatch.setattr(server, 'line_chat_enabled', lambda: False)
    assert isinstance(server.line_event_reply(event), str)


def test_reply_transport_preserves_quick_replies_without_push(monkeypatch):
    monkeypatch.setattr(server, 'LINE_CHANNEL_ACCESS_TOKEN', 'test-only')
    captured = []
    class Response:
        def __enter__(self):return self
        def __exit__(self, *args):pass
    def send(request, **kwargs):
        assert request.full_url.endswith('/message/reply')
        captured.append(json.loads(request.data)); return Response()
    monkeypatch.setattr(server.urllib.request, 'urlopen', send)
    payload = line_chat.message('test')
    server.line_reply('test-reply-token', payload)
    assert captured == [{'replyToken':'test-reply-token','messages':[payload]}]
