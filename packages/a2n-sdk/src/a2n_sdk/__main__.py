"""Machine API command, using the same protected local node endpoints as the console."""
import argparse
import json
import os
import sys
from . import cli_i18n
from .client import NodeClient, NodeRequestError
from .serialization import strict_object

def main(argv=None, *, client=None):
    try:
        # 从 product_cli 进来时语言已定；单独运行时由这里定。
        argv = cli_i18n.configure(argv)
    except ValueError as exc:
        print(json.dumps({"error": cli_i18n.error_text(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    t = cli_i18n.t
    parser = argparse.ArgumentParser(prog="a2n-sdk", description=t("cli.api.description"),
                                     epilog=t("cli.common.language_hint"))
    parser.add_argument("--url", default="http://127.0.0.1:8771")
    parser.add_argument("--token", default=os.environ.get("A2N_LOCAL_TOKEN"))
    parser.add_argument("--method", choices=("GET", "POST", "PUT", "DELETE"), default="GET")
    parser.add_argument("--path", default="/v1/runtime")
    parser.add_argument("--body", help=t("cli.api.body.help"))
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
        print(json.dumps({"error": cli_i18n.error_text(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
