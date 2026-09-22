import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
import pytest
import server

@pytest.mark.parametrize('runtime,service', [('production',server.LINE_NOTIFICATION_STAGING_SERVICE_ID),('staging','other-service'),('','')])
def test_trial_rejects_other_runtime(monkeypatch,runtime,service):
    monkeypatch.setattr(server,'LINE_NOTIFICATION_RUNTIME',runtime)
    monkeypatch.setattr(server,'LINE_NOTIFICATION_SERVICE_ID',service)
    monkeypatch.setattr(server.california_fantasy5,'probe',lambda: pytest.fail('must not fetch'))
    with pytest.raises(ValueError,match='Staging-only'):
        server.fantasy5_official_trial_payload()

@pytest.mark.parametrize('valid',[False,True])
def test_http_trial_never_uses_legacy_or_sends(monkeypatch,valid):
    monkeypatch.setattr(server,'LINE_NOTIFICATION_RUNTIME','staging')
    monkeypatch.setattr(server,'LINE_NOTIFICATION_SERVICE_ID',server.LINE_NOTIFICATION_STAGING_SERVICE_ID)
    result={'ok':valid,'attempts':[{'httpStatus':200,'contentType':'text/plain'}]}
    if valid:
        result.update(drawNumber='12006',drawDate='2026-09-20',winningNumbers=[1,8,10,19,21],
            sourceUrl=server.california_fantasy5.OFFICIAL_PAGE_URL,source='test-fixture',fetchedAt=1,latencyMs=20)
    monkeypatch.setattr(server.california_fantasy5,'probe',lambda:result)
    monkeypatch.setattr(server,'california_latest',lambda:pytest.fail('legacy source forbidden'))
    monkeypatch.setattr(server,'line_push',lambda *args:pytest.fail('LINE forbidden'))
    httpd=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
    try:
        try:
            response=urllib.request.urlopen(f'http://127.0.0.1:{httpd.server_port}/api/latest?game=ca-fantasy5')
        except urllib.error.HTTPError as exc:
            response=exc
        with response:
            body=json.load(response)
            assert response.status==(200 if valid else 503)
        assert body['ok']==valid and body['notificationsEnabled'] is False
        assert ('latest' in body)==valid
        assert server.delivery_is_blocked('ca-fantasy5')
    finally:
        httpd.shutdown();httpd.server_close();thread.join()
