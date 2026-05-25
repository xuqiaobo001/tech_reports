#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-04: NFS 端口阻断 — 模拟安全组误配
==========================================
模拟安全组规则错误，NFS 协议所需端口被阻断。

原理:
    iptables DROP 到 SFS Turbo IP 的 NFS 端口。

现象:
    - 新挂载失败 (mount.nfs: Connection timed out)
    - showmount -e 失败
    - 已有连接陆续超时

用法:
    sudo python3 fault-04-nfs-port-block.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-04-nfs-port-block.py --cleanup --mount-point /mnt/sfs_turbo
"""

from common import (
    require_root, get_sfs_ip, save_state, load_state,
    cleanup_state, banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-04"
FAULT_NAME = "NFS 端口阻断 (模拟安全组误配)"

# NFS 协议所需端口
NFS_PORTS = [111, 2049, 2051, 2052, 20048]


def inject(mount_point: str, duration: int):
    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, duration=duration)
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    print(f"[注入] 阻断到 {sfs_ip} 的端口 {NFS_PORTS}...")

    for port in NFS_PORTS:
        cmd = f"iptables -I OUTPUT -d {sfs_ip} -p tcp --dport {port} -j DROP"
        rc, _, err = run_cmd(cmd)
        if rc != 0:
            print(f"  [WARN] 端口 {port} 阻断失败: {err}")
        else:
            print(f"  [OK] DROP TCP → {sfs_ip}:{port}")

    # 同时阻断 UDP 2049 (NFS 可能用 UDP)
    run_cmd(f"iptables -I OUTPUT -d {sfs_ip} -p udp --dport 2049 -j DROP")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "ports": NFS_PORTS, "mount_point": mount_point})

    print(f"\n[效果] NFS 端口已被阻断")
    print(f"  验证: nc -zv -w 3 {sfs_ip} 2049  (应超时)")
    print(f"  验证: showmount -e {sfs_ip}  (应失败)")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    sfs_ip = state.get("sfs_ip", get_sfs_ip(mount_point))
    ports = state.get("ports", NFS_PORTS)

    print(f"[清理] 恢复到 {sfs_ip} 的端口...")
    for port in ports:
        run_cmd(f"iptables -D OUTPUT -d {sfs_ip} -p tcp --dport {port} -j DROP 2>/dev/null")
    run_cmd(f"iptables -D OUTPUT -d {sfs_ip} -p udp --dport 2049 -j DROP 2>/dev/null")
    cleanup_state(FAULT_ID)
    print("[OK] NFS 端口已恢复")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
