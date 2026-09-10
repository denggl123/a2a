import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="a2n-test-")) / "test.db"
os.environ["A2N_DB"] = str(_TMP)
# 测试永不真实出网：direct/relay 的入站探测一律短路（每次探测要 3s 超时，
# 套件里多几个 direct 节点就会把全量拖慢近半分钟，还引入网络抖动）
os.environ.setdefault("A2N_PROBE_DISABLED", "1")
# 测试环境默认按演示模式放行"无签名充值回调"（真实部署必须带持牌方签名）
os.environ.setdefault("A2N_DEMO_CUSTODIAN", "1")

from a2n_store import init_db  # noqa: E402

init_db()

# 服务端装配（wiring.py）会把事件落库注入 outbox；测试环境同样装配，
# 否则 publish 只走内存订阅，"事件与业务同事务"永远测不到。
from a2n_kernel import events  # noqa: E402
from a2n_store import outbox  # noqa: E402

events.set_sink(outbox.append)
