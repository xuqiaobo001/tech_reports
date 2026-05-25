#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
业务进程模拟器 — 基于文件锁的数据读写
======================================
模拟正常业务进程，尝试获取排他锁后进行读写操作。
用于配合 fault-08 验证文件锁竞争对业务的影响。

行为:
    1. 尝试获取文件锁（非阻塞，失败则重试）
    2. 获取成功 → 读取文件最后 10 个字符 + 写入时间戳和随机文字
    3. 释放锁，等待一个随机间隔后重复
    4. 默认运行 1 小时

用法:
    sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo
    sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo --runtime 7200
    sudo python3 business-app-filelock.py --mount-point /mnt/sfs_turbo --lock-timeout 5
"""

import argparse
import datetime
import fcntl
import os
import random
import string
import signal
import sys
import time


DATA_FILE = ".business_app_datafile"
LOCK_FILE = ".fault_injector_lockfile"
DEFAULT_RUNTIME = 3600  # 1 小时
LOCK_RETRY_INTERVAL = 2  # 获取锁失败后重试间隔（秒）
LOOP_INTERVAL_MIN = 3    # 每轮循环最小间隔（秒）
LOOP_INTERVAL_MAX = 8    # 每轮循环最大间隔（秒）

# 停止标志
_running = True


def handle_signal(signum, frame):
    global _running
    print(f"\n[信号] 收到信号 {signum}，准备退出...")
    _running = False


def setup_signals():
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def try_lock(lock_path, timeout):
    """
    尝试获取文件排他锁（非阻塞 + 超时重试）。
    返回 (file_object, elapsed_seconds)，获取失败返回 (None, elapsed_seconds)。
    """
    deadline = time.monotonic() + timeout
    attempts = 0

    while time.monotonic() < deadline:
        try:
            f = open(lock_path, "w")
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f, timeout - (deadline - time.monotonic())
        except (IOError, OSError):
            f.close()
            attempts += 1
            if _running and time.monotonic() + LOCK_RETRY_INTERVAL < deadline:
                time.sleep(LOCK_RETRY_INTERVAL)
            else:
                break
        except Exception as e:
            print(f"  [异常] 获取锁时出错: {e}")
            try:
                f.close()
            except Exception:
                pass
            break

    elapsed = timeout - (deadline - time.monotonic())
    return None, elapsed


def read_tail(filepath, n=10):
    """读取文件最后 n 个字符，文件不存在或为空返回空字符串。"""
    if not os.path.exists(filepath):
        return ""
    try:
        with open(filepath, "r") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return "<空文件>"
            f.seek(max(0, size - n))
            return f.read()
    except Exception as e:
        return f"<读取失败: {e}>"


def write_entry(filepath):
    """向文件追加当前时间和随机文字。"""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rand_text = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    entry = f"[{now}] PID={os.getpid()} data={rand_text}\n"
    try:
        with open(filepath, "a") as f:
            f.write(entry)
            f.flush()
            os.fsync(f.fileno())
        return entry.strip()
    except Exception as e:
        return f"<写入失败: {e}>"


def main():
    parser = argparse.ArgumentParser(
        description="业务进程模拟器 — 基于文件锁的数据读写"
    )
    parser.add_argument(
        "--mount-point", default="/mnt/sfs_turbo",
        help="SFS Turbo 挂载点 (默认: /mnt/sfs_turbo)"
    )
    parser.add_argument(
        "--runtime", type=int, default=DEFAULT_RUNTIME,
        help=f"总运行时间秒数 (默认: {DEFAULT_RUNTIME})"
    )
    parser.add_argument(
        "--lock-timeout", type=int, default=10,
        help="每次获取锁的超时时间秒数 (默认: 10)"
    )
    args = parser.parse_args()

    mount_point = args.mount_point
    runtime = args.runtime
    lock_timeout = args.lock_timeout

    data_path = os.path.join(mount_point, DATA_FILE)
    lock_path = os.path.join(mount_point, LOCK_FILE)

    # 确保挂载点存在
    if not os.path.ismount(mount_point):
        print(f"[警告] {mount_point} 未挂载为文件系统，继续运行但可能出错")

    setup_signals()

    print("=" * 60)
    print("  业务进程模拟器 — 文件锁数据读写")
    print(f"  挂载点:    {mount_point}")
    print(f"  数据文件:  {data_path}")
    print(f"  锁文件:    {lock_path}")
    print(f"  运行时长:  {runtime} 秒 ({runtime / 60:.0f} 分钟)")
    print(f"  锁超时:    {lock_timeout} 秒")
    print(f"  进程 PID:  {os.getpid()}")
    print("=" * 60)
    print()

    start_time = time.monotonic()
    cycle = 0
    stats = {"success": 0, "timeout": 0, "errors": 0}

    while _running:
        elapsed_total = time.monotonic() - start_time
        if elapsed_total >= runtime:
            print(f"\n[结束] 已达到运行时长 {runtime} 秒，退出")
            break

        cycle += 1
        remaining = int(runtime - elapsed_total)
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] 第 {cycle} 轮 | 剩余 {remaining}s | 尝试获取锁...", end=" ", flush=True)

        # 尝试获取锁
        lock_fd = None
        try:
            lock_fd, lock_elapsed = try_lock(lock_path, lock_timeout)

            if lock_fd is None:
                stats["timeout"] += 1
                print(f"获取失败 (耗时 {lock_elapsed:.1f}s, 累计超时 {stats['timeout']} 次)")
            else:
                stats["success"] += 1
                print(f"成功 (耗时 {lock_elapsed:.1f}s)", flush=True)

                # 读取最后 10 个字符
                tail = read_tail(data_path, 10)
                print(f"        读取尾部: ...{tail}")

                # 写入新数据
                written = write_entry(data_path)
                print(f"        写入数据: {written}")

                # 释放锁
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
                lock_fd = None

        except Exception as e:
            stats["errors"] += 1
            print(f"异常: {e}")
        finally:
            if lock_fd is not None:
                try:
                    lock_fd.close()
                except Exception:
                    pass

        # 循环间隔
        interval = random.uniform(LOOP_INTERVAL_MIN, LOOP_INTERVAL_MAX)
        # 截断到剩余运行时间内
        elapsed_total = time.monotonic() - start_time
        if elapsed_total + interval >= runtime:
            break
        if _running:
            time.sleep(interval)

    # 汇总统计
    total_time = time.monotonic() - start_time
    print()
    print("=" * 60)
    print("  运行统计")
    print(f"  总运行时间:  {total_time:.0f} 秒 ({total_time / 60:.1f} 分钟)")
    print(f"  总循环次数:  {cycle}")
    print(f"  获取锁成功:  {stats['success']} 次")
    print(f"  获取锁超时:  {stats['timeout']} 次")
    print(f"  异常次数:    {stats['errors']} 次")
    if stats['success'] + stats['timeout'] > 0:
        rate = stats['success'] / (stats['success'] + stats['timeout']) * 100
        print(f"  锁获取成功率: {rate:.1f}%")
    print("=" * 60)


if __name__ == "__main__":
    main()
