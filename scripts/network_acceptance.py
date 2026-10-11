"""Exercise the Docker nodes, desktop SDK, and an optional remote SDK over HTTP.

This is a network verification tool. It lists actual test HTTP supplies through
the node APIs; ordinary product startup does not load these supplies.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from a2n_node.receipt import hash_payload, verify as verify_receipt
from a2n_sdk.coordination import Budget

ROOT = Path(__file__).resolve().parents[1]
NODES = ('node-a', 'node-b', 'node-c', 'desktop')
CHECKS = []
RUN = uuid.uuid4().hex
VERSION = '1.0.0+lan-network'
SERVER_ADMIN = None
SERVER_UPSTREAM = None
PUBLIC_BUDGET = {'remote_operations': 64, 'received_bytes': 4194304, 'duration_ms': 60000,
                 'introduction_depth': 8, 'candidate_limit': 256, 'probe_operations': 6, 'max_concurrency': 3}

def api(node, path, body=None, headers=None):
    if node == 'server':
        args = list(SERVER_ADMIN)
    elif node == 'desktop':
        args = [sys.executable, '-m', 'a2n_node.product_cli', 'request']
    else:
        args = ['docker', 'exec', '-i', 'a2n-acceptance-' + node + '-1',
                'python', '-m', 'a2n_node.product_cli', 'request']
    args += ['--path', path, '--method', 'GET' if body is None else 'POST']
    if body is not None:
        args += ['--body', '-']
    for k, v in (headers or {}).items():
        args += ['--header', k + ':' + v]
    result = subprocess.run(args, input=json.dumps(body) if body is not None else None,
                            text=True, encoding='utf-8', capture_output=True,
                            cwd=ROOT, timeout=90)
    if result.returncode:
        raise RuntimeError(f'{node} {path}: {result.stderr[-600:]}')
    return json.loads(result.stdout)

def check(name, passed, **facts):
    CHECKS.append({'name': name, 'passed': bool(passed), 'facts': facts})
    print(('PASS ' if passed else 'FAIL ') + name, flush=True)
    if not passed:
        raise AssertionError(name + ': ' + str(facts))


def candidate_page(node, search_id, seller_did, service_id):
    prefix = '/v1/coord/searches/' + search_id
    def has_path(page):
        return any(item['key'] == {'provider_did': seller_did, 'service_id': service_id}
                   and any(row['card'].get('version') == VERSION and
                           (node != 'server' or
                            row['card'].get('x-a2n', {}).get('relay', {}).get('protocol') == 'a2n-sealed-relay/1')
                           for row in item['cards']) for item in page['items'])
    page = api(node, prefix + '/candidates?limit=100')
    # A count-satisfied candidate can still lack the path this buyer needs.
    # Continue the same search, then pause once the verified relay card exists.
    for round_index in range(4):
        if has_path(page):
            state = api(node, prefix)
            if state['state'] == 'RUNNING':
                state = api(node, prefix + '/pause', {}, {
                    'Idempotency-Key': RUN + search_id + '-pause', 'If-Match': '"' + str(state['revision']) + '"'})
            if node == 'server' or round_index:
                check(node + ' 续查核对 ' + service_id + ' 可用路径', True,
                      search_id=search_id, round=state['round'], state=state['state'])
            return page
        if round_index == 3:
            break
        state = api(node, prefix)
        if state['state'] != 'RUNNING':
            api(node, prefix + '/resume', {'round_budget': PUBLIC_BUDGET,
                'preferences': {'min_candidates': 256}}, {
                    'Idempotency-Key': RUN + search_id + '-resume-' + str(round_index),
                    'If-Match': '"' + str(state['revision']) + '"'})
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            page = api(node, prefix + '/candidates?limit=100')
            if has_path(page) or api(node, prefix)['state'] != 'RUNNING':
                break
            time.sleep(.5)
    raise RuntimeError('EXPECTED_ROUTE_NOT_DISCOVERED_WITHIN_THREE_BOUNDED_ROUNDS')

def run(identities):
    for node in NODES:
        identities[node] = api(node, '/v1/runtime')['node_did']
    check(f'{len(NODES)} 个不同身份的真实节点', len(set(identities.values())) == len(NODES), identities=identities)
    for node in NODES:
        snapshot = api(node, '/v1/runtime')
        check(node + ' 必须承担公共发现', snapshot['public_service']['services']['discovery'] is True)
        sid = 'network-' + node + '-add'
        existing = any(b['service_id'] == sid for b in snapshot['bindings'])
        if existing and api(node, '/v1/publish', {'service_id': sid})['card']['version'] != VERSION:
            api(node, '/v1/bindings/remove', {'service_id': sid})
            existing = False
        if not existing:
            card = {'name': 'Network HTTP arithmetic (' + node + ')', 'version': VERSION,
                    'description': 'Actual HTTP arithmetic upstream used for network verification',
                    'skills': [{'id': 'add'}], 'x-a2n': {
                        'uid': str(uuid.uuid5(uuid.NAMESPACE_URL, identities[node] + '/' + sid)),
                        'price_book': {'add': {'CNY': {'dimensions': [
                            {'key': 'call_count', 'amount': 0, 'per': 1}]} }},
                        'acceptance_template': {'version': '1', 'required_fields': ['sum']}}}
            endpoint = (SERVER_UPSTREAM if node == 'server' else
                        'http://127.0.0.1:18884/invoke' if node == 'desktop' else 'http://agents:9000/invoke')
            api(node, '/v1/bindings/http', {'card': card, 'service_id': sid, 'protocol': 'json',
                'endpoint': endpoint,
                'listed': True})
        published = api(node, '/v1/publish', {'service_id': sid})
        check(node + ' 通过管理接口上架实际 HTTP 供给', published['card']['version'] == VERSION)
    # Local referrals remain a ring. The remote node also learns outbound
    # mailbox registrations; server -> private seller must use a sealed card.
    for buyer in NODES:
        for seller in NODES:
            if buyer == seller:
                continue
            spec = {'skill': 'add',
                'required': {'provider_did': identities[seller], 'version': VERSION},
                'preferences': {'min_candidates': 1}}
            if SERVER_ADMIN:
                spec['round_budget'] = PUBLIC_BUDGET
            search = api(buyer, '/v1/coord/searches', spec, {'Idempotency-Key': RUN + buyer + seller})
            end = time.monotonic() + (90 if SERVER_ADMIN else 40)
            state = search
            while time.monotonic() < end:
                state = api(buyer, '/v1/coord/searches/' + search['search_id'])
                if state['state'] != 'RUNNING':
                    break
                time.sleep(.15)
            page = candidate_page(buyer, search['search_id'], identities[seller], 'network-' + seller + '-add')
            candidate = next((x for x in page['items'] if x['key']['provider_did'] == identities[seller]
                              and x['key']['service_id'] == 'network-' + seller + '-add'), None)
            check(buyer + ' 逐级发现 ' + seller, candidate is not None, state=state['state'])
            cards = [row['card'] for row in candidate['cards'] if row['card']['version'] == VERSION]
            if buyer == 'server':
                cards = [card for card in cards
                         if (card.get('x-a2n', {}).get('relay') or {}).get('protocol') == 'a2n-sealed-relay/1']
                check(buyer + ' 获得 ' + seller + ' 密封中继路径', bool(cards))
            current = cards[0]
            projection = api(buyer, '/v1/projections', {'card': current})
            scope = projection['projection_id']
            task = RUN + '-' + buyer + '-' + seller
            params = {'message': {'role': 'user', 'messageId': task,
                     'parts': [{'kind': 'data', 'data': {'a': 17, 'b': 25}}]}, 'metadata': {'skill': 'add'}}
            reply = api(buyer, '/a2a/' + scope, {'jsonrpc': '2.0', 'id': task,
                        'method': 'message/send', 'params': params})
            result = reply.get('result') or reply
            original_state = result.get('status', {}).get('state')
            if original_state in {'unknown', 'working', 'submitted'}:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    reply = api(buyer, '/a2a/' + scope, {'jsonrpc': '2.0', 'id': task + ':get',
                                'method': 'tasks/get', 'params': {'id': task}})
                    result = reply.get('result') or reply
                    if result.get('status', {}).get('state') not in {'unknown', 'working', 'submitted'}:
                        break
                    time.sleep(.5)
            data = ((result.get('artifacts') or [{}])[0].get('parts') or [{}])[0].get('data')
            check(buyer + ' 实际调用 ' + seller,
                  result.get('status', {}).get('state') == 'completed' and data == {'sum': 42},
                  state=result.get('metadata', {}).get('a2nState'))
            receipt = result.get('metadata', {}).get('a2nReceipt')
            valid, reason = verify_receipt(receipt)
            matches = bool(valid and receipt['caller_did'] == identities[buyer]
                           and receipt['provider_did'] == identities[seller]
                           and receipt['output_hash'] == hash_payload({'sum': 42}))
            check(buyer + ' ↔ ' + seller + ' 签名收据', matches, signature=reason,
                  call_id=result.get('id'), route=current.get('url'))
    return identities

def main():
    global NODES, SERVER_ADMIN, SERVER_UPSTREAM
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-admin', help='JSON argv prefix for remote a2n-admin (SSH key authentication or a private bridge)')
    parser.add_argument('--server-upstream', help='Actual HTTP test upstream accessible from the server, e.g. http://127.0.0.1:9000/invoke')
    args = parser.parse_args()
    if bool(args.server_admin) != bool(args.server_upstream):
        parser.error('server-admin and server-upstream must be supplied together')
    if args.server_admin:
        try:
            SERVER_ADMIN = json.loads(args.server_admin)
        except ValueError:
            parser.error('server-admin must be a JSON array of command arguments')
        if not isinstance(SERVER_ADMIN, list) or not SERVER_ADMIN or any(
                not isinstance(item, str) or not item for item in SERVER_ADMIN):
            parser.error('server-admin must be a nonempty JSON array of nonempty strings')
        SERVER_UPSTREAM = args.server_upstream
        NODES = (*NODES, 'server')
    identities = {}
    error = None
    try:
        run(identities)
    except Exception as exc:
        error = str(exc)
        print(error, file=sys.stderr)
    report = {'at': datetime.now(timezone.utc).isoformat(), 'passed': error is None,
              'nodes': identities, 'checks': CHECKS, 'error': error,
              'server': {'requested': bool(SERVER_ADMIN), 'covered': 'server' in identities,
                         'status': ('Verified with all four local nodes' if SERVER_ADMIN and error is None else
                                    'Remote verification failed' if SERVER_ADMIN else
                                    'The remote server is outside this local test')}}
    if SERVER_ADMIN:
        report['discovery_round_budget'] = PUBLIC_BUDGET
    (ROOT / 'artifacts').mkdir(exist_ok=True)
    name = 'network-five.json' if SERVER_ADMIN else 'network-local.json'
    (ROOT / 'artifacts' / name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if error is None else 1)

if __name__ == '__main__':
    main()
