import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from store import Store
from travel import nights


@pytest.fixture
def db(tmp_path):
    db = Store(tmp_path / 'state.sqlite3')
    db.issue('alice'); db.issue('bob')
    return db


def start(db, student='alice'):
    return db.call(student, 'start_task', {'kind': 'travel'})['task_id']


def invoke(db, tid, tool, **args):
    return db.call('alice', tool, {'task_id': tid, **args})


def trip(db, tid):
    data = invoke(db, tid, 'inspect_task')
    out = next(f for f in data['flights'] if f['available'] and f['direction']=='outbound'
               and f['arrive_date'] > f['depart_date'])
    # Fixed generated itinerary: return four days after initial departure.
    returns = sorted((f for f in data['flights'] if f['direction']=='return'),key=lambda f:f['depart_date'])
    back = returns[1]
    hotel = next(h for h in data['hotels'] if h['name']=='Маяк')
    return data,out,back,hotel


def book(db, tid, out, back, hotel, check_in=None, check_out=None):
    results=[invoke(db,tid,'book_flight',flight_id=out['id']),
             invoke(db,tid,'book_flight',flight_id=back['id']),
             invoke(db,tid,'book_hotel',hotel_id=hotel['id'],
                    check_in=check_in or out['arrive_date'], check_out=check_out or back['depart_date'])]
    assert all(r['ok'] for r in results)
    return results


def test_success_receipt_restart_and_frozen_state(db):
    tid=start(db); _,out,back,hotel=trip(db,tid)
    assert not invoke(db,tid,'finish')['passed']
    bookings=book(db,tid,out,back,hotel)
    result=invoke(db,tid,'finish'); assert result['passed']
    assert result['total_rub'] <= result['budget_rub']
    assert Store(db.path).call('alice','finish',{'task_id':tid})['receipt']==result['receipt']
    assert invoke(db,tid,'cancel_booking',booking_id=bookings[0]['booking']['id'])['error']=='task_closed'


def test_unavailable_flight_and_hotel_have_no_side_effects(db):
    tid=start(db); d,out,back,_=trip(db,tid)
    sold=next(f for f in d['flights'] if not f['available'])
    assert invoke(db,tid,'book_flight',flight_id=sold['id'])['error']=='flight_unavailable'
    hotel=next(h for h in d['hotels'] if h['name']=='Центральная')
    result=invoke(db,tid,'book_hotel',hotel_id=hotel['id'],check_in=out['arrive_date'],check_out=back['depart_date'])
    assert result['error']=='hotel_unavailable' and result['unavailable_nights']==[out['arrive_date']]
    assert invoke(db,tid,'list_bookings')['bookings']==[]


@pytest.mark.parametrize('mistake',['departure_not_arrival','missing_last_night','wrong_return','budget','extra_booking'])
def test_incoherent_trip_rejected(db,mistake):
    tid=start(db); d,out,back,hotel=trip(db,tid)
    dates={}
    if mistake=='departure_not_arrival': dates['check_in']=out['depart_date']
    if mistake=='missing_last_night':
        dates['check_out']=nights(out['arrive_date'],back['depart_date'])[-1]
    if mistake=='wrong_return':
        back=min((f for f in d['flights'] if f['direction']=='return'),key=lambda f:f['depart_date'])
    if mistake=='budget': hotel=next(h for h in d['hotels'] if h['name']=='Панорама')
    book(db,tid,out,back,hotel,**dates)
    if mistake=='extra_booking':
        extra=next(f for f in d['flights'] if f['available'] and f['direction']=='outbound' and f['id']!=out['id'])
        invoke(db,tid,'book_flight',flight_id=extra['id'])
    assert not invoke(db,tid,'finish')['passed']


def test_cancel_and_replan(db):
    tid=start(db); d,out,back,hotel=trip(db,tid)
    expensive=next(h for h in d['hotels'] if h['name']=='Панорама')
    bookings=book(db,tid,out,back,expensive)
    assert not invoke(db,tid,'finish')['passed']
    bad_id=bookings[-1]['booking']['id']
    assert invoke(db,tid,'cancel_booking',booking_id=bad_id)['ok']
    assert invoke(db,tid,'cancel_booking',booking_id=bad_id)['ok']
    invoke(db,tid,'book_hotel',hotel_id=hotel['id'],check_in=out['arrive_date'],check_out=back['depart_date'])
    assert invoke(db,tid,'finish')['passed']
    assert any(json.loads(e['result']).get('booking',{}).get('status')=='cancelled' for e in db.events('alice'))


def test_concurrent_replays_do_not_duplicate(db):
    tid=start(db); _,out,_,_=trip(db,tid)
    with ThreadPoolExecutor(4) as pool:
        results=list(pool.map(lambda _:invoke(db,tid,'book_flight',flight_id=out['id']),range(8)))
    assert len({r['booking']['id'] for r in results})==1
    assert len(invoke(db,tid,'list_bookings')['bookings'])==1


@pytest.mark.parametrize('start_date,end_date',[('bad','2027-04-01'),('2027-04-01','2027-04-01'),
                                               ('2027-04-02','2027-04-01'),('20270401','2027-04-03')])
def test_invalid_dates(db,start_date,end_date):
    tid=start(db); _,_,_,hotel=trip(db,tid)
    assert invoke(db,tid,'book_hotel',hotel_id=hotel['id'],check_in=start_date,check_out=end_date)['error']=='invalid_dates'


def test_isolation_and_foreign_calls_do_not_consume_budget(db):
    tid=start(db); assert start(db)==tid; assert start(db,'bob')!=tid
    _,out,back,hotel=trip(db,tid); booked=book(db,tid,out,back,hotel)
    for _ in range(101):
        assert db.call('bob','cancel_booking',{'task_id':tid,'booking_id':booked[0]['booking']['id']})['error']=='task_not_found'
    assert invoke(db,tid,'finish')['passed']
    assert next(r for r in db.report() if r['task_id']==tid)['calls']==7


def test_auth_rotation_revocation_expiry(db):
    old=db.issue('alice'); new=db.issue('alice')
    assert db.authenticate(old) is None and db.authenticate(new)=='alice'
    db.revoke('alice'); assert db.authenticate(new) is None
    assert db.call('alice','start_task',{'kind':'travel'})['error']=='access_expired'
    new=db.issue('alice')
    with db.connect() as con: con.execute('UPDATE students SET expires=0 WHERE student=?',('alice',))
    assert db.authenticate(new) is None


def test_budget_expiry_reset_and_old_work_retired(db):
    assert {r['status'] for r in db.report()}=={'not_started'}
    assert db.call('alice','start_task',{'kind':'delivery'})['error']=='unknown_kind'
    tid=start(db)
    for _ in range(99): invoke(db,tid,'list_bookings')
    assert invoke(db,tid,'finish')['error']=='call_budget_exhausted'
    db.reset('alice','travel'); new=start(db); assert new!=tid
    assert any(e['task']==tid for e in db.events('alice'))
    with db.connect() as con: con.execute('UPDATE tasks SET expires=0 WHERE id=?',(new,))
    assert invoke(db,new,'finish')['error']=='task_expired'


def test_forged_final_and_empty_trip_fail(db):
    tid=start(db)
    assert not invoke(db,tid,'finish',passed=True,receipt='fake')['passed']
    assert all(r['receipt'] is None for r in db.report())
