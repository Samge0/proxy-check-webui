"""质量指标：去重数 / 重复率 / /24 集中度 / 跨家交集。

交集口径 = 共同去重IP数 ÷ 较小方去重IP数 × 100%；<15% 判定「真不太相同」。
"""
from __future__ import annotations

from collections import Counter


def ip_to_24(ip: str) -> str | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3]) + ".0/24"


def vendor_metrics(ips: list[str]) -> dict:
    """ips = 某厂商全部成功采样的出口 IP（按时间顺序，允许重复）。"""
    total = len(ips)
    unique_ips = set(ips)
    net_counter = Counter(net for net in (ip_to_24(ip) for ip in unique_ips) if net)
    top_nets = [
        {"net": net, "ips": count, "share": round(count / total, 4)}
        for net, count in net_counter.most_common(3)
    ]
    dup_rate = round(1 - len(unique_ips) / total, 4) if total else 0.0
    metrics = {
        "probes_ok": total,
        "unique_ips": len(unique_ips),
        "dup_rate": dup_rate,
        "unique_24": len(net_counter),
        "top_24": top_nets,
    }
    return metrics


def pairwise_intersection(ips_a: list[str], ips_b: list[str]) -> dict:
    set_a = set(ips_a)
    set_b = set(ips_b)
    nets_a = {ip_to_24(ip) for ip in set_a} - {None}
    nets_b = {ip_to_24(ip) for ip in set_b} - {None}
    shared = len(set_a & set_b)
    shared_nets = len(nets_a & nets_b)
    denom = min(len(set_a), len(set_b))
    denom_nets = min(len(nets_a), len(nets_b))
    result = {
        "shared": shared,
        "pct": round(shared / denom * 100, 2) if denom else 0.0,
        "shared_24": shared_nets,
        "pct_24": round(shared_nets / denom_nets * 100, 2) if denom_nets else 0.0,
    }
    return result
