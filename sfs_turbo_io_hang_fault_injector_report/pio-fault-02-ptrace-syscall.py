#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-02: ptrace 系统调用拦截注入
======================================
使用 ptrace 附加到正在运行的进程，拦截 I/O 相关系统调用并修改返回值为错误。

注入原理:
    利用 Linux ptrace() 系统调用的 PTRACE_SYSCALL 模式，
    在目标进程每次系统调用进入/退出时暂停，
    修改寄存器中的返回值为错误码 (-ERROR)。

适用场景:
    - 对已运行的进程注入 I/O 故障（无需重启进程）
    - 拦截直接使用 syscall 指令的程序
    - 精确控制哪些系统调用被拦截

优点:
    ✓ 可附加到正在运行的进程，无需重启
    ✓ 在内核边界拦截，可捕获直接 syscall 调用
    ✓ 不依赖 LD_PRELOAD，对静态编译程序也有效

限制:
    ✗ 性能开销极大（每个 syscall 都会暂停两次）
    ✗ 目标进程运行极慢（约 10-100x 降速）
    ✗ 不适合高 I/O 频率的进程
    ✗ 同一时间一个进程只能被一个 tracer 附加

现象:
    - 目标进程的 read/write/open 等系统调用返回 -EIO
    - 进程看到 "Input/output error" 等错误
    - 进程运行明显变慢（ptrace 开销）

用法:
    sudo python3 pio-fault-02-ptrace-syscall.py --pid 12345
    sudo python3 pio-fault-02-ptrace-syscall.py --pid 12345 --error-type eacces
    sudo python3 pio-fault-02-ptrace-syscall.py --cleanup --pid 12345
"""

import argparse
import ctypes
import ctypes.util
import os
import signal
import struct
import sys
import time

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, run_cmd
)

FAULT_ID = "pio-fault-02"
FAULT_NAME = "ptrace 系统调用拦截 I/O 故障注入"

# Linux x86_64 系统调用号
SYSCALL_NAMES = {
    0: "read", 1: "write", 2: "open", 3: "close",
    4: "stat", 5: "fstat", 6: "lstat", 7: "poll",
    8: "lseek", 9: "mmap", 10: "mprotect",
    17: "pread64", 18: "pwrite64", 19: "readv", 20: "writev",
    40: "mkdir", 41: "rmdir", 59: "execve",
    72: "fcntl", 74: "fsync", 75: "fdatasync",
    76: "truncate", 77: "ftruncate",
    78: "getdents", 79: "getcwd",
    82: "rename", 83: "mkdir", 84: "rmdir",
    85: "creat", 86: "link", 87: "unlink",
    90: "chmod", 92: "chown",
    257: "openat", 262: "newfstatat",
    322: "statx",
}

# 要拦截的 I/O 系统调用号集合
IO_SYSCALLS = {0, 1, 2, 3, 4, 5, 6, 17, 18, 19, 20,
               40, 72, 74, 75, 76, 77, 78, 79,
               82, 83, 84, 85, 86, 87, 90, 92,
               257, 262, 322}

# errno 值
ERROR_MAP = {
    "eio":    5,
    "eacces": 13,
    "enospc": 28,
    "enoent": 2,
    "emfile": 24,
}

# ptrace 常量
PTRACE_ATTACH = 16
PTRACE_DETACH = 17
PTRACE_SYSCALL = 24
PTRACE_TRACEME = 0
PTRACE_PEEKUSER = 3
PTRACE_POKEUSER = 6
PTRACE_GETREGS = 12
PTRACE_SETREGS = 13

# x86_64 user_regs_struct 偏移
# orig_rax = 系统调用号, rax = 返回值
ORIG_RAX_OFFSET = 120 // 8  # offsetof(struct user, regs.orig_rax) = 120 bytes / 8
RAX_OFFSET = 80 // 8         # offsetof(struct user, regs.rax)

# struct user_regs_struct for x86_64
class UserRegs(ctypes.Structure):
    _fields_ = [
        ("r15", ctypes.c_ulonglong), ("r14", ctypes.c_ulonglong),
        ("r13", ctypes.c_ulonglong), ("r12", ctypes.c_ulonglong),
        ("rbp", ctypes.c_ulonglong), ("rbx", ctypes.c_ulonglong),
        ("r11", ctypes.c_ulonglong), ("r10", ctypes.c_ulonglong),
        ("r9", ctypes.c_ulonglong),  ("r8", ctypes.c_ulonglong),
        ("rax", ctypes.c_ulonglong), ("rcx", ctypes.c_ulonglong),
        ("rdx", ctypes.c_ulonglong), ("rsi", ctypes.c_ulonglong),
        ("rdi", ctypes.c_ulonglong), ("orig_rax", ctypes.c_ulonglong),
        ("rip", ctypes.c_ulonglong), ("cs", ctypes.c_ulonglong),
        ("eflags", ctypes.c_ulonglong), ("rsp", ctypes.c_ulonglong),
        ("ss", ctypes.c_ulonglong), ("fs_base", ctypes.c_ulonglong),
        ("gs_base", ctypes.c_ulonglong), ("ds", ctypes.c_ulonglong),
        ("es", ctypes.c_ulonglong), ("fs", ctypes.c_ulonglong),
        ("gs", ctypes.c_ulonglong),
    ]


# 加载 libc
libc_name = ctypes.util.find_library("c")
if not libc_name:
    print("[ERROR] 无法找到 libc")
    sys.exit(1)
libc = ctypes.CDLL(libc_name, use_errno=True)

# 运行标志
_running = True

def handle_signal(signum, frame):
    global _running
    print(f"\n[信号] 收到信号 {signum}，准备退出并 detach...")
    _running = False

def ptrace_attach(pid):
    ret = libc.ptrace(PTRACE_ATTACH, pid, 0, 0)
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    # 等待进程停止
    os.waitpid(pid, 0)

def ptrace_detach(pid):
    # 先发送 SIGSTOP 让进程停止（如果正在运行）
    try:
        libc.ptrace(PTRACE_DETACH, pid, 0, 0)
    except Exception:
        pass

def ptrace_getregs(pid):
    regs = UserRegs()
    ret = libc.ptrace(PTRACE_GETREGS, pid, 0, ctypes.byref(regs))
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    return regs

def ptrace_setregs(pid, regs):
    ret = libc.ptrace(PTRACE_SETREGS, pid, 0, ctypes.byref(regs))
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))

def ptrace_syscall(pid):
    ret = libc.ptrace(PTRACE_SYSCALL, pid, 0, 0)
    if ret != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))

def inject(pid, error_type, fail_rate):
    err_num = ERROR_MAP.get(error_type)
    if err_num is None:
        print(f"[ERROR] 不支持的错误类型: {error_type}")
        return

    err_name = error_type.upper()

    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME}")
    print(f"  目标 PID:    {pid}")
    print(f"  错误类型:    {err_name} ({err_num})")
    print(f"  失败率:      {fail_rate}%")
    print("=" * 60)
    print()

    # 确认目标进程存在
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
    print()

    confirm_inject(FAULT_NAME, "HIGH", f"PID {pid}", 0)

    # 附加到目标进程
    print(f"[注入] ptrace 附加到进程 {pid}...")
    try:
        ptrace_attach(pid)
    except OSError as e:
        print(f"[ERROR] ptrace attach 失败: {e}")
        print("[提示] 可能原因: 进程不存在 / 权限不足 / 已被其他 tracer 附加")
        return

    print(f"  [OK] 已附加到进程 {pid}")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "error_type": error_type,
        "fail_rate": fail_rate,
    })

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    intercepted = 0
    injected = 0
    enter_syscall = True  # 交替追踪 syscall 进入/退出

    print(f"\n[效果] 正在拦截进程 {pid} 的 I/O 系统调用...")
    print(f"  按 Ctrl+C 停止注入并 detach 进程\n")

    while _running:
        try:
            ptrace_syscall(pid)

            # 等待进程停止
            try:
                wpid, status = os.waitpid(pid, os.WNOHANG | __import__('os').WUNTRACED)
                if wpid == 0:
                    continue
                if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                    print(f"[信息] 进程 {pid} 已退出")
                    break
            except ChildProcessError:
                print(f"[信息] 进程 {pid} 已退出")
                break

            if not _running:
                break

            if enter_syscall:
                # syscall 进入 — 记录 syscall 号
                enter_syscall = False
                continue

            enter_syscall = True

            # syscall 退出 — 检查并修改返回值
            try:
                regs = ptrace_getregs(pid)
            except OSError:
                break

            syscall_nr = regs.orig_rax

            if syscall_nr in IO_SYSCALLS:
                intercepted += 1

                # 按失败率决定是否注入
                import random
                if fail_rate >= 100 or random.randint(1, 100) <= fail_rate:
                    # 修改 rax 为 -errno
                    regs.rax = ctypes.c_ulonglong(-err_num & 0xFFFFFFFFFFFFFFFF).value
                    try:
                        ptrace_setregs(pid, regs)
                    except OSError:
                        break
                    injected += 1

                    if injected <= 20 or injected % 100 == 0:
                        sc_name = SYSCALL_NAMES.get(syscall_nr, f"syscall_{syscall_nr}")
                        print(f"  [{injected}] 拦截 {sc_name}() → -{err_name}")

        except Exception as e:
            if not _running:
                break
            print(f"  [警告] {e}")
            time.sleep(0.1)

    # detach
    try:
        ptrace_detach(pid)
    except Exception:
        pass

    print(f"\n[统计] 拦截 I/O syscall: {intercepted} 次, 注入错误: {injected} 次")
    cleanup_state(FAULT_ID)
    print(f"[OK] 已 detach 进程 {pid}，注入停止")


def do_cleanup():
    state = load_state(FAULT_ID)
    pid = state.get("target_pid")
    if pid:
        print(f"[清理] detach 进程 {pid}...")
        try:
            ptrace_detach(pid)
        except Exception as e:
            print(f"  [警告] detach 失败: {e}")
            print(f"  [手动] kill -CONT {pid}")
    cleanup_state(FAULT_ID)
    print("[OK] 清理完成")


def main():
    require_root()
    parser = argparse.ArgumentParser(description=FAULT_NAME)
    parser.add_argument("--pid", type=int, required=not '--cleanup' in sys.argv,
                        help="目标进程 PID")
    parser.add_argument("--error-type", default="eio",
                        choices=list(ERROR_MAP.keys()),
                        help="注入的错误类型 (默认: eio)")
    parser.add_argument("--fail-rate", type=int, default=100,
                        help="失败率百分比 1-100 (默认: 100)")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复")
    args = parser.parse_args()

    if args.cleanup:
        do_cleanup()
    else:
        inject(args.pid, args.error_type, args.fail_rate)


if __name__ == "__main__":
    main()
