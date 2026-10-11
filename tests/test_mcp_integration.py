"""Real MCP HTTP sessions and A2N market calls; no stubbed network transport."""
import base64
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from a2n_node.daemon import Daemon
from a2n_node.protection import EnvironmentProtector
from a2n_sdk.mcp import MCPClient, MCPUpstream
from a2n_sdk.ports import CallRequest


@contextmanager
def mcp_server(*, sse=False, lost=False):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            method = body['method']
            if method != 'initialize':
                assert self.headers.get('MCP-Session-Id') == 'test-session'
                assert self.headers.get('MCP-Protocol-Version') == '2025-11-25'
            if method == 'notifications/initialized':
                self.send_response(202); self.end_headers(); return
            if method == 'initialize':
                result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'actual-test-tools', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'add', 'description': 'Add two integers',
                    'inputSchema': {'type': 'object', 'properties': {'a': {'type': 'integer'}, 'b': {'type': 'integer'}}, 'required': ['a', 'b']}}]}
            elif method == 'tools/call':
                params = body['params']; calls.append(params)
                if lost:
                    self.close_connection = True; return
                result = {'content': [{'type': 'text', 'text': 'calculated'}],
                          'structuredContent': {'sum': params['arguments']['a'] + params['arguments']['b']}}
            else:
                raise AssertionError(method)
            frame = {'jsonrpc': '2.0', 'id': body['id'], 'result': result}
            data = ('data: ' + json.dumps(frame) + '\n\n').encode() if sse else json.dumps(frame).encode()
            self.send_response(200); self.send_header('MCP-Session-Id', 'test-session')
            self.send_header('Content-Type', 'text/event-stream' if sse else 'application/json')
            self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:' + str(server.server_port) + '/mcp', calls
    finally: server.shutdown(); server.server_close(); thread.join()


def test_streamable_http_json_and_sse_negotiate_session_without_executing_on_preview():
    for sse in (False, True):
        with mcp_server(sse=sse) as (endpoint, calls):
            client = MCPClient(endpoint)
            assert client.tools()[0]['name'] == 'add' and not calls
            assert client.call('add', {'a': 2, 'b': 4}, 'one')['structuredContent'] == {'sum': 6}
            assert len(calls) == 1


def test_existing_mcp_tool_mounts_then_runs_ten_signed_public_samples_and_survives_restart(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    with mcp_server() as (endpoint, calls):
        nodes = [Daemon(tmp_path / str(i), port=0, protector=protector, beacon=False,
            coord_allow_networks=['127.0.0.0/8']).start() for i in range(2)]
        try:
            provider, buyer = nodes
            for node in nodes: node.management.discovery_public_base = node.runtime.local_base_url
            status, preview = provider.management.command('/v1/integrations/mcp/preview', {'endpoint': endpoint})
            assert status == 200 and not calls and preview['executed_tools'] == 0
            import urllib.request
            for name in ('integrations.js', 'settlement.js'):
                assert urllib.request.urlopen(provider.runtime.local_base_url+'/console/'+name).status == 200
            status, mounted = provider.management.command('/v1/bindings/http', {
                'card': preview['tools'][0]['card'], 'endpoint': endpoint, 'protocol': 'mcp', 'service_id': 'mcp-add'})
            buyer.runtime.import_agent(mounted['card'], projection_id='mcp-use')
            denied = provider.runtime.bindings.get('mcp-add').upstream.invoke(CallRequest(
                task_id='different-tool', skill='expensive-unlisted-tool', payload={}))
            assert not denied.ok and not calls
            for i in range(10):
                request = CallRequest(task_id='mcp-' + str(i), skill='add', payload={'a': i, 'b': 3})
                out = buyer.calls.invoke('mcp-use', request)
                assert out.ok and out.result == {'sum': i + 3}
                assert buyer.calls.invoke('mcp-use', request).result == out.result
            assert len(calls) == 10 and len(provider.trials.samples('mcp-add')) == 10
            assert not provider.store.items('payment_orders') and not buyer.store.items('points_journal')
            provider.stop()
            restored = Daemon(tmp_path / '0', port=0, protector=protector, beacon=False).start()
            nodes[0] = restored
            assert isinstance(restored.runtime.bindings.get('mcp-add').upstream, MCPUpstream)
        finally:
            for node in reversed(nodes): node.stop()


def test_lost_tool_response_is_unknown_and_a2n_task_replay_never_resends_it(tmp_path):
    protector = EnvironmentProtector(base64.b64encode(os.urandom(32)).decode())
    with mcp_server(lost=True) as (endpoint, calls):
        node = Daemon(tmp_path / 'node', port=0, protector=protector, beacon=False).start()
        try:
            from a2n_sdk.mcp import tool_card
            tool = MCPClient(endpoint).tools()[0]
            node.management.command('/v1/bindings/http', {'card': tool_card(endpoint, tool),
                'endpoint': endpoint, 'protocol': 'mcp', 'service_id': 'lost-tool'})
            request = CallRequest(task_id='lost', skill='add', payload={'a': 1, 'b': 2})
            assert node.calls.invoke('lost-tool', request).state == 'DELIVERY_UNKNOWN'
            assert node.calls.invoke('lost-tool', request).state == 'DELIVERY_UNKNOWN'
            assert len(calls) == 1
        finally: node.stop()
