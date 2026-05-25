#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-06: eBPF I/O 故障注入
================================
使用 eBPF (extended Berkeley Packet Filter) 在内核层面拦截目标进程的
I/O 系统调用，直接修改返回值为错误码。

注入原理:
    加载 eBPF 程序到内核，挂载到 syscalls:sys_exit_read/write/open 等追踪点，
    当目标 PID 的系统调用返回时，修改返回寄存器为 -errno。

    eBPF 程序运行在内核态，对每个 syscall 的处理开销约 100ns，
    比 ptrace (每次 syscall 约 10-100us) 低 2-3 个数量级。

适用场景:
    - 对运行中进程低开销注入 I/O 故障
    - 高 I/O 频率场景（ptrace 太慢时使用 eBPF）
    - 生产环境旁路注入（无需重启目标进程）
    - 验证应用在内核级别的 I/O 错误处理

优点:
    ✓ 可附加到运行中进程，无需重启
    ✓ 性能开销极低（比 ptrace 低 100-1000 倍）
    ✓ 内核级别拦截，覆盖所有 syscall 路径
    ✓ 可随时加载/卸载，热插拔式故障注入
    ✓ BCC 工具生态丰富

限制:
    ✗ 需要内核 4.7+ 和 BCC 工具链
    ✗ 需要 root 权限和内核头文件
    ✗ 部分容器环境不支持 eBPF
    ✗ CONFIG_BPF 和 CONFIG_BPF_SYSCALL 需要开启

现象:
    - 目标进程 read/write/open 等系统调用返回 -EIO
    - 进程看到 "Input/output error" 等错误
    - 性能影响极小（约 1-5% 开销）

用法:
    sudo python3 pio-fault-06-ebpf-io-error.py --pid 12345
    sudo python3 pio-fault-06-ebpf-io-error.py --pid 12345 --error-type enospc
    sudo python3 pio-fault-06-ebpf-io-error.py --pid 12345 --fail-rate 50
    sudo python3 pio-fault-06-ebpf-io-error.py --cleanup
"""

import argparse
import os
import sys
import signal
import time

from common import (
    require_root, save_state, load_state, cleanup_state,
    confirm_inject, run_cmd
)

FAULT_ID = "pio-fault-06"
FAULT_NAME = "eBPF I/O 故障注入"

ERROR_MAP = {
    "eio":     {"errno": 5,  "desc": "Input/output error"},
    "eacces":  {"errno": 13, "desc": "Permission denied"},
    "enospc":  {"errno": 28, "desc": "No space left on device"},
    "enoent":  {"errno": 2,  "desc": "No such file or directory"},
    "emfile":  {"errno": 24, "desc": "Too many open files"},
    "enosys":  {"errno": 38, "desc": "Function not implemented"},
}

# BCC eBPF 程序模板
BPF_PROGRAM_TEMPLATE = r"""
#include <uapi/linux/ptrace.h>
#include <linux/sched.h>

// 错误码 (负值)
#define INJECT_ERRNO -{errno_num}
#define TARGET_PID {target_pid}
#define FAIL_RATE {fail_rate}

// 简单哈希用于随机决策
static u32 quick_hash(u32 x) {{
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = ((x >> 16) ^ x) * 0x45d9f3b;
    x = (x >> 16) ^ x;
    return x;
}}

// 拦截 read 返回
TRACEPOINT_FN(tracepoint/syscalls/sys_exit_read)
int on_read_exit(struct tracepoint_syscalls_sys_exit_read *ctx) {{
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    if (pid != TARGET_PID)
        return 0;

    if (FAIL_RATE < 100) {{
        u32 h = quick_hash((u32)bpf_ktime_get_ns());
        if (h % 100 >= FAIL_RATE)
            return 0;
    }}

    ctx->ret = INJECT_ERRNO;
    return 0;
}}

// 拦截 write 返回
TRACEPOINT_FN(tracepoint/syscalls/sys_exit_write)
int on_write_exit(struct tracepoint_syscalls_sys_exit_write *ctx) {{
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    if (pid != TARGET_PID)
        return 0;

    if (FAIL_RATE < 100) {{
        u32 h = quick_hash((u32)bpf_ktime_get_ns());
        if (h % 100 >= FAIL_RATE)
            return 0;
    }}

    ctx->ret = INJECT_ERRNO;
    return 0;
}}

// 拦截 openat 返回
TRACEPOINT_FN(tracepoint/syscalls/sys_exit_openat)
int on_openat_exit(struct tracepoint_syscalls_sys_exit_openat *ctx) {{
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    if (pid != TARGET_PID)
        return 0;

    if (FAIL_RATE < 100) {{
        u32 h = quick_hash((u32)bpf_ktime_get_ns());
        if (h % 100 >= FAIL_RATE)
            return 0;
    }}

    ctx->ret = INJECT_ERRNO;
    return 0;
}}

// 拦截 close 返回
TRACEPOINT_FN(tracepoint/syscalls/sys_exit_close)
int on_close_exit(struct tracepoint_syscalls_sys_exit_close *ctx) {{
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    if (pid != TARGET_PID)
        return 0;

    if (FAIL_RATE < 100) {{
        u32 h = quick_hash((u32)bpf_ktime_get_ns());
        if (h % 100 >= FAIL_RATE)
            return 0;
    }}

    ctx->ret = INJECT_ERRNO;
    return 0;
}}

// 拦截 fsync 返回
TRACEPOINT_FN(tracepoint/syscalls/sys_exit_fsync)
int on_fsync_exit(struct tracepoint_syscalls_sys_exit_fsync *ctx) {{
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;

    if (pid != TARGET_PID)
        return 0;

    ctx->ret = INJECT_ERRNO;
    return 0;
}}
"""


def inject(pid, error_type, fail_rate):
    err_info = ERROR_MAP.get(error_type)
    if not err_info:
        print(f"[ERROR] 不支持的错误类型: {error_type}")
        return

    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME}")
    print(f"  目标 PID:    {pid}")
    print(f"  错误类型:    {error_type.upper()} ({err_info['desc']})")
    print(f"  失败率:      {fail_rate}%")
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

    # 检查 BCC 是否可用
    try:
        from bcc import BPF
    except ImportError:
        print("\n[ERROR] BCC (BPF Compiler Collection) 未安装")
        print("  安装方法:")
        print("    Ubuntu: apt install -y bpfcc-tools python3-bpfcc linux-headers-$(uname -r)")
        print("    CentOS: yum install -y bcc-tools python-bcc kernel-devel")
        print()
        print("  备选方案: 使用 pio-fault-02 (ptrace) 或 pio-fault-01 (LD_PRELOAD)")
        return

    confirm_inject(FAULT_NAME, "HIGH", f"PID {pid}", 0)

    # 生成 BPF 程序
    bpf_text = BPF_PROGRAM_TEMPLATE.format(
        errno_num=err_info["errno"],
        target_pid=pid,
        fail_rate=fail_rate,
    )

    print("[注入] 编译并加载 eBPF 程序...")
    try:
        b = BPF(text=bpf_text)
    except Exception as e:
        print(f"[ERROR] eBPF 编译失败: {e}")
        print("[提示] 请确保已安装内核头文件: apt install linux-headers-$(uname -r)")
        return

    # 附加到 tracepoint
    try:
        b.attach_tracepoint(tp="syscalls:sys_exit_read", fn_name="on_read_exit")
        b.attach_tracepoint(tp="syscalls:sys_exit_write", fn_name="on_write_exit")
        b.attach_tracepoint(tp="syscalls:sys_exit_openat", fn_name="on_openat_exit")
        b.attach_tracepoint(tp="syscalls:sys_exit_close", fn_name="on_close_exit")
        b.attach_tracepoint(tp="syscalls:sys_exit_fsync", fn_name="on_fsync_exit")
    except Exception as e:
        print(f"[ERROR] 附加 tracepoint 失败: {e}")
        return

    print("  [OK] eBPF 程序已加载并附加到 I/O tracepoints")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "error_type": error_type,
        "fail_rate": fail_rate,
    })

    print(f"\n[效果] 进程 {pid} 的 I/O syscall 返回值已被 eBPF 修改为 -{error_type.upper()}")
    print(f"  按 Ctrl+C 停止注入并卸载 eBPF 程序")

    _running = True
    def handle_signal(signum, frame):
        nonlocal _running
        print(f"\n[信号] 卸载 eBPF 程序...")
        _running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # 保持运行
    count = 0
    while _running:
        try:
            # 打印 eBPF 事件（如果有）
            # b.trace_print()  # 可选：打印 trace 消息
            time.sleep(1)
            count += 1
            if count % 30 == 0:
                print(f"  [运行中] eBPF 注入活跃, 目标 PID {pid}, 已运行 {count}s")
        except KeyboardInterrupt:
            break

    # 卸载
    print("[清理] 卸载 eBPF 程序...")
    del b
    cleanup_state(FAULT_ID)
    print("[OK] eBPF 程序已卸载，进程 I/O 恢复正常")


def do_cleanup():
    state = load_state(FAULT_ID)
    pid = state.get("target_pid")
    print(f"[清理] eBPF 程序随脚本退出自动卸载")
    if pid:
        print(f"  目标进程 {pid} 的 I/O 应已恢复正常")
    cleanup_state(FAULT_ID)
    print("[OK] 清理完成")


def main():
    require_root()
    parser = argparse.ArgumentParser(description=FAULT_NAME)
    parser.add_argument("--pid", type=int, help="目标进程 PID")
    parser.add_argument("--error-type", default="eio",
                        choices=list(ERROR_MAP.keys()),
                        help="注入的错误类型 (默认: eio)")
    parser.add_argument("--fail-rate", type=int, default=100,
                        help="失败率百分比 1-100 (默认: 100)")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复")
    args = parser.parse_args()

    if args.cleanup:
        do_cleanup()
    elif args.pid:
        inject(args.pid, args.error_type, args.fail_rate)
    else:
        parser.print_help()
        print("\n[ERROR] 请指定 --pid 或 --cleanup")


if __name__ == "__main__":
    main()
