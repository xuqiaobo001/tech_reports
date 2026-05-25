#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-08: 并发文件锁竞争 — 模拟 Lock Manager 故障
===================================================
多进程对同一文件加排他锁 (flock)，制造锁等待/死锁。

现象:
    - 5 个进程阻塞在 flock() 系统调用
    - strace -p <pid> 可见 flock 阻塞
    - 依赖文件锁的业务卡死

用法:
    sudo python3 fault-08-file-lock-deadlock.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-08-file-lock-deadlock.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import sys
import subprocess

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-08"
FAULT_NAME = "并发文件锁竞争 (Lock Manager 故障)"

LOCK_FILE = ".fault_injector_lockfile"
NUM_WAITERS = 5


def inject(mount_point: str, duration: int):
    if not os.path.ismount(mount_point):
        print(f"[ERROR] {mount_point} 未挂载")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "MEDIUM", mount_point, duration)

    lock_path = os.path.join(mount_point, LOCK_FILE)

    # 锁持有者进程
    holder_code = f'''
import fcntl, time, sys
f = open("{lock_path}", "w")
fcntl.flock(f.fileno(), fcntl.LOCK_EX)
print("LOCKED", flush=True)
while True:
    time.sleep(1)
'''
    print(f"[注入] 启动锁持有者...")
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    line = holder.stdout.readline().decode().strip()
    if line != "LOCKED":
        print(f"[ERROR] 锁持有者启动失败")
        holder.kill()
        return
    print(f"  [OK] 进程 {holder.pid} 已持有排他锁")

    # 启动等待进程
    waiters = []
    for i in range(NUM_WAITERS):
        waiter_code = f'''
import fcntl, sys
f = open("{lock_path}", "r")
fcntl.flock(f.fileno(), fcntl.LOCK_EX)
print("GOT_LOCK", flush=True)
'''
        w = subprocess.Popen(
            [sys.executable, "-c", waiter_code],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        waiters.append(w)
        print(f"  [OK] 等待者 {i+1} PID={w.pid} 阻塞在 flock()")

    save_state(FAULT_ID, {
        "lock_file": lock_path,
        "holder_pid": holder.pid,
        "waiter_pids": [w.pid for w in waiters],
    })

    print(f"\n[效果] {NUM_WAITERS} 个进程阻塞等待锁释放")
    print(f"  验证: strace -p {waiters[0].pid}  (可见 flock 阻塞)")
    print(f"  [提示] 手动恢复: sudo python3 {sys.argv[0]} --cleanup")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)

    holder_pid = state.get("holder_pid")
    waiter_pids = state.get("waiter_pids", [])
    lock_file = state.get("lock_file", os.path.join(mount_point, LOCK_FILE))

    if holder_pid:
        print(f"[清理] 终止锁持有者 {holder_pid}...")
        run_cmd(f"kill -9 {holder_pid} 2>/dev/null")

    print(f"[清理] 终止 {len(waiter_pids)} 个等待者...")
    for pid in waiter_pids:
        run_cmd(f"kill -9 {pid} 2>/dev/null")

    if os.path.exists(lock_file):
        os.remove(lock_file)

    cleanup_state(FAULT_ID)
    print("[OK] 锁已释放，所有进程已终止")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
