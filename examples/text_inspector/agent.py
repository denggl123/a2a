"""A usable HTTP text inspection Agent, implemented entirely in Python.

It counts text structure and token frequencies. Chinese tokens are individual
characters; this is deterministic text processing, not an LLM assessment.
"""
import argparse
from collections import Counter
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading

VERSION = "text-inspector/1"
MAX_INPUT = 65536


def inspect_text(payload):
    text = payload if isinstance(payload, str) else payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > MAX_INPUT:
        raise ValueError("提供非空文本，UTF-8 大小不超过 64 KiB")
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?|[\u4e00-\u9fff]|[0-9]+", text.casefold())
    frequencies = Counter(tokens)
    return {"text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": len(text), "non_whitespace_characters": sum(not c.isspace() for c in text),
        "lines": len(text.splitlines()),
        "paragraphs": len([p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]),
        "tokens": len(tokens), "token_rule": "English words, Chinese characters, numeric groups",
        "keywords": [{"token":token,"count":count} for token,count in
            sorted(frequencies.items(), key=lambda item:(-item[1],item[0]))[:8]],
        "summary": text.strip()[:120]}


class Handler(BaseHTTPRequestHandler):
    counts = {"requests":0,"successful_inspections":0}
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def reply(self, value, status=200):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path != "/counts":
            return self.reply({"error":"not found"},404)
        with self.lock:
            self.reply({"version":VERSION,"counts":dict(self.counts)})

    def do_POST(self):
        if self.path != "/invoke":
            return self.reply({"error":"not found"},404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_INPUT:
                return self.reply({"error":"输入上限 64 KiB"},413)
            value = json.loads(self.rfile.read(size))
            if value.get("skill") != "inspect":
                raise ValueError("只支持 inspect 技能")
            with self.lock:
                self.counts["requests"] += 1
            result = inspect_text(value.get("payload"))
            with self.lock:
                self.counts["successful_inspections"] += 1
            self.reply(result)
        except (ValueError, TypeError, AttributeError):
            self.reply({"error":"请提供有效的文本输入和 inspect 技能"},400)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host,args.port),Handler).serve_forever()


if __name__ == "__main__":
    main()
