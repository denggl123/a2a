"""Portable desktop launcher using the product's shared Windows memory guard."""
import sys

from a2n_node.process_limits import DEFAULT_LIMIT, ensure_limit

JOB_MEM_LIMIT_BYTES = DEFAULT_LIMIT


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    ensure_limit()
    from serve_desktop_node import main as serve
    return serve(args)


if __name__ == '__main__':
    raise SystemExit(main())
