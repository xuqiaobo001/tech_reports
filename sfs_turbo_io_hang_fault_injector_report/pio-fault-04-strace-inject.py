#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-04: strace 故障注入启动器
====================================
通过 strace 的 -e inject 故障注入功能启动目标程序，
在系统调用级别注入 I/O 错误。

注入原理:
    strace 从 4.15 版本开始支持 -e inject=syscall:retval=errno 功能，
    可以在指定系统调用返回时注入错误码，无需编写任何 C 代码。

适用场景:
    - 快速验证程序对特定系统调用错误的处理
    - 不需要编写 LD_PRELOAD 库
    - 适合开发调试阶段的故障模拟

优点:
    ✓ 使用简单，一条命令即可注入
    ✓ 内置在 strace 中，无需额外编译
    ✓ 可精确指定注入的系统调用和错误码
    ✓ 可设置注入频率 (when=条件表达式)
    ✓ 支持延迟注入 (delay_enter/delay_exit)

限制:
    ✗ 进程必须在 strace 下启动（不能附加到已运行进程）
    ✗ strace 本身有性能开销（约 2-10x 降速）
    ✗ strace 使用 ptrace 实现，不能同时使用其他 ptrace 工具
    ✗ 需要安装 strace (>= 4.15)

现象:
    - 目标程序的 read/write/open 等系统调用返回指定错误
    - strace 输出显示被注入的 syscall 及错误

用法:
    sudo python3 pio-fault-04-strace-inject.py --error-type eio -- command args...
    sudo python3 pio-fault-04-strace-inject.py --error-type enospc -- dd if=/dev/zero of=/mnt/test bs=1M count=100
    sudo python3 pio-fault-04-strace-inject.py --error-type eio --fail-rate 50 -- cp /etc/hosts /mnt/sfs_turbo/test
"""

import argparse
import os
import shutil
import subprocess
import sys

from common import (
    require_root, save_state, cleanup_state, banner, run_cmd
)

FAULT_ID = "pio-fault-04"
FAULT_NAME = "strace 故障注入启动器"

# 错误类型映射 (strace 使用错误名称)
ERROR_MAP = {
    "eio":     "EIO",
    "eacces":  "EACCES",
    "emfile":  "EMFILE",
    "enospc":  "ENOSPC",
    "enoent":  "ENOENT",
    "enotdir": "ENOTDIR",
    "eperm":   "EPERM",
    "enosys":  "ENOSYS",
}

# 要注入的 I/O 系统调用列表
IO_SYSCALLS = "read,write,open,openat,close,fsync,fdatasync,stat,lstat,newfstatat,truncate,ftruncate,mkdir,rmdir,unlink,rename,chmod,chown,pread64,pwrite64,readv,writev,getdents64"


def inject(error_type, fail_rate, delay_ms, command):
    err_name = ERROR_MAP.get(error_type)
    if not err_name:
        print(f"[ERROR] 不支持的错误类型: {error_type}")
        return

    # 检查 strace 是否安装
    strace_path = shutil.which("strace")
    if not strace_path:
        print("[ERROR] strace 未安装")
        print("  安装: apt install -y strace  或  yum install -y strace")
        return

    # 检查 strace 版本是否支持 inject
    rc, out, _ = run_cmd("strace --version 2>&1")
    print(f"  strace 版本: {out.strip() if out else 'unknown'}")

    # 构建 strace inject 参数
    # 格式: -e inject=syscall:retval=errno[:when=expr]
    inject_spec = f"{IO_SYSCALLS}:retval={err_name}"

    if fail_rate < 100:
        # strace when 参数: when=1+N 表示每 N 次注入一次
        # fail_rate=50 → 每 2 次注入 1 次
        interval = max(1, round(100 / fail_rate))
        inject_spec += f":when=1+{interval-1}"

    if delay_ms > 0:
        inject_spec += f":delay_enter={delay_ms}ms"

    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME}")
    print(f"  错误类型:    {err_name}")
    print(f"  失败率:      {fail_rate}%")
    if delay_ms:
        print(f"  延迟:        {delay_ms}ms")
    print(f"  目标命令:    {' '.join(command)}")
    print("=" * 60)
    print()

    # 构建 strace 命令
    strace_cmd = [
        strace_path,
        "-f",                           # 追踪子进程
        "-e", f"inject={inject_spec}",   # 故障注入
        "-o", "/tmp/strace_fault_output.log",  # 输出到文件
        "--"
    ] + command

    print(f"[注入] 启动命令:")
    print(f"  {' '.join(strace_cmd)}")
    print()

    save_state(FAULT_ID, {
        "command": command,
        "error_type": error_type,
        "strace_log": "/tmp/strace_fault_output.log",
    })

    print(f"[效果] strace 将拦截所有 I/O syscall 并注入 {err_name}")
    print(f"  日志: tail -f /tmp/strace_fault_output.log")
    print()

    # 执行
    try:
        proc = subprocess.Popen(strace_cmd)
        proc.wait()
        exit_code = proc.returncode
        print(f"\n[完成] 进程退出码: {exit_code}")
    except KeyboardInterrupt:
        print(f"\n[中断] 终止目标进程...")
        proc.terminate()
        proc.wait()
    except Exception as e:
        print(f"\n[ERROR] 启动失败: {e}")
        return

    # 显示注入统计
    log_file = "/tmp/strace_fault_output.log"
    if os.path.exists(log_file):
        rc, count, _ = run_cmd(f"grep -c '{err_name}' {log_file} 2>/dev/null")
        if rc == 0 and count:
            print(f"  注入 {err_name} 次数: {count}")
        print(f"  完整日志: {log_file}")

    cleanup_state(FAULT_ID)


def main():
    parser = argparse.ArgumentParser(
        description=FAULT_NAME,
        epilog="示例:\n"
               "  sudo python3 pio-fault-04-strace-inject.py --error-type eio -- cp /etc/hosts /tmp/test\n"
               "  sudo python3 pio-fault-04-strace-inject.py --error-type enospc -- dd if=/dev/zero of=/tmp/test bs=1M count=100\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--error-type", default="eio",
                        choices=list(ERROR_MAP.keys()),
                        help="注入的错误类型 (默认: eio)")
    parser.add_argument("--fail-rate", type=int, default=100,
                        help="失败率百分比 1-100 (默认: 100)")
    parser.add_argument("--delay", type=int, default=0,
                        help="syscall 延迟 (毫秒, 默认: 0)")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="要运行的目标命令 (用 -- 分隔)")
    args = parser.parse_args()

    # 跳过前导的 --
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]

    if not cmd:
        parser.print_help()
        print("\n[ERROR] 请指定要运行的目标命令")
        print("  用法: sudo python3 pio-fault-04-strace-inject.py --error-type eio -- <command>")
        return

    inject(args.error_type, args.fail_rate, args.delay, cmd)


if __name__ == "__main__":
    main()
