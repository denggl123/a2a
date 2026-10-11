"""Scheduled-task compatibility entry: resolve the installed SDK for this home."""
import sys
from pathlib import Path

from a2n_serve_job_guard import main as serve


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # Older tasks pass only this script and use the node home as their working
    # directory. Preserve that home without requiring an elevated task update.
    if not any(arg == '--home' or arg.startswith('--home=') for arg in args):
        candidate = Path.cwd()
        if (candidate / 'runtime.db').is_file():
            args += ['--home', str(candidate.resolve())]
    if '--watch-existing' not in args:
        args.append('--watch-existing')
    return serve(args)


if __name__ == '__main__':
    raise SystemExit(main())
