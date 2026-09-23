from datetime import datetime, timedelta
import pytest
from community import CommunityStore, TAIPEI

NOW=datetime(2026,9,22,12,tzinfo=TAIPEI)


def test_empty_read_has_no_side_effect(tmp_path):
    store=CommunityStore(tmp_path/'new.sqlite')
    result=store.snapshot('2026-09-22')
    assert result['sampleSize']==0 and not store.path.exists()


def test_one_vote_per_member_with_audit_and_correct_denominator(tmp_path):
    store=CommunityStore(tmp_path/'community.sqlite')
    a=store.share('verified-member-a','2026-09-22',[1,2,3,4,5],'理由',now=NOW)
    assert not store.share('verified-member-a','2026-09-22',[5,4,3,2,1],'理由',now=NOW)['changed']
    store.share('verified-member-a','2026-09-22',[2,3,4,5,6],'修正',now=NOW)
    store.share('verified-member-b','2026-09-22',[2,7,8,9,10],'理由',now=NOW)
    result=store.snapshot('2026-09-22')
    assert result['sampleSize']==2
    assert result['numbers'][1]=={'number':2,'count':2,'share':1}
    assert result['numbers'][0]['count']==0
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM revisions WHERE pick_id=?',(a['id'],)).fetchone()[0]==2


def test_cutoff_future_date_and_identity(tmp_path):
    store=CommunityStore(tmp_path/'community.sqlite')
    for member,day,now in [('', '2026-09-22', NOW),('a','2026-09-20',NOW),('a','2026-10-01',NOW),('a','2026-09-22',NOW.replace(hour=20,minute=25))]:
        with pytest.raises(ValueError):store.share(member,day,[1,2,3,4,5],'理由',now=now)
    with pytest.raises(ValueError):store.share('a','2026-09-22',[True,2,3,4,5],'理由',now=NOW)


def test_comments_are_threaded_and_throttled(tmp_path):
    store=CommunityStore(tmp_path/'community.sqlite')
    pick=store.share('a','2026-09-22',[1,2,3,4,5],'理由',now=NOW)
    store.comment('b',pick['id'],'討論',now=NOW)
    with pytest.raises(ValueError):store.comment('b',pick['id'],'重複',now=NOW)
    with pytest.raises(ValueError):store.comment('c',999,'不存在',now=NOW)
    store.comment('b',pick['id'],'回覆',now=NOW+timedelta(minutes=1))
