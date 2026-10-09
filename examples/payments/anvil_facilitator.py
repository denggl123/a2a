"""LOCAL TEST ONLY: Anvil EIP-3009 facilitator, test token and durable receipts."""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from eth_abi import encode
from eth_utils import keccak
from a2n_node.x402.evm import EvmRPC, EvmVerifier
from a2n_node.x402.http import HTTPClient
from a2n_sdk.x402.protocol import validate_payload, validate_requirement

RPC=EvmRPC(os.environ.get('TEST_RPC','http://a2n-payment-testchain:8545'),HTTPClient(allow_http=True))
if int(RPC.call('eth_chainId',[]),16)!=31337 or 'anvil' not in RPC.call('web3_clientVersion',[]).lower():
    raise RuntimeError('THIS_EXAMPLE_REQUIRES_LOCAL_ANVIL')
ROOT=Path('/state');ROOT.mkdir(exist_ok=True);STATE=ROOT/'public-test-state.json'
lock=threading.RLock()
sender=RPC.call('eth_accounts',[])[3]  # Public Anvil-only account, never a real wallet.
def save():
    temporary=STATE.with_suffix('.tmp');temporary.write_text(json.dumps(state));temporary.replace(STATE)
def receipt(tx):
    until=time.monotonic()+5
    row=None
    while time.monotonic()<until:
        row=RPC.call('eth_getTransactionReceipt',[tx])
        if row:break
        time.sleep(.05)
    if not row or int(row['status'],16)!=1:raise ValueError('TEST_TRANSACTION_FAILED')
    return row
if STATE.exists():state=json.loads(STATE.read_text())
else:
    artifact=json.loads(Path('/app/PaymentTestToken.json').read_text())
    assert hashlib.sha256(Path('/app/PaymentTestToken.sol').read_bytes()).hexdigest()==artifact['source_sha256']
    tx=RPC.call('eth_sendTransaction',[{'from':sender,'data':'0x'+artifact['bytecode'],'gas':hex(3000000)}])
    state={'token':receipt(tx)['contractAddress'],'receipts':{}};save()
if RPC.call('eth_getCode',[state['token'],'latest'])=='0x':raise RuntimeError('TEST_CHAIN_STATE_MISMATCH')

def call_data(payload):
    auth=payload['payload']['authorization'];sig=bytes.fromhex(payload['payload']['signature'][2:])
    return '0x'+(keccak(text='transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)')[:4]+
        encode(['address','address','uint256','uint256','uint256','bytes32','uint8','bytes32','bytes32'],
               [auth['from'],auth['to'],int(auth['value']),int(auth['validAfter']),int(auth['validBefore']),bytes.fromhex(auth['nonce'][2:]),sig[64],sig[:32],sig[32:64]])).hex()

def verify(payload,required):
    validate_requirement(required)
    if required['network']!='eip155:31337' or required['asset'].lower()!=state['token'].lower() or required['extra']!={'name':'A2N Test Token','version':'1'}:
        raise ValueError('ONLY_LOCAL_TEST_TOKEN_SUPPORTED')
    now=int(RPC.call('eth_getBlockByNumber',['latest',False])['timestamp'],16)
    validate_payload(payload,required,now=now)
    valid=EvmVerifier().verify_signature(payload)
    if valid:RPC.call('eth_call',[{'from':sender,'to':state['token'],'data':call_data(payload)},'latest'])
    return {'isValid':bool(valid),'payer':payload['payload']['authorization']['from']}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,status,row):
        raw=json.dumps(row).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/supported':return self.send(200,{'kinds':[{'x402Version':2,'scheme':'exact','network':'eip155:31337'}],'extensions':[],'signers':{}})
        if self.path=='/status':return self.send(200,{'kind':'LOCAL_ANVIL_TEST_FACILITATOR','token':state['token'],'settled':len(state['receipts'])})
        self.send(404,{'error':'UNKNOWN_TEST_ROUTE'})
    def do_POST(self):
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=65536:raise ValueError('TEST_REQUEST_LIMIT')
            body=json.loads(self.rfile.read(size))
            with lock:
                if self.path=='/rpc-loss':
                    result=RPC.call(body['method'],body.get('params',[]))
                    if body['method']=='eth_sendRawTransaction' and not state.get('lost_response_used'):
                        state['lost_response_used']=True;save()
                        self.connection.shutdown(2);self.connection.close();return
                    return self.send(200,{'jsonrpc':'2.0','id':body['id'],'result':result})
                if self.path=='/mint':
                    data=keccak(text='mint(address,uint256)')[:4]+encode(['address','uint256'],[body['address'],1000000])
                    tx=RPC.call('eth_sendTransaction',[{'from':sender,'to':state['token'],'data':'0x'+data.hex(),'gas':hex(100000)}]);receipt(tx)
                    return self.send(200,{'test_only':True,'transaction':tx})
                payload,required=body['paymentPayload'],body['paymentRequirements']
                key=hashlib.sha256(json.dumps([payload,required],sort_keys=True).encode()).hexdigest()
                if self.path=='/settle' and key in state['receipts']:return self.send(200,state['receipts'][key])
                result=verify(payload,required)
                if self.path=='/verify':return self.send(200,result)
                if self.path!='/settle' or not result['isValid']:raise ValueError('INVALID_TEST_PAYMENT')
                tx=RPC.call('eth_sendTransaction',[{'from':sender,'to':state['token'],'data':call_data(payload),'gas':hex(200000)}]);receipt(tx)
                row={'success':True,'network':'eip155:31337','transaction':tx,'payer':result['payer']}
                state['receipts'][key]=row;save();return self.send(200,row)
        except Exception as exc:self.send(400,{'error':type(exc).__name__})

ThreadingHTTPServer(('0.0.0.0',8546),Handler).serve_forever()
