"""SDK 内嵌本地管理台：跑节点的人在自己电脑上就能看到一切。

为什么放在 SDK 里而不是平台上：你电脑上跑着什么、干得多快、账上进出多少，
这些数据本来就该在你自己手里。这个页面只对本机 (127.0.0.1) 监听，
数据 = 平台 API（账目、状态）+ 本机进程内统计（我侧真实体验）。

零第三方依赖：标准库 http.server。`python -m a2n_sdk.console` 可单独启动。
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>A2N 节点管理台（本机）</title>
<style>
  :root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--ink:#1c1f23;--muted:#6b7280;
        --blue:#185fa5;--blue-bg:#e6f1fb;--green:#0f6e56;--green-bg:#e1f5ee;
        --amber:#854f0b;--amber-bg:#faeeda;--red:#a32d2d;--red-bg:#fcebeb}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
  header{background:#fff;border-bottom:1px solid var(--line);padding:14px 24px;
         display:flex;align-items:center;gap:14px;flex-wrap:wrap;position:sticky;top:0;z-index:10}
  .brand{font-size:16px;font-weight:600}
  .brand span{color:var(--muted);font-weight:400;font-size:12px;margin-left:8px}
  .spacer{flex:1}
  main{padding:20px 24px;max-width:1100px;margin:0 auto}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-bottom:18px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .card h4{margin:0 0 6px;font-size:12px;color:var(--muted);font-weight:500}
  .card .v{font-size:22px;font-weight:600}
  .card .s{font-size:12px;color:var(--muted);margin-top:4px}
  .badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px}
  .b-ok{background:var(--green-bg);color:var(--green)}
  .b-bad{background:var(--red-bg);color:var(--red)}
  .b-warn{background:var(--amber-bg);color:var(--amber)}
  .b-info{background:var(--blue-bg);color:var(--blue)}
  table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);
        border-radius:12px;overflow:hidden;font-size:13px}
  th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line)}
  th{background:#fafbfc;color:var(--muted);font-weight:500;font-size:12px}
  .sec{margin-bottom:22px}.sec h3{margin:0 0 10px;font-size:14px;font-weight:600}
  .mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
  .muted{color:var(--muted)}
  .empty{padding:24px;text-align:center;color:var(--muted);background:#fff;
         border:1px dashed var(--line);border-radius:12px}
  .note{background:var(--blue-bg);border:1px solid #cfe3f5;border-radius:12px;
        padding:12px 16px;font-size:13px;margin-bottom:18px}
</style></head><body>
<header>
  <div class="brand">A2N 节点管理台<span id="nodeid">本机视图 · 数据不出你的电脑</span></div>
  <div class="spacer"></div>
  <span id="hb" class="muted" style="font-size:12px"></span>
</header>
<main><div id="root"><div class="empty">正在加载…</div></div></main>
<script>
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const yuan=p=>((p||0)/100).toFixed(2)+' 元';
async function refresh(){
  let d; try{ d=await (await fetch('/api/state')).json(); }catch(e){ return; }
  const a=d.agent||{}, c=a.connection||{}, l=d.local||{}, p=d.peer_info||{}, tr=d.transport||{}, tn=d.tunnel;
  const connBad = tr.chosen ? 'b-ok' : 'b-bad';
  const connTxt = tr.chosen ? tr.chosen+' · 可派' : '不可达';
  const ladder = (tr.candidates||[]).map(x=>
    `<span class="badge ${x.usable?'b-ok':'b-warn'}" title="${esc(x.detail)}">${esc(x.transport)}</span>`).join(' ');
  document.getElementById('nodeid').textContent = (a.name||a.agent_id||'未注册')+' · 本机视图';
  document.getElementById('hb').textContent = '平台 '+d.base_url+' · 刷新于 '+new Date().toLocaleTimeString();
  const ok=l.tasks_ok||0, bad=l.tasks_failed||0, total=ok+bad;
  document.getElementById('root').innerHTML = `
  <div class="note"><b>连接处境：</b>平台看到你的出口 IP 是 <span class="mono">${esc(p.peer_ip||'-')}</span>，
    你在本机自报的网卡 IP 是 <span class="mono">${esc((c.local_ips||[]).slice(0,3).join(', ')||'-')}</span>。
    ${c.nat==='natted'?'两者不同 → 你在 NAT 后（家用宽带常态）。<b>没关系：</b>A2N 的任务由你主动来取（pull），不需要公网 IP、不需要端口映射、不需要 frp。'
     :c.nat==='public'?'两者一致 → 你有公网 IP。可以选择 direct 模式被平台直连，延迟更低；不选也一样能跑。'
     :'平台还在判定你的网络处境（等第一次心跳回执）。'}
    ${c.downgraded?'<br><span style="color:var(--amber)">⚠ '+esc(c.downgraded)+'</span>':''}</div>
  <div class="grid">
    <div class="card"><h4>节点状态</h4><div class="v"><span class="badge ${a.status==='ACTIVE'?'b-ok':(a.status==='PROBATION'?'b-warn':'b-info')}">${esc(a.status||'-')}</span></div>
      <div class="s">KYA ${esc(a.kya_grade||'-')} · 信誉 ${a.reputation!=null?(a.reputation*100).toFixed(0):'-'}</div></div>
    <div class="card"><h4>连接</h4><div class="v"><span class="badge ${connBad}">${esc(connTxt)}</span></div>
      <div class="s" style="margin-top:6px">阶梯 ${ladder}</div>
      ${tn?`<div class="s">隧道 ${tn.connected?'在线':'断开'}${tn.last_error?' · '+esc(tn.last_error):''}</div>`:''}</div>
    <div class="card"><h4>本地观测</h4><div class="v">${total?Math.round(ok/total*100):'-'}<span style="font-size:13px">% 成功</span></div>
      <div class="s">${ok} 成 / ${bad} 败 · 平均 ${l.avg_ms||0}ms</div></div>
    <div class="card"><h4>累计收益</h4><div class="v">${yuan(a.earned)}</div>
      <div class="s">可提现 ${yuan(d.balance_points)}（1 积分 = 0.01 元）</div></div>
  </div>
  <div class="sec"><h3>最近任务（本机视角）</h3>
  ${(l.recent&&l.recent.length)?`<table><thead><tr><th>时间</th><th>任务</th><th>能力</th><th>耗时</th><th>验收</th><th>实付</th></tr></thead><tbody>
    ${l.recent.map(t=>`<tr><td class="mono">${esc(t.at)}</td><td class="mono">${esc(t.task_id)}</td>
      <td class="mono">${esc(t.skill)}</td><td>${t.ms}ms</td>
      <td><span class="badge ${t.passed?'b-ok':'b-bad'}">${t.passed?'通过':'打回'}</span></td>
      <td>${yuan(t.amount)}</td></tr>`).join('')}</tbody></table>`
   :'<div class="empty">还没有任务进来。挂着就行 —— 长轮询会在有单时第一时间拉到。</div>'}</div>
  <div class="sec"><h3>运行信息</h3><table><tbody>
    <tr><th style="width:160px">平台</th><td class="mono">${esc(d.base_url)}</td></tr>
    <tr><th>心跳</th><td>${p.last_seen_at?esc(p.last_seen_at):'-'} · 出口 IP <span class="mono">${esc(p.peer_ip||'-')}</span> · NAT ${esc(c.nat||'unknown')}</td></tr>
    <tr><th>最近错误</th><td class="mono">${esc(l.last_error||'无')}</td></tr>
    <tr><th>启动时间</th><td class="mono">${esc(l.started_at||'')} UTC</td></tr>
  </tbody></table></div>`;
}
refresh(); setInterval(refresh, 5000);
</script></body></html>"""


class LocalConsole:
    """本机管理台。监听 127.0.0.1，外部机器访问不到。"""

    def __init__(self, node: Any, port: int = 8770) -> None:
        self.node = node
        self.port = port
        self._srv: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        node = self.node
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:  # 静音访问日志
                pass

            def _send(self, code: int, body: bytes, ctype: str) -> None:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.startswith("/api/state"):
                    try:
                        data = node.snapshot()
                    except Exception as e:  # noqa: BLE001
                        data = {"error": str(e)}
                    self._send(200, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")
                elif self.path in ("/", "/index.html", "/console"):
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                else:
                    self._send(404, b"not found", "text/plain")

        self._srv = ThreadingHTTPServer(("127.0.0.1", outer.port), Handler)
        self.port = self._srv.server_address[1]
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()


def main() -> None:
    """python -m a2n_sdk.console —— 起一个演示节点 + 本地管理台。"""
    from .runner import Node

    card = {
        "name": "本机演示节点",
        "url": None,
        "skills": [{"id": "echo", "version": "1.0.0"}],
        "x-a2n": {
            "compute": {"region": "local"},
            "sla": {"max_latency_ms": 5000},
            "price_hint": {"echo": {"amount": 1}},
        },
    }
    node = Node(card, {"echo": lambda p: {"echo": p, "host": "本机"}},
                principal="acct:local-demo")
    node.serve(console=True)


if __name__ == "__main__":
    main()
