#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-12: VPC 路由黑洞 — 到 SFS Turbo 流量被丢弃
===================================================
添加黑洞路由，模拟 VPC 路由表被误修改。

现象:
    - ping SFS Turbo IP 返回 "Destination Host Unreachable"
    - NFS hard mount 进程进入 D-state
    - 所有到 SFS Turbo 的流量被内核丢弃

用法:
    sudo python3 fault-12-vpc-route-blackhole.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-12-vpc-route-blackhole.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-12"
FAULT_NAME = "VPC 路由黑洞 (流量被丢弃)"


def inject(mount_point: str, duration: int):
    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, duration=duration)
    confirm_inject(FAULT_NAME, "CRITICAL", mount_point, duration)

    print(f"[注入] 添加黑洞路由: {sfs_ip}/32 ...")
    cmd = f"ip route add blackhole {sfs_ip}/32"
    rc, _, err = run_cmd(cmd)
    if rc != 0:
        print(f"[ERROR] 添加失败: {err}")
        return
    print(f"  [OK] {cmd}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip})

    print(f"\n[效果] 所有到 {sfs_ip} 的流量被内核丢弃")
    print(f"  验证: ping {sfs_ip}  (应 Destination Host Unreachable)")
    print(f"  [警告] hard mount 进程将进入 D-state，恢复后也可能需要重启 ECS")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    sfs_ip = state.get("sfs_ip", get_sfs_ip(mount_point))

    print(f"[清理] 移除到 {sfs_ip} 的黑洞路由...")
    run_cmd(f"ip route del blackhole {sfs_ip}/32 2>/dev/null")
    cleanup_state(FAULT_ID)
    print("[OK] 路由已恢复")
    print("[警告] 已进入 D-state 的进程可能需要重启 ECS")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
