#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-10: 大量并发连接压力 — 模拟连接数超限
=============================================
发起大量并发 TCP 连接到 NFS 端口 (2049)。

现象:
    - SFS Turbo 资源被大量连接占用
    - 新的 NFS 操作被排队或超时

用法:
    sudo python3 fault-10-concurrent-connections.py --mount-point /mnt/sfs_turbo --count 200
    sudo python3 fault-10-concurrent-connections.py --cleanup --mount-point /mnt/sfs_turbo
"""

import socket
import time

from common import (
    require_root, get_sfs_ip, save_state, load_state, cleanup_state,
    banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-10"
FAULT_NAME = "大量并发连接压力"

DEFAULT_COUNT = 200
NFS_PORT = 2049


def inject(mount_point: str, duration: int):
    import argparse
    # 额外参数
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help=f"并发连接数 (默认: {DEFAULT_COUNT})")
    parser.add_argument("--mount-point", default=mount_point)
    parser.add_argument("--duration", type=int, default=duration)
    parser.add_argument("--cleanup", action="store_true")
    extra_args = parser.parse_args()
    count = extra_args.count

    sfs_ip = get_sfs_ip(mount_point)
    if not sfs_ip:
        print("[ERROR] 无法获取 SFS Turbo IP")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, duration=duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    print(f"[注入] 发起 {count} 个 TCP 连接到 {sfs_ip}:{NFS_PORT}...")

    socks = []
    connected = 0
    failed = 0
    for i in range(count):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((sfs_ip, NFS_PORT))
            socks.append(s)
            connected += 1
        except Exception as e:
            failed += 1
            if i % 50 == 0:
                print(f"  [WARN] 连接 {i} 失败: {e}")

    print(f"  [OK] 成功: {connected}, 失败: {failed}")

    save_state(FAULT_ID, {"sfs_ip": sfs_ip, "connected": connected})

    print(f"\n[效果] {connected} 个连接占用 NFS 服务端资源")
    print(f"  验证: ss -s  (查看总连接数)")
    print(f"  验证: time ls {mount_point}  (可能变慢)")

    # 保持连接
    try:
        while True:
            time.sleep(10)
            for s in socks[:]:
                try:
                    s.send(b"")
                except:
                    socks.remove(s)
    except KeyboardInterrupt:
        print("\n[中断] 正在清理...")
        for s in socks:
            try:
                s.close()
            except:
                pass
    cleanup_state(FAULT_ID)
    print("[OK] 连接已断开")


def do_cleanup(mount_point: str):
    cleanup_state(FAULT_ID)
    print("[OK] 状态已清理（如注入进程在后台运行，需手动终止）")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
