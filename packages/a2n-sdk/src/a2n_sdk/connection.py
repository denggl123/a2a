"""本机连通性自检：回答"我这台电脑处于什么网络处境"。

关键认知：**节点不需要知道自己的公网 IP，平台替它看。**
节点只自报本机网卡的 IP 列表；平台比对心跳的来源 IP：
  - 来源 IP 在本机 IP 列表里 → 你有公网 IP（direct 可行）
  - 不在 → 你在 NAT 后（家用宽带常态），用 pull/tunnel，零配置照跑

另附 STUN 反射探测：向公共 STUN 服务器发 Binding Request，拿到
运营商视角的"反射地址"（srflx candidate）。它只用于通道协商的候选
枚举——打洞是候选，不是依赖。
"""
from __future__ import annotations

import os
import socket
import struct

STUN_SERVERS = [("stun.miwifi.com", 3478), ("stun.qq.com", 3478),
                ("stun.qq.com", 8000), ("stun.l.google.com", 19302)]


def local_ips() -> list[str]:
    """本机所有 IPv4/IPv6 地址（尽力而为，失败不影响注册）。"""
    ips: set[str] = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        # 经典技巧：连一个外部地址（不真正发包）让系统选出默认路由的网卡 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        try:
            s.connect(("223.5.5.5", 53))  # 阿里 DNS，仅用于路由选择
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    return sorted(ips)


def stun_reflexive(servers: list | None = None, timeout: float = 2.0) -> dict | None:
    """STUN 反射地址探测（RFC 5389 最小实现，标准库 UDP）。

    返回 {"ip":..., "port":..., "server":...}；全失败返回 None。
    这不是打洞——只是拿到"运营商眼里我是谁"，供平台枚举打洞候选。
    """
    for server in (servers or STUN_SERVERS):
        txid = os.urandom(12)
        req = struct.pack(">HHI", 0x0001, 0, 0x2112A442) + txid
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(timeout)
            s.sendto(req, server)
            data, _ = s.recvfrom(2048)
            if len(data) < 20:
                continue
            _msg_type, msg_len, cookie = struct.unpack(">HHI", data[:8])
            if data[8:20] != txid:
                continue
            pos = 20
            end = min(20 + msg_len, len(data))
            while pos + 4 <= end:
                attr_type, attr_len = struct.unpack(">HH", data[pos:pos + 4])
                val = data[pos + 4:pos + 4 + attr_len]
                if attr_type == 0x0001 and len(val) >= 8:      # MAPPED-ADDRESS
                    port = struct.unpack(">H", val[2:4])[0]
                    ip = socket.inet_ntoa(val[4:8])
                    return {"ip": ip, "port": port, "server": server[0]}
                if attr_type == 0x0020 and len(val) >= 8:      # XOR-MAPPED-ADDRESS
                    xport = struct.unpack(">H", val[2:4])[0] ^ (cookie >> 16)
                    xip = bytes(a ^ b for a, b in
                                zip(val[4:8], struct.pack(">I", cookie)))
                    return {"ip": socket.inet_ntoa(xip), "port": xport, "server": server[0]}
                pos += 4 + attr_len + ((4 - attr_len % 4) % 4)
        except OSError:
            continue
        finally:
            if s:
                s.close()
    return None


def connection_report(advertised_url: str | None = None,
                      with_stun: bool = False) -> dict:
    """注册/心跳用的连接自报。mode 留空 → 平台按处境给默认（家宽=pull）。"""
    report = {
        "mode": "direct" if advertised_url else "pull",
        "url": advertised_url,
        "local_ips": local_ips(),
        "nat": "unknown",
    }
    if with_stun:
        srflx = stun_reflexive()
        if srflx:
            report["candidates"] = [f"{srflx['ip']}:{srflx['port']}"]
            report["reflexive_via"] = srflx["server"]
    return report

