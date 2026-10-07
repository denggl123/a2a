"""Actual private PyEVM chain exposed through the production JSON-RPC port."""
import json
import threading
from pathlib import Path
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY_NAMES = {"chain_id": "chainId", "block_hash": "blockHash", "block_number": "blockNumber",
    "transaction_hash": "transactionHash", "transaction_index": "transactionIndex", "base_fee_per_gas": "baseFeePerGas",
    "gas_used": "gasUsed", "effective_gas_price": "effectiveGasPrice", "log_index": "logIndex"}


def rpc_shape(value):
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, dict):
        return {KEY_NAMES.get(k, k): rpc_shape(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [rpc_shape(v) for v in value]
    return hex(value) if type(value) is int else value


class TestEVM:
    __test__ = False
    def __init__(self):
        from eth_tester import EthereumTester, PyEVMBackend
        self.chain = EthereumTester(PyEVMBackend())
        self.chain_id = self.chain.backend.chain.chain_id
        self.keys = self.chain.backend.account_keys
        self.broadcasts = 0
        self.drop_response = False
        self._lock = threading.RLock()

    def call(self, method, params):
        from eth_tester.exceptions import TransactionNotFound
        with self._lock:
            if method == "eth_chainId": return hex(self.chain_id)
            if method == "eth_blockNumber": return hex(self.chain.get_block_by_number("latest")["number"])
            if method == "eth_getCode": return self.chain.get_code(params[0])
            if method == "eth_getBalance": return hex(self.chain.get_balance(params[0]))
            if method == "eth_getTransactionCount": return hex(self.chain.get_nonce(params[0], block_number=params[1]))
            if method == "eth_getBlockByNumber":
                number = int(params[0], 16) if str(params[0]).startswith("0x") else params[0]
                return rpc_shape(self.chain.get_block_by_number(number))
            if method == "eth_estimateGas":
                tx = dict(params[0]); tx["value"] = int(tx.get("value", "0x0"), 16)
                return hex(self.chain.estimate_gas(tx))
            if method == "eth_sendRawTransaction":
                self.broadcasts += 1
                result = self.chain.send_raw_transaction(params[0])
                if self.drop_response: raise TimeoutError("lost response after actual chain acceptance")
                return result
            if method in {"eth_getTransactionReceipt", "eth_getTransactionByHash"}:
                try:
                    row = (self.chain.get_transaction_receipt if method.endswith("Receipt") else self.chain.get_transaction_by_hash)(params[0])
                    return rpc_shape(row)
                except TransactionNotFound:
                    return None
            if method == "eth_call":
                tx = dict(params[0]); tx.setdefault("from", self.chain.get_accounts()[0])
                return self.chain.call(tx)
            if method == "eth_getLogs":
                cfg = params[0]
                return rpc_shape(self.chain.get_logs(from_block=int(cfg["fromBlock"],16), to_block=int(cfg["toBlock"],16),
                    address=cfg.get("address"), topics=cfg.get("topics")))
            raise ValueError("unsupported test RPC method " + method)


@contextmanager
def evm_http(evm, host="127.0.0.1"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                result = {"jsonrpc": "2.0", "id": body["id"], "result": evm.call(body["method"], body["params"])}
            except Exception as exc:
                result = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000, "message": str(exc)}}
            raw = json.dumps(result).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    server = ThreadingHTTPServer((host, 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


class TestFacilitator:
    __test__=False
    def __init__(self,evm):
        from eth_abi import encode
        from eth_utils import keccak
        self.evm,self.settles,self.verifies=evm,0,0
        self.lose_response=False
        artifact=json.loads((Path(__file__).parent/'fixtures/PaymentTestToken.json').read_text())
        from hashlib import sha256
        assert sha256((Path(__file__).parent/'fixtures/PaymentTestToken.sol').read_bytes()).hexdigest()==artifact['source_sha256']
        sender=evm.chain.get_accounts()[3]
        tx=evm.chain.send_transaction({'from':sender,'gas':3000000,'data':'0x'+artifact['bytecode']})
        self.token=evm.chain.get_transaction_receipt(tx)['contract_address']
        for address in evm.chain.get_accounts()[:2]:
            data=keccak(text='mint(address,uint256)')[:4]+encode(['address','uint256'],[address,1000000])
            evm.chain.send_transaction({'from':sender,'to':self.token,'gas':100000,'data':'0x'+data.hex()})

    def balance(self,address):
        from eth_abi import encode,decode
        from eth_utils import keccak
        raw=self.evm.chain.call({'from':self.evm.chain.get_accounts()[3],'to':self.token,
            'data':'0x'+(keccak(text='balanceOf(address)')[:4]+encode(['address'],[address])).hex()})
        return decode(['uint256'],bytes.fromhex(raw[2:]))[0]

    def supported(self):
        return {'kinds':[{'x402Version':2,'scheme':'exact','network':'eip155:'+str(self.evm.chain_id)}],'extensions':[],'signers':{}}

    def data(self,payload):
        from eth_abi import encode
        from eth_utils import keccak
        auth=payload['payload']['authorization']
        sig=bytes.fromhex(payload['payload']['signature'][2:])
        return '0x'+(keccak(text='transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32)')[:4]+
            encode(['address','address','uint256','uint256','uint256','bytes32','uint8','bytes32','bytes32'],
            [auth['from'],auth['to'],int(auth['value']),int(auth['validAfter']),int(auth['validBefore']),bytes.fromhex(auth['nonce'][2:]),sig[64],sig[:32],sig[32:64]])).hex()

    def verify(self,payload,required):
        from a2n_node.x402.evm import EvmVerifier
        self.verifies+=1
        valid=EvmVerifier().verify_signature(payload)
        try:self.evm.call('eth_call',[{'to':self.token,'data':self.data(payload)},'latest'])
        except Exception:valid=False
        return {'isValid':valid,'payer':payload['payload']['authorization']['from']}

    def settle(self,payload,required):
        from eth_account import Account
        self.settles+=1
        sender=self.evm.chain.get_accounts()[3]
        base=self.evm.chain.get_block_by_number('latest')['base_fee_per_gas']
        signed=Account.from_key(self.evm.keys[3].to_bytes()).sign_transaction({'type':2,'chainId':self.evm.chain_id,
            'to':self.token,'nonce':self.evm.chain.get_nonce(sender),'gas':200000,'maxFeePerGas':2*base+10**9,
            'maxPriorityFeePerGas':10**9,'data':self.data(payload)})
        tx=self.evm.call('eth_sendRawTransaction',['0x'+signed.raw_transaction.hex()])
        self.evm.chain.mine_blocks(2)
        assert self.evm.chain.get_transaction_receipt(tx)['status']==1
        return {'success':True,'network':'eip155:'+str(self.evm.chain_id),'transaction':tx,'payer':payload['payload']['authorization']['from']}


@contextmanager
def facilitator_http(facilitator):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):self.send(facilitator.supported())
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            result=getattr(facilitator,'verify' if self.path=='/verify' else 'settle')(body['paymentPayload'],body['paymentRequirements'])
            if self.path=='/settle' and facilitator.lose_response:
                self.connection.shutdown(2);self.connection.close();return
            self.send(result)
        def send(self,result):
            raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();server.server_close();thread.join(2)
