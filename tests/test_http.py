import asyncio
import json
import re
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'service'))
sys.path.insert(0, str(ROOT / 'examples'))
from store import Store
from mcp_client import connect, call, schemas


@pytest.fixture(scope='module')
def live(tmp_path_factory):
    folder = tmp_path_factory.mktemp('verification-http')
    config = json.loads((ROOT / 'config/events.json').read_text())
    config['events'][1]['request']['budget_rub'] = 31000
    config_path = folder / 'custom-events.json'
    config_path.write_text(json.dumps(config))
    store = Store(folder / 'db.sqlite3', config_path)
    alice = store.issue('alice'); bob = store.issue('bob', event_id='kazan-engineering')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}/mcp'
    with open(folder / 'server.log', 'w') as log:
        proc = subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1',
                                 '--port',str(port),'--no-access-log'], cwd=ROOT/'service',
                                env={**os.environ,'VERIFY_DB':store.path,'VERIFY_CONFIG':str(config_path)}, stdout=log,stderr=log)
        try:
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError((folder/'server.log').read_text())
                try:
                    if httpx.get(url).status_code == 401: break
                except httpx.ConnectError: pass
                time.sleep(.05)
            else: raise RuntimeError('server startup timeout')
            yield url,store,alice,bob
        finally:
            proc.terminate(); proc.wait(timeout=10)


def test_http_access_boundaries(live):
    url,_,alice,_ = live
    assert httpx.post(url,json={}).status_code == 401
    assert httpx.post(url,headers={'Authorization':'Bearer incorrect'},json={}).status_code == 401
    headers={'Authorization':f'Bearer {alice}', 'Origin':'https://untrusted.example'}
    assert httpx.post(url,headers=headers,json={}).status_code == 403
    headers={'Authorization':f'Bearer {alice}', 'Host':'untrusted.example'}
    assert httpx.post(url,headers=headers,json={}).status_code == 421
    assert httpx.post(url,headers={'Authorization':f'Bearer {alice}'},content=b'x'*17000).status_code == 413


@pytest.mark.asyncio
async def test_real_mcp_two_principals(live):
    url,store,alice,bob = live
    async with connect(url,alice) as a, connect(url,bob) as b:
        names={x['name'] for x in await schemas(a)}
        assert names == {'start_task','inspect_task','book_flight','book_hotel','cancel_booking','list_bookings','finish'}
        results = await asyncio.gather(call(a,'start_task',{'kind':'travel'}),call(b,'start_task',{'kind':'travel'}))
        ta,tb = [x['task_id'] for x in results]
        assert ta != tb
        assigned = await call(b, 'inspect_task', {'task_id': tb})
        assert assigned['event']['id'] == 'kazan-engineering'
        assert '31000 рублей' in assigned['user_request']
        assert (await call(b,'inspect_task',{'task_id':ta}))['error']=='task_not_found'
        data=await call(a,'inspect_task',{'task_id':ta})
        depart,back_date=re.findall(r'\d{4}-\d{2}-\d{2}',data['user_request'])
        out=next(f for f in data['flights'] if f['direction']=='outbound' and f['available'] and f['arrive_date']>depart)
        back=next(f for f in data['flights'] if f['direction']=='return' and f['depart_date']==back_date)
        hotel=next(h for h in data['hotels'] if h['name']=='Маяк')
        await call(a,'book_flight',{'task_id':ta,'flight_id':out['id']})
        await call(a,'book_flight',{'task_id':ta,'flight_id':back['id']})
        await call(a,'book_hotel',{'task_id':ta,'hotel_id':hotel['id'],'check_in':out['arrive_date'],'check_out':back_date})
        result=await call(a,'finish',{'task_id':ta}); assert result['passed']
        assert next(x for x in store.report() if x['task_id']==ta)['receipt']==result['receipt']
        missing=await call(a,'book_flight',{'task_id':ta})
        assert missing['error']=='mcp_tool_error'
    store.revoke('bob')
    assert httpx.post(url,headers={'Authorization':f'Bearer {bob}'},json={}).status_code==401


def test_http_rate_limit(live):
    url,store,_,_ = live
    token=store.issue('rate-test')
    with httpx.Client(headers={'Authorization':f'Bearer {token}'}) as client:
        statuses=[client.post(url,json={}).status_code for _ in range(121)]
    assert statuses[-1]==429
