#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-06: Inode 耗尽 — 大量小文件导致无法创建新文件
=====================================================
创建大量 0 字节文件耗尽 inode。

现象:
    - df -h 显示有空间，但无法创建文件
    - df -i 显示 IUse% 100%
    - touch 返回 "No space left on device"

用法:
    sudo python3 fault-06-inode-exhaustion.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-06-inode-exhaustion.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import time

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-06"
FAULT_NAME = "Inode 耗尽 (大量小文件)"

INODE_DIR = ".fault_injector_inodes"
BATCH_SIZE = 10000


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    inode_dir = os.path.join(mount_point, INODE_DIR)
    os.makedirs(inode_dir, exist_ok=True)

    rc, out, _ = run_cmd(f"df -i {mount_point} | tail -1")
    print(f"[当前] {out}")
    print(f"\n[注入] 在 {inode_dir} 中批量创建空文件...")

    count = 0
    try:
        while True:
            cmd = (
                f"for i in $(seq {count} $(( {count} + {BATCH_SIZE} - 1 ))); "
                f"do touch {inode_dir}/f_$i; done"
            )
            run_cmd(cmd, timeout=120)
            count += BATCH_SIZE

            rc, out, _ = run_cmd(f"df -i {mount_point} --output=iuse | tail -1")
            if rc == 0:
                pct = out.strip().replace("%", "")
                print(f"  [进度] 已创建 {count} 文件, Inode 使用: {pct}%")
                if pct and int(pct) >= 99:
                    print(f"\n  [OK] Inode 使用率 {pct}%")
                    break
    except KeyboardInterrupt:
        print("\n  [中断] 用户中断")

    save_state(FAULT_ID, {"inode_dir": inode_dir, "count": count})

    rc, out, _ = run_cmd(f"df -i {mount_point} | tail -1")
    print(f"\n[当前] {out}")
    print(f"[效果] 创建新文件将失败 (但 df -h 显示有空间)")
    print(f"  验证: touch {mount_point}/test_inode  (应失败)")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    inode_dir = state.get("inode_dir", os.path.join(mount_point, INODE_DIR))

    if os.path.exists(inode_dir):
        print(f"[清理] 删除 {inode_dir} ...")
        run_cmd(f"rm -rf {inode_dir}")
        print("[OK] Inode 已释放")

    cleanup_state(FAULT_ID)
    rc, out, _ = run_cmd(f"df -i {mount_point} | tail -1")
    print(f"[当前] {out}")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
