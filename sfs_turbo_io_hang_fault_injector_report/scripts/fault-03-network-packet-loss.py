#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-03: 网络丢包注入 — NFS 重传风暴
========================================
模拟到 SFS Turbo 的网络质量极差，大量丢包。

原理:
    tc netem 注入 50% 丢包率。

现象:
    - nfsstat -c 显示 retrans 急剧上升
    - IO 频繁超时和错误
    - 文件操作返回 Input/output error

用法:
    sudo python3 fault-03-network-packet-loss.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-03-network-packet-loss.py --mount-point /mnt/sfs_turbo --duration 60
    sudo python3 fault-03-network-packet-loss.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, get_interface, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-03"
FAULT_NAME = "网络丢包注入 (NFS 重传风暴)"

LOSS_RATE = 50     # 丢包率 %
CORRELATION = 25   # 相关性 %


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
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    print(f"[注入] 在 {iface} 上注入 {LOSS_RATE}% 丢包 (到 {sfs_ip})...")

    cmds = [
        f"tc qdisc add dev {iface} root handle 1: prio",
        f"tc filter add dev {iface} parent 1:0 protocol ip prio 1 u32 match ip dst {sfs_ip} flowid 1:1",
        f"tc qdisc add dev {iface} parent 1:1 handle 10: netem loss {LOSS_RATE}% {CORRELATION}%",
    ]
    for cmd in cmds:
        rc, _, err = run_cmd(cmd)
        if rc != 0:
            print(f"[ERROR] {cmd}\n{err}")
            run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")
            return
        print(f"  [OK] {cmd}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "interface": iface})

    print(f"\n[效果] 到 SFS Turbo 的 {LOSS_RATE}% 数据包被丢弃")
    print(f"  验证: nfsstat -c  (观察 retrans)")
    print(f"  验证: time dd if=/dev/zero of={mount_point}/test bs=1M count=10 oflag=direct")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    iface = state.get("interface", "")
    if not iface:
        sfs_ip = get_sfs_ip(mount_point)
        iface = get_interface(sfs_ip) if sfs_ip else ""

    if iface:
        print(f"[清理] 移除 {iface} 上的丢包规则...")
        run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")
    cleanup_state(FAULT_ID)
    print("[OK] 丢包规则已移除")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
