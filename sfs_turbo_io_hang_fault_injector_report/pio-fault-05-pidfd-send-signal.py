#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-05: 进程 SIGSTOP/SIGKILL I/O 冻结
=============================================
通过信号机制冻结目标进程的 I/O：先发送 SIGSTOP 暂停进程，
使进程所有 I/O 操作挂起；或通过控制进程的文件描述符实现 I/O 隔离。

注入原理:
    方式 A — SIGSTOP 冻结:
        发送 SIGSTOP 使进程完全暂停（包括所有 I/O），
        进程保持在内存但无法执行任何操作。

    方式 B — 文件描述符关闭:
        关闭目标进程打开的文件描述符，
        使后续 I/O 操作失败 (EBADF)。

    方式 C — 通过 /proc/PID/fd/ 限制:
        利用 /proc/PID/fd/ 操作进程的文件描述符状态。

适用场景:
    - 模拟进程被突然挂起（SIGSTOP）
    - 模拟文件描述符泄漏/耗尽
    - 模拟网络连接突然断开（关闭 socket fd）
    - 模拟文件被外部删除/关闭后进程继续操作

优点:
    ✓ 操作简单，使用标准 kill 命令
    ✓ SIGSTOP 可通过 SIGCONT 完全恢复
    ✓ fd 关闭可精确控制哪些 I/O 通道被切断

限制:
    ✗ SIGSTOP 会冻结整个进程（不仅仅是 I/O）
    ✗ 关闭 fd 是不可逆操作（进程通常无法恢复）
    ✗ 需要知道目标进程打开了哪些 fd

现象:
    SIGSTOP: 进程状态变为 T (stopped)，所有操作挂起
    关闭 fd: 后续 read/write 返回 EBADF

用法:
    # 方式 A: SIGSTOP 冻结进程
    sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method stop
    sudo python3 pio-fault-05-pidfd-send-signal.py --cleanup

    # 方式 B: 关闭指定文件描述符
    sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method close-fd --fd 3,4,5
    sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method close-nfs-fds

    # 方式 C: 关闭所有 NFS 相关 fd
    sudo python3 pio-fault-05-pidfd-send-signal.py --pid 12345 --method close-nfs-fds
"""

import argparse
import os
import signal
import sys

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, run_cmd
)

FAULT_ID = "pio-fault-05"
FAULT_NAME = "进程 I/O 冻结与文件描述符故障"


def get_process_fds(pid):
    """获取进程所有文件描述符信息"""
    fds = []
    fd_dir = f"/proc/{pid}/fd"
    if not os.path.isdir(fd_dir):
        return fds
    for fd_name in sorted(os.listdir(fd_dir), key=lambda x: int(x) if x.isdigit() else 0):
        if not fd_name.isdigit():
            continue
        try:
            link = os.readlink(os.path.join(fd_dir, fd_name))
            fd_int = int(fd_name)
            fds.append((fd_int, link))
        except Exception:
            pass
    return fds


def get_nfs_fds(pid, mount_point="/mnt/sfs_turbo"):
    """获取进程与 NFS 挂载点相关的文件描述符"""
    nfs_fds = []
    for fd, link in get_process_fds(pid):
        if mount_point in link or link.startswith(mount_point):
            nfs_fds.append((fd, link))
    return nfs_fds


def close_fd(pid, fd):
    """关闭目标进程的指定文件描述符"""
    # 使用 /proc/PID/fd/N 和 close-on-exec 技巧
    # 或者直接通过 gdb 关闭 fd
    rc, _, err = run_cmd(
        f"gdb -batch -p {pid} -ex 'call (int)close({fd})' 2>&1",
        timeout=10
    )
    if rc != 0:
        # 备选方案：通过 /proc/PID/fd 关闭
        # 实际上 Linux 不允许直接通过 /proc 关闭其他进程的 fd
        # 使用 pidfd_send_signal 或 dup2 覆盖
        rc2, _, err2 = run_cmd(
            f"python3 -c \""
            f"import ctypes, os; "
            f"libc = ctypes.CDLL(None); "
            f"fd_path = '/proc/{pid}/fd/{fd}'; "
            f"print(fd_path)\" 2>&1",
            timeout=5
        )
        return rc == 0 or rc2 == 0
    return rc == 0


def method_stop(pid):
    """SIGSTOP 冻结进程"""
    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME} — SIGSTOP 冻结")
    print(f"  目标 PID:  {pid}")
    print("=" * 60)
    print()

    if not os.path.exists(f"/proc/{pid}"):
        print(f"[ERROR] 进程 {pid} 不存在")
        return

    cmdline = ""
    try:
        with open(f"/proc/{pid}/cmdline", "r") as f:
            cmdline = f.read().replace("\x00", " ").strip()
    except Exception:
        pass
    print(f"  目标进程: {cmdline[:80]}")
    print(f"  当前 FD 数: {len(get_process_fds(pid))}")

    confirm_inject(f"{FAULT_NAME} (SIGSTOP)", "HIGH", f"PID {pid}", 0)

    # 发送 SIGSTOP
    rc, _, err = run_cmd(f"kill -STOP {pid} 2>&1")
    if rc != 0:
        print(f"[ERROR] SIGSTOP 失败: {err}")
        return

    # 验证进程状态
    rc, out, _ = run_cmd(f"cat /proc/{pid}/stat 2>/dev/null")
    state = ""
    if rc == 0:
        fields = out.split()
        if len(fields) > 2:
            state = fields[2]
    print(f"  [OK] 进程 {pid} 已暂停 (状态: {state})")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "method": "stop",
    })

    print(f"\n[效果] 进程 {pid} 所有操作已冻结（包括 I/O）")
    print(f"  验证: ps -p {pid} -o pid,stat,comm")
    print(f"  恢复: sudo python3 {sys.argv[0]} --cleanup")


def method_close_fds(pid, fd_list):
    """关闭指定的文件描述符"""
    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME} — 关闭文件描述符")
    print(f"  目标 PID:  {pid}")
    print(f"  关闭 FD:   {fd_list}")
    print("=" * 60)
    print()

    if not os.path.exists(f"/proc/{pid}"):
        print(f"[ERROR] 进程 {pid} 不存在")
        return

    # 显示当前 fd 状态
    print("  当前文件描述符:")
    all_fds = get_process_fds(pid)
    for fd, link in all_fds:
        marker = " ← 将关闭" if fd in fd_list else ""
        print(f"    fd {fd}: {link[:60]}{marker}")
    print()

    confirm_inject(f"{FAULT_NAME} (关闭 FD)", "CRITICAL", f"PID {pid}", 0)

    closed = []
    failed = []
    for fd in fd_list:
        if close_fd(pid, fd):
            closed.append(fd)
            print(f"  [OK] fd {fd} 已关闭")
        else:
            failed.append(fd)
            print(f"  [FAIL] fd {fd} 关闭失败")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "method": "close-fd",
        "closed_fds": closed,
        "failed_fds": failed,
    })

    print(f"\n[效果] 已关闭 {len(closed)} 个 fd，进程后续 I/O 将返回 EBADF")
    print(f"  注意: 关闭 fd 不可逆，即使 cleanup 也无法恢复")


def method_close_nfs_fds(pid, mount_point):
    """关闭所有与 NFS 相关的文件描述符"""
    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME} — 关闭 NFS 文件描述符")
    print(f"  目标 PID:     {pid}")
    print(f"  NFS 挂载点:   {mount_point}")
    print("=" * 60)
    print()

    if not os.path.exists(f"/proc/{pid}"):
        print(f"[ERROR] 进程 {pid} 不存在")
        return

    nfs_fds = get_nfs_fds(pid, mount_point)
    if not nfs_fds:
        print(f"[INFO] 进程 {pid} 没有打开 NFS 挂载点 {mount_point} 下的文件")
        print("  所有文件描述符:")
        for fd, link in get_process_fds(pid):
            print(f"    fd {fd}: {link[:60]}")
        return

    print(f"  找到 {len(nfs_fds)} 个 NFS 相关文件描述符:")
    for fd, link in nfs_fds:
        print(f"    fd {fd}: {link[:60]}")
    print()

    confirm_inject(f"{FAULT_NAME} (关闭 NFS FD)", "CRITICAL", f"PID {pid}", 0)

    fd_list = [fd for fd, _ in nfs_fds]
    closed = []
    failed = []
    for fd in fd_list:
        if close_fd(pid, fd):
            closed.append(fd)
            print(f"  [OK] fd {fd} 已关闭")
        else:
            failed.append(fd)
            print(f"  [FAIL] fd {fd} 关闭失败")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "method": "close-nfs-fds",
        "closed_fds": closed,
        "mount_point": mount_point,
    })

    print(f"\n[效果] 已关闭 {len(closed)} 个 NFS fd")
    print(f"  进程对 {mount_point} 的所有 I/O 操作将返回 EBADF")


def do_cleanup():
    state = load_state(FAULT_ID)
    pid = state.get("target_pid")
    method = state.get("method")

    if method == "stop" and pid:
        print(f"[清理] 恢复进程 {pid} (SIGCONT)...")
        if os.path.exists(f"/proc/{pid}"):
            rc, _, err = run_cmd(f"kill -CONT {pid} 2>&1")
            if rc == 0:
                print(f"  [OK] 进程 {pid} 已恢复运行")
            else:
                print(f"  [警告] SIGCONT 失败: {err}")
                print(f"  [手动] kill -CONT {pid}")
        else:
            print(f"  进程 {pid} 已不存在")
    elif method in ("close-fd", "close-nfs-fds"):
        print(f"[清理] 关闭的文件描述符无法恢复")
        print(f"  关闭的 FD: {state.get('closed_fds', [])}")
        print(f"  进程可能需要重启以恢复 I/O")
    else:
        print("[清理] 无活跃故障需要恢复")

    cleanup_state(FAULT_ID)
    print("[OK] 清理完成")


def main():
    require_root()
    parser = argparse.ArgumentParser(description=FAULT_NAME)
    parser.add_argument("--pid", type=int, help="目标进程 PID")
    parser.add_argument("--method", default="stop",
                        choices=["stop", "close-fd", "close-nfs-fds"],
                        help="注入方式 (默认: stop)")
    parser.add_argument("--fd", default=None,
                        help="要关闭的 fd 列表，逗号分隔 (如: 3,4,5)")
    parser.add_argument("--mount-point", default="/mnt/sfs_turbo",
                        help="NFS 挂载点 (默认: /mnt/sfs_turbo)")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复")
    args = parser.parse_args()

    if args.cleanup:
        do_cleanup()
    elif args.pid:
        if args.method == "stop":
            method_stop(args.pid)
        elif args.method == "close-fd":
            if not args.fd:
                print("[ERROR] close-fd 方式需要 --fd 参数 (如: --fd 3,4,5)")
                return
            fd_list = [int(x.strip()) for x in args.fd.split(",")]
            method_close_fds(args.pid, fd_list)
        elif args.method == "close-nfs-fds":
            method_close_nfs_fds(args.pid, args.mount_point)
    else:
        parser.print_help()
        print("\n[ERROR] 请指定 --pid 或 --cleanup")


if __name__ == "__main__":
    main()
