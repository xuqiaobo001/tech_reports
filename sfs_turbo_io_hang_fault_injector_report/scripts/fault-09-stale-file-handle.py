#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-09: Stale NFS file handle 模拟
======================================
打开文件描述符后删除底层数据，模拟后端升级后文件句柄失效。

现象:
    - 访问已删除文件的 fd 出现 "Stale NFS file handle"
    - /proc/<pid>/fd/ 中可见指向 deleted 的 fd

用法:
    sudo python3 fault-09-stale-file-handle.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-09-stale-file-handle.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import sys
import time
import subprocess

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-09"
FAULT_NAME = "Stale NFS file handle 模拟"

TEST_DIR = ".fault_injector_stale"
NUM_FILES = 10


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    test_dir = os.path.join(mount_point, TEST_DIR)
    os.makedirs(test_dir, exist_ok=True)

    # 创建测试文件
    files = []
    for i in range(NUM_FILES):
        fp = os.path.join(test_dir, f"stale_{i}.dat")
        with open(fp, "w") as f:
            f.write(f"test data {i}\n")
        files.append(fp)

    print(f"[注入] 创建了 {NUM_FILES} 个文件，启动进程持有 fd...")

    # 持有 fd 的进程
    holder_code = f'''
import os, time, sys
fds = []
for i in range({NUM_FILES}):
    fd = os.open("{test_dir}/stale_" + str(i) + ".dat", os.O_RDONLY)
    fds.append(fd)
print("HOLDING " + str(len(fds)) + " fds", flush=True)
while True:
    time.sleep(1)
'''
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    line = holder.stdout.readline().decode().strip()
    print(f"  [OK] 进程 {holder.pid}: {line}")

    time.sleep(1)

    # 删除底层文件
    print(f"\n[注入] 删除底层文件（fd 仍被持有）...")
    for fp in files:
        os.remove(fp)
        print(f"  [DEL] {os.path.basename(fp)}")

    save_state(FAULT_ID, {
        "holder_pid": holder.pid,
        "test_dir": test_dir,
    })

    print(f"\n[效果] 进程 {holder.pid} 持有已删除文件的 fd")
    print(f"  验证: ls -la /proc/{holder.pid}/fd/")
    print(f"  尝试读取可能触发 stale 错误")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    pid = state.get("holder_pid")
    test_dir = state.get("test_dir", os.path.join(mount_point, TEST_DIR))

    if pid:
        print(f"[清理] 终止 fd 持有进程 {pid}...")
        run_cmd(f"kill -9 {pid} 2>/dev/null")

    if os.path.exists(test_dir):
        run_cmd(f"rm -rf {test_dir}")

    cleanup_state(FAULT_ID)
    print("[OK] 已清理")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
