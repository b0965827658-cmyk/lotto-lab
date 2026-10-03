import json
from datetime import datetime as RealDatetime
from email.message import Message
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import pytest
import california_fantasy5_official as f5

PAYLOAD = {"games": [{"name": "Fantasy 5", "draws": [{"drawNumber": 12006,
    "drawDate": "2026-09-20", "winningNumbers": [1, 8, 10, 19, 21]}]}]}

@pytest.fixture(autouse=True)
def clock(monkeypatch):
    class Clock(RealDatetime):
        @classmethod
        def now(cls, tz=None):
            return RealDatetime(2026, 9, 21, 12, tzinfo=f5.PACIFIC).astimezone(tz)
    monkeypatch.setattr(f5, 'datetime', Clock)

class Response:
    def __init__(self, body, media='application/json', url=f5.API_URL, status=200):
        self.body = body.encode()
        self.headers = Message()
        self.headers['Content-Type'] = media
        self.url, self.status = url, status
    def geturl(self): return self.url
    def read(self, limit): return self.body[:limit]
    def __enter__(self): return self
    def __exit__(self, *args): pass

def responses(*values):
    return patch('california_fantasy5_official.urllib.request.OpenerDirector.open', side_effect=values)

def test_maintenance_fallback_and_repeat(tmp_path):
    page = '<script type="application/json">' + json.dumps(PAYLOAD) + '</script>'
    with responses(*[r for _ in range(2) for r in (
        Response('<title>503 Error Service Unavailable</title>', 'text/plain'),
        Response(page, 'text/html', f5.OFFICIAL_PAGE_URL))]):
        a = f5.probe(ledger_path=tmp_path/'probe.sqlite')
        b = f5.probe(ledger_path=tmp_path/'probe.sqlite')
    assert a['ok'] and a['inserted'] and b['ok'] and not b['inserted']
    assert a['attempts'][0]['httpStatus'] == 200
    assert 'maintenance' in a['attempts'][0]['error']
    assert a['source'] == 'calottery-page-json'
    assert all('fetchedAt' in x and 'latencyMs' in x for x in a['attempts'])

@pytest.mark.parametrize('body,media', [('<html>503</html>','application/json'),
    (json.dumps(PAYLOAD),'text/plain'), ('{}','application/json'),
    ('{"games":[],"games":[]}','application/json')])
def test_invalid_api_text_page(body, media):
    with responses(Response(body,media), Response('Fantasy 5 Draw 12006 1 8 10 19 21','text/html',f5.OFFICIAL_PAGE_URL)):
        result=f5.probe()
    assert not result['ok'] and len(result['attempts']) == 2

def test_page_discovered_endpoint():
    endpoint='https://www.calottery.com/test-fixture.json'
    with responses(Response('{}'),Response('<div data-api-url="'+endpoint+'"></div>','text/html',f5.OFFICIAL_PAGE_URL),Response(json.dumps(PAYLOAD),url=endpoint)):
        result=f5.probe()
    assert result['ok'] and result['sourceUrl']==endpoint and result['source']=='calottery-page-xhr'

@pytest.mark.parametrize('url',['https://evil.example/result','https://www.calottery.com.evil.example/result',
    'http://www.calottery.com/result','https://user@www.calottery.com/result','https://www.calottery.com:444/result'])
def test_reject_external_before_request(url):
    with pytest.raises(ValueError): f5.official_url(url)
    with pytest.raises(ValueError): f5.OfficialRedirect().redirect_request(None,None,302,'',{},url)

@pytest.mark.parametrize('changes',[
    {'winningNumbers':[True,8,10,19,21]}, {'winningNumbers':[1.5,8,10,19,21]},
    {'winningNumbers':[2,8,10,19,21]}, {'drawNumber':True}, {'drawDate':'2026-09-19'},
    {'drawDate':'garbage'}, {'drawNumber':12008,'drawDate':'2026-09-22'},
    {'drawNumber':12004,'drawDate':'2026-09-18'}])
def test_schema_history_freshness(changes):
    payload=json.loads(json.dumps(PAYLOAD)); payload['games'][0]['draws'][0].update(changes)
    with responses(Response(json.dumps(payload)),Response('','text/html',f5.OFFICIAL_PAGE_URL)):
        assert not f5.probe()['ok']

def test_concurrent_insert_history_conflict(tmp_path):
    from dataclasses import replace
    draw=f5.Fantasy5Draw('12006','2026-09-20',(1,8,10,19,21),'fixture',1,2)
    ledger=tmp_path/'ledger.sqlite'
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _:f5.record_draw(ledger,draw),range(8)))==1
    with pytest.raises(ValueError,match='history conflict'):
        f5.record_draw(ledger,replace(draw,numbers=(2,8,10,19,21)))

def test_external_page_endpoint_ignored():
    page=f5.PageData(f5.OFFICIAL_PAGE_URL)
    page.feed('<div data-api-url="https://thirdparty.example/result"></div>')
    assert page.endpoints==[]

def test_repeat_across_process_restart(tmp_path):
    import subprocess
    import sys
    code = "import california_fantasy5_official as f; import sys; print(f.record_draw(sys.argv[1], f.Fantasy5Draw('12006','2026-09-20',(1,8,10,19,21),'fixture',1,2)))"
    command = [sys.executable, '-c', code, str(tmp_path/'restart.sqlite')]
    assert subprocess.check_output(command, text=True).strip() == 'True'
    assert subprocess.check_output(command, text=True).strip() == 'False'
