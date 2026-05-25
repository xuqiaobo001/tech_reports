#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-15: NFS umount 残留状态 — 强制 umount 后无法重新挂载
============================================================
在 IO 活跃期间执行 lazy umount，制造内核 NFS 客户端状态残留。

现象:
    - umount -l 后 /proc/mounts 可能有残留
    - 重新挂载失败 ("device is busy" 或 "mount.nfs: mounting failed")
    - 可能需要重启 ECS 才能恢复

用法:
    sudo python3 fault-15-umount-residual.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-15-umount-residual.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import time
import subprocess

from common import (
    require_root, get_sfs_ip, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-15"
FAULT_NAME = "NFS umount 残留状态 (强制 umount 后无法挂载)"


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    sfs_ip = get_sfs_ip(mount_point)
    banner(FAULT_ID, FAULT_NAME, mount_point, sfs_ip, duration=duration)
    confirm_inject(FAULT_NAME, "CRITICAL", mount_point, duration)

    # 1. 启动持续 IO
    test_file = os.path.join(mount_point, ".fault_injector_umount_io")
    print(f"[注入] 启动后台 IO 写入...")
    io_proc = subprocess.Popen(
        f"dd if=/dev/zero of={test_file} bs=1M count=102400 oflag=direct 2>/dev/null",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print(f"  [OK] IO 进程 PID={io_proc.pid}")
    time.sleep(2)

    # 2. 记录当前挂载信息
    rc, mount_info, _ = run_cmd(f"cat /proc/mounts | grep '{mount_point}'")
    print(f"\n[当前挂载] {mount_info}")

    # 3. 执行 lazy umount
    print(f"\n[注入] 执行 umount -l {mount_point} ...")
    rc, _, err = run_cmd(f"umount -l {mount_point}")
    if rc == 0:
        print(f"  [OK] lazy umount 成功")
    else:
        print(f"  [WARN] lazy umount 失败: {err}，尝试 -f ...")
        run_cmd(f"umount -f {mount_point}")

    # 4. 终止 IO 进程
    run_cmd(f"kill -9 {io_proc.pid} 2>/dev/null")

    # 5. 检查残留
    rc, residual, _ = run_cmd(f"cat /proc/mounts | grep '{mount_point}'")
    if residual:
        print(f"\n  [残留] /proc/mounts 中仍有条目:")
        for line in residual.split("\n"):
            print(f"    {line}")
    else:
        print(f"\n  /proc/mounts 中无残留条目")

    save_state(FAULT_ID, {
        "mount_point": mount_point,
        "mount_info": mount_info,
        "sfs_ip": sfs_ip,
    })

    print(f"\n[效果] umount 后 NFS 客户端可能有残留状态")
    print(f"  验证: mount -t nfs {sfs_ip}:/! {mount_point}  (可能失败)")
    print(f"  [警告] 如无法重新挂载，可能需要重启 ECS")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    mp = state.get("mount_point", mount_point)

    print(f"[清理] 清理 {mp} 残留状态...")
    run_cmd(f"umount -l {mp} 2>/dev/null")
    run_cmd(f"umount -f {mp} 2>/dev/null")

    cleanup_state(FAULT_ID)
    print("[OK] 残留状态已清理")
    print("[提示] 如仍无法挂载，请尝试重启 ECS")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
