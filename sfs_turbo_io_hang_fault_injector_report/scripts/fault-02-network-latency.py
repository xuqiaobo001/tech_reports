#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-02: 网络延迟注入 — NFS IO 极慢
======================================
模拟 ECS 到 SFS Turbo 的网络高延迟。

原理:
    使用 tc (traffic control) 的 netem qdisc 向目标 IP 流量注入延迟。

现象:
    - NFS RPC 请求延迟从毫秒升至秒级
    - dd 写入速度急剧下降
    - 应用层 IO 超时

用法:
    sudo python3 fault-02-network-latency.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-02-network-latency.py --mount-point /mnt/sfs_turbo --duration 120
    sudo python3 fault-02-network-latency.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, get_interface, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-02"
FAULT_NAME = "网络延迟注入 (NFS IO 极慢)"

# 可调参数
LATENCY_MS = 1000   # 基础延迟 ms
JITTER_MS = 500     # 抖动 ms


def inject(mount_point: str, duration: int):
    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP")
        return
    iface = get_interface(sfs_ip)
    if not iface:
        print("[ERROR] 无法确定网卡接口")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, iface, duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    print(f"[注入] 在 {iface} 上添加 {LATENCY_MS}ms ± {JITTER_MS}ms 延迟 (仅到 {sfs_ip})...")

    cmds = [
        f"tc qdisc add dev {iface} root handle 1: prio",
        f"tc filter add dev {iface} parent 1:0 protocol ip prio 1 u32 match ip dst {sfs_ip} flowid 1:1",
        f"tc qdisc add dev {iface} parent 1:1 handle 10: netem delay {LATENCY_MS}ms {JITTER_MS}ms distribution normal",
    ]
    for cmd in cmds:
        rc, _, err = run_cmd(cmd)
        if rc != 0:
            print(f"[ERROR] {cmd}\n{err}")
            run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")
            return
        print(f"  [OK] {cmd}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "interface": iface})

    print(f"\n[效果] 到 SFS Turbo 的每次 NFS RPC 延迟 {LATENCY_MS}ms ± {JITTER_MS}ms")
    print(f"  验证: ping {sfs_ip}")
    print(f"  验证: time dd if=/dev/zero of={mount_point}/test bs=1M count=1 oflag=direct")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    iface = state.get("interface", "")
    if not iface:
        sfs_ip = get_sfs_ip(mount_point)
        iface = get_interface(sfs_ip) if sfs_ip else ""

    if iface:
        print(f"[清理] 移除 {iface} 上的 tc qdisc...")
        run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")
    cleanup_state(FAULT_ID)
    print("[OK] 延迟已恢复")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
