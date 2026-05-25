#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-14: IO 带宽打满 — 吞吐量耗尽
====================================
使用 tc 将到 SFS Turbo 的带宽限制到极低值。

现象:
    - 大文件传输几乎停滞
    - dd 写入极慢
    - 吞吐量远低于正常值

用法:
    sudo python3 fault-14-bandwidth-saturation.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-14-bandwidth-saturation.py --mount-point /mnt/sfs_turbo --duration 120
    sudo python3 fault-14-bandwidth-saturation.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, get_interface, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-14"
FAULT_NAME = "IO 带宽打满 (吞吐量耗尽)"

RATE_KBIT = 1  # 1 kbit/s — 极慢


def inject(mount_point: str, duration: int):
    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP")
        return
    iface = get_interface(sfs_ip)
    if not iface:
        print("[ERROR] 无法确定网卡接口")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, iface, duration=duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    print(f"[注入] 将到 {sfs_ip} 的带宽限制为 {RATE_KBIT} kbit/s ...")

    cmds = [
        f"tc qdisc add dev {iface} root handle 1: htb default 20",
        f"tc class add dev {iface} parent 1: classid 1:1 htb rate {RATE_KBIT}kbit",
        f"tc class add dev {iface} parent 1: classid 1:20 htb rate 10gbit",
        f"tc filter add dev {iface} parent 1:0 protocol ip prio 1 u32 match ip dst {sfs_ip} flowid 1:1",
    ]
    for cmd in cmds:
        rc, _, err = run_cmd(cmd)
        if rc != 0:
            print(f"[ERROR] {cmd}\n{err}")
            run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")
            return
        print(f"  [OK] {cmd}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "interface": iface})

    print(f"\n[效果] 到 SFS Turbo 的带宽被限制为 {RATE_KBIT} kbit/s")
    print(f"  验证: time dd if=/dev/zero of={mount_point}/test bs=1M count=1 oflag=direct")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    iface = state.get("interface", "")
    if not iface:
        sfs_ip = get_sfs_ip(mount_point)
        iface = get_interface(sfs_ip) if sfs_ip else ""

    if iface:
        print(f"[清理] 移除 {iface} 上的 tc 限速...")
        run_cmd(f"tc qdisc del dev {iface} root 2>/dev/null")

    cleanup_state(FAULT_ID)
    print("[OK] 带宽限制已恢复")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
