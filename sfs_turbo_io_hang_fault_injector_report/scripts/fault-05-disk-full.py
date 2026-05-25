#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-05: 文件系统容量耗尽 — 磁盘满导致 IO 阻塞
==================================================
向挂载点写入大文件填满磁盘空间。

现象:
    - 写入返回 "No space left on device"
    - df -h 显示 Use% 100%
    - 依赖写入的业务全面中断

用法:
    sudo python3 fault-05-disk-full.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-05-disk-full.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import time
import subprocess

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-05"
FAULT_NAME = "文件系统容量耗尽 (磁盘满)"

FILLER_FILE = ".fault_injector_disk_filler"


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    rc, out, _ = run_cmd(f"df -h {mount_point} | tail -1")
    print(f"[当前] {out}")

    filler = os.path.join(mount_point, FILLER_FILE)
    print(f"\n[注入] 持续写入 {filler} 直到磁盘满...")

    proc = subprocess.Popen(
        f"dd if=/dev/zero of={filler} bs=1M status=progress",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    # 监控直到 >= 99%
    try:
        while proc.poll() is None:
            rc, out, _ = run_cmd(f"df {mount_point} --output=pcent | tail -1")
            if rc == 0:
                pct = out.strip().replace("%", "")
                if pct and int(pct) >= 99:
                    print(f"\n  [OK] 磁盘使用率 {pct}%")
                    proc.terminate()
                    break
            time.sleep(3)
    except KeyboardInterrupt:
        proc.terminate()

    save_state(FAULT_ID, {"filler_file": filler, "mount_point": mount_point})

    rc, out, _ = run_cmd(f"df -h {mount_point} | tail -1")
    print(f"\n[当前] {out}")
    print(f"[效果] 写入将返回 'No space left on device'")
    print(f"  验证: touch {mount_point}/test_file  (应失败)")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    filler = state.get("filler_file", os.path.join(mount_point, FILLER_FILE))

    if os.path.exists(filler):
        print(f"[清理] 删除填充文件: {filler} ...")
        run_cmd(f"rm -f {filler}")
        print("[OK] 空间已释放")
    else:
        # 通用清理
        for f in os.listdir(mount_point):
            if FILLER_FILE in f:
                run_cmd(f"rm -f {os.path.join(mount_point, f)}")

    cleanup_state(FAULT_ID)
    rc, out, _ = run_cmd(f"df -h {mount_point} | tail -1")
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
