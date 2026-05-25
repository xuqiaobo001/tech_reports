#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-07: IOPS 压力打满 — IO 排队极慢
=======================================
发起大量随机小 IO 打满 IOPS 限制，模拟 IO 限流。

现象:
    - 其他业务的 IO 延迟急剧升高
    - dd 写入速度极慢
    - 类似 IO 卡死的表现

用法:
    sudo python3 fault-07-iops-pressure.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-07-iops-pressure.py --mount-point /mnt/sfs_turbo --duration 120
    sudo python3 fault-07-iops-pressure.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import subprocess

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-07"
FAULT_NAME = "IOPS 压力打满 (IO 排队极慢)"

STRESS_DIR = ".fault_injector_iops"
NUM_THREADS = 8


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    stress_dir = os.path.join(mount_point, STRESS_DIR)
    os.makedirs(stress_dir, exist_ok=True)

    # 检查是否有 fio
    has_fio = run_cmd("which fio")[0] == 0

    pids = []
    for i in range(NUM_THREADS):
        test_file = os.path.join(stress_dir, f"stress_{i}.dat")

        if has_fio:
            cmd = (
                f"fio --name=sfs_stress_{i} --filename={test_file} "
                f"--size=100M --bs=4k --rw=randrw --iodepth=32 "
                f"--numjobs=1 --time_based --runtime=86400 "
                f"--group_reporting 2>/dev/null"
            )
        else:
            cmd = (
                f"while true; do "
                f"dd if=/dev/urandom of={test_file} bs=4k count=256 oflag=direct 2>/dev/null; "
                f"dd if={test_file} of=/dev/null bs=4k iflag=direct 2>/dev/null; "
                f"done"
            )

        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pids.append(p.pid)
        tool = "fio" if has_fio else "dd"
        print(f"  [OK] 线程 {i} PID={p.pid} ({tool})")

    save_state(FAULT_ID, {"stress_dir": stress_dir, "pids": pids})

    print(f"\n[效果] {NUM_THREADS} 个线程持续 4K 随机 IO")
    print(f"  验证: time dd if=/dev/zero of={mount_point}/test bs=4k count=1000 oflag=direct")

    auto_wait_cleanup(duration, lambda: do_cleanup(mount_point), FAULT_ID)


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    pids = state.get("pids", [])
    stress_dir = state.get("stress_dir", os.path.join(mount_point, STRESS_DIR))

    if pids:
        print(f"[清理] 终止 {len(pids)} 个 IO 进程...")
        for pid in pids:
            run_cmd(f"kill -9 {pid} 2>/dev/null")
            run_cmd(f"kill -9 {pid} 2>/dev/null")

    if os.path.exists(stress_dir):
        run_cmd(f"rm -rf {stress_dir}")

    cleanup_state(FAULT_ID)
    print("[OK] IO 压力已停止")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
