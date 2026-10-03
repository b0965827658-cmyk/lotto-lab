from datetime import timedelta
import json
import pytest
from community import CommunityStore
from test_community import NOW


def test_free_post_reply_retry_and_privacy(tmp_path):
 s=CommunityStore(tmp_path/'board.sqlite');s.register('private-a','甲會員');s.register('private-b','乙會員')
 p=s.board_write('private-a','不用日期也能聊','request-key-00001',now=NOW)
 assert not s.board_write('private-a','不用日期也能聊','request-key-00001',now=NOW)['changed']
 with pytest.raises(ValueError):s.board_write('private-a','改內容','request-key-00001',now=NOW)
 with pytest.raises(ValueError):s.board_write('private-a','太快','request-key-00002',now=NOW)
 s.board_write('private-a','第二篇','request-key-00002',now=NOW+timedelta(seconds=30))
 r=s.board_write('private-b','回覆','request-key-00003',parent_id=p['id'],now=NOW)
 assert s.board_feed()[-1]['replies']==1
 assert 'private-' not in json.dumps(s.board_feed())
 assert s.board_feed(parent_id=p['id'])[0]['id']==r['id']
 s.hide('admin','board',r['id'],'不當回覆');assert s.board_feed()[-1]['replies']==0
 s.hide('admin','board',p['id'],'隱藏主題')
 with pytest.raises(ValueError):s.board_feed(parent_id=p['id'])
 with pytest.raises(ValueError):s.board_write('private-b','回覆隱藏文','request-key-00004',parent_id=p['id'],now=NOW+timedelta(minutes=1))
 assert len(s.board_feed())==1


def test_board_validation_and_pagination(tmp_path):
 s=CommunityStore(tmp_path/'board.sqlite')
 assert s.board_feed()==[] and not s.path.exists()
 with pytest.raises(ValueError):s.board_write('unregistered','內容','request-key-00001',now=NOW)
 s.register('a','會員甲')
 for body in ('',' '*10,'a'*2001):
  with pytest.raises(ValueError):s.board_write('a',body,'request-key-00001',now=NOW)
 for i in range(32):s.board_write('a',f'文{i}',f'request-key-{i:05}',category='question',now=NOW+timedelta(minutes=i))
 first=s.board_feed(category='question');second=s.board_feed(before=first[-1]['id'],category='question')
 assert len(first)==30 and len(second)==2 and not {p['id'] for p in first}&{p['id'] for p in second}
 assert s.board_feed(category='numbers')==[]
 s.report('a','board',first[0]['id'],'測試檢舉');assert s.reports()[0]['kind']=='board'
 assert s.snapshot('2026-09-22')['sampleSize']==0
