"""Isolated worker: encrypted job files, only two bounded byte I/O host imports."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import threading

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b"a2n-wasm-worker/1"


def run_module(module_bytes, payload, limits):
    import wasmtime as w
    config = w.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.wasm_threads = False
    config.wasm_memory64 = False
    config.wasm_multi_memory = False
    config.wasm_simd = False
    config.wasm_relaxed_simd = False
    config.max_wasm_stack = 256 * 1024
    engine = w.Engine(config)
    timer = None
    try:
        module = w.Module(engine, module_bytes)
        allowed = {("a2n", "read_input"), ("a2n", "write_output")}
        if any((imp.module, imp.name) not in allowed or not isinstance(imp.type, w.FuncType) for imp in module.imports):
            raise ValueError("UNSUPPORTED_WASM_HOST_IMPORT")
        raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > 1024 * 1024:
            raise ValueError("WASM_INPUT_TOO_LARGE")
        output, position, overflow = bytearray(), [0], [False]
        with w.Store(engine) as store:
            store.set_limits(memory_size=limits["memory_bytes"], table_elements=1024, instances=1, tables=1, memories=1)
            store.set_fuel(limits["fuel"])
            store.set_epoch_deadline(1)
            linker = w.Linker(engine)
            def read_input(caller, pointer, length):
                memory = caller.get("memory")
                if not isinstance(memory, w.Memory) or pointer < 0 or length < 0 or pointer + length > memory.data_len(caller):
                    raise w.Trap("INVALID_INPUT_MEMORY")
                data = raw[position[0]:position[0] + min(length, 1024 * 1024)]
                memory.write(caller, data, pointer)
                position[0] += len(data)
                return len(data)
            def write_output(caller, pointer, length):
                memory = caller.get("memory")
                if not isinstance(memory, w.Memory) or pointer < 0 or length < 0 or pointer + length > memory.data_len(caller):
                    raise w.Trap("INVALID_OUTPUT_MEMORY")
                if len(output) + length > limits["output_bytes"]:
                    overflow[0] = True
                    raise w.Trap("WASM_OUTPUT_LIMIT")
                output.extend(memory.read(caller, pointer, pointer + length))
                return length
            io_type = w.FuncType([w.ValType.i32(), w.ValType.i32()], [w.ValType.i32()])
            linker.define_func("a2n", "read_input", io_type, read_input, access_caller=True)
            linker.define_func("a2n", "write_output", io_type, write_output, access_caller=True)
            timer = threading.Timer(limits["timeout_ms"] / 1000, engine.increment_epoch)
            timer.daemon = True
            timer.start()
            instance = linker.instantiate(store, module)
            run = instance.exports(store).get("run")
            if not isinstance(run, w.Func) or list(run.type(store).params) or list(run.type(store).results):
                raise ValueError("INVALID_WASM_ENTRY")
            run(store)
            if overflow[0]:
                raise ValueError("WASM_OUTPUT_LIMIT")
        return json.loads(output.decode("utf-8"))
    finally:
        if timer:
            timer.cancel()
            timer.join(timeout=.1)
        engine.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    path = Path(args.job)
    secret = os.environ.pop("_A2N_WASM_JOB_KEY")
    cipher = AESGCM(bytes.fromhex(secret))
    blob = path.read_bytes()
    job = json.loads(cipher.decrypt(blob[:12], blob[12:], AAD))
    try:
        value = run_module(base64.b64decode(job["module_base64"], validate=True), job["payload"], job["limits"])
        result = {"ok": True, "result": value}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:1000]}
    nonce = os.urandom(12)
    path.with_suffix(".result").write_bytes(nonce + cipher.encrypt(nonce,
        json.dumps(result, ensure_ascii=False, allow_nan=False).encode(), AAD))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
