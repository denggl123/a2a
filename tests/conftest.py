import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="a2n-test-")) / "test.db"
os.environ["A2N_DB"] = str(_TMP)
# 测试永不真实出网：direct/relay 的入站探测一律短路（每次探测要 3s 超时，
# 套件里多几个 direct 节点就会把全量拖慢近半分钟，还引入网络抖动）
os.environ.setdefault("A2N_PROBE_DISABLED", "1")

from a2n_store import init_db  # noqa: E402

init_db()
