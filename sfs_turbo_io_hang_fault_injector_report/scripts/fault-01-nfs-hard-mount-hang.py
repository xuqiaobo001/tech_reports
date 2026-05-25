#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-01: NFS hard mount 进程 D-state 卡死
=============================================
模拟 SFS Turbo 后端完全不可达，导致 hard mount 的进程进入 D-state。

原理:
    使用 iptables DROP 所有到 SFS Turbo IP 的出方向流量。
    NFS hard mount 的进程会无限重试 RPC，进入不可中断睡眠 (D-state)。

现象:
    - ls /mnt/sfs_turbo 卡住
    - 进程进入 D state (ps aux | awk '$8~/D/')
    - 系统 load average 飙升但 CPU 使用率低
    - kill -9 无法终止

用法:
    sudo python3 fault-01-nfs-hard-mount-hang.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-01-nfs-hard-mount-hang.py --mount-point /mnt/sfs_turbo --duration 60
    sudo python3 fault-01-nfs-hard-mount-hang.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, get_interface, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-01"
FAULT_NAME = "NFS hard mount 进程 D-state 卡死"


def inject(mount_point: str, duration: int):
    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP，请确认已挂载")
        return

    iface = get_interface(sfs_ip)
    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, iface, duration)
    confirm_inject(FAULT_NAME, "CRITICAL", mount_point, duration)

    print("[注入] 阻断到 SFS Turbo 的所有流量...")
    cmd = f"iptables -I OUTPUT -d {sfs_ip} -j DROP"
    rc, _, err = run_cmd(cmd)
    if rc != 0:
        print(f"[ERROR] iptables 失败: {err}")
        return
    print(f"  [OK] {cmd}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "mount_point": mount_point})

    print(f"\n[效果] 所有 NFS IO 已卡死")
    print(f"  验证: ls {mount_point}  (会卡住)")
    print(f"  查看: ps aux | awk '$8~/D/'")
    print(f"  负载: cat /proc/loadavg")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    sfs_ip = state.get("sfs_ip", get_sfs_ip(mount_point))

    print(f"[清理] 恢复到 {sfs_ip} 的流量...")
    run_cmd(f"iptables -D OUTPUT -d {sfs_ip} -j DROP 2>/dev/null")
    cleanup_state(FAULT_ID)
    print("[OK] 已恢复。注意: 已进入 D-state 的进程可能需要重启 ECS 才能恢复。")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
