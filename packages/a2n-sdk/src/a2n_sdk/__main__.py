"""Machine API command, using the same protected local node endpoints as the console."""
import argparse
import json
import os
import sys
from .client import NodeClient, NodeRequestError
from .serialization import strict_object

def main(argv=None, *, client=None):
    parser = argparse.ArgumentParser(description="A2N 本机节点 API")
    parser.add_argument("--url", default="http://127.0.0.1:8771")
    parser.add_argument("--token", default=os.environ.get("A2N_LOCAL_TOKEN"))
    parser.add_argument("--method", choices=("GET", "POST", "PUT", "DELETE"), default="GET")
    parser.add_argument("--path", default="/v1/runtime")
    parser.add_argument("--body", help="JSON 对象；- 从 stdin 读取")
    parser.add_argument("--header", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        # Machine JSON is UTF-8, independent of the Windows console code page.
        # Read bytes when available; StringIO clients already provide Unicode.
        raw = (sys.stdin.buffer.read().decode("utf-8") if hasattr(sys.stdin, "buffer") else sys.stdin.read()) if args.body == "-" else args.body
        body = strict_object(raw) if args.body else None
        headers = dict(h.split(":", 1) for h in args.header)
        result = (client or NodeClient(args.url, token=args.token)).request(args.method, args.path, body, headers=headers)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, NodeRequestError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
