#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-13: 随机 IO 错误注入 — 模拟后端 IO Error
=================================================
使用 LD_PRELOAD 拦截 read/write/open 系统调用，随机返回 EIO。

现象:
    - 程序随机遇到 "Input/output error"
    - 文件操作不可预测地失败
    - 模拟华为云告警 17321020082 (后端 IO Error)

用法:
    sudo python3 fault-13-random-io-error.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-13-random-io-error.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import subprocess

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "fault-13"
FAULT_NAME = "随机 IO 错误注入 (模拟后端 IO Error)"

INJECT_DIR = "/tmp/sfs_fault_ldpreload"
FAIL_RATE = 10  # 10% 失败率

C_SOURCE = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdlib.h>
#include <unistd.h>

#define FAIL_RATE """ + str(FAIL_RATE) + r"""

static int should_fail(void) {
    return (rand() % 100) < FAIL_RATE;
}

typedef ssize_t (*orig_write_t)(int, const void *, size_t);
typedef ssize_t (*orig_read_t)(int, void *, size_t);

ssize_t write(int fd, const void *buf, size_t count) {
    if (should_fail()) { errno = EIO; return -1; }
    orig_write_t fn = (orig_write_t)dlsym(RTLD_NEXT, "write");
    return fn(fd, buf, count);
}

ssize_t read(int fd, void *buf, size_t count) {
    if (should_fail()) { errno = EIO; return -1; }
    orig_read_t fn = (orig_read_t)dlsym(RTLD_NEXT, "read");
    return fn(fd, buf, count);
}

int open(const char *path, int flags, ...) {
    if (should_fail()) { errno = EIO; return -1; }
    typedef int (*orig_open_t)(const char *, int, ...);
    orig_open_t fn = (orig_open_t)dlsym(RTLD_NEXT, "open");
    return fn(path, flags, 0644);
}
"""


def inject(mount_point: str, duration: int):
    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    os.makedirs(INJECT_DIR, exist_ok=True)
    c_file = os.path.join(INJECT_DIR, "fault_inject.c")
    so_file = os.path.join(INJECT_DIR, "fault_inject.so")

    print(f"[注入] 编译 LD_PRELOAD 故障注入库...")
    with open(c_file, "w") as f:
        f.write(C_SOURCE)

    rc, _, err = run_cmd(f"gcc -shared -fPIC -o {so_file} {c_file} -ldl 2>&1")
    if rc != 0:
        print(f"[ERROR] 编译失败: {err}")
        print("[提示] 请安装 gcc: yum install -y gcc  或  apt install -y gcc")
        return

    print(f"  [OK] {so_file}")

    save_state(FAULT_ID, {"inject_dir": INJECT_DIR, "so_file": so_file})

    print(f"\n[效果] 使用 LD_PRELOAD 运行的程序有 {FAIL_RATE}% 概率遇到 IO 错误")
    print(f"\n  使用方法:")
    print(f"    LD_PRELOAD={so_file} cp /etc/hosts {mount_point}/test_copy")
    print(f"    LD_PRELOAD={so_file} dd if=/dev/zero of={mount_point}/test bs=1M count=10")
    print(f"    LD_PRELOAD={so_file} find {mount_point} -type f -exec cat {{}} \\; > /dev/null")
    print(f"\n  对已有运行中的进程:")
    print(f"    grep -r 'NFS' /proc/<pid>/maps  # 确认是否使用 NFS")
    print(f"    # 注意: LD_PRELOAD 只对新启动的进程生效")


def do_cleanup(mount_point: str):
    state = load_state(FAULT_ID)
    inject_dir = state.get("inject_dir", INJECT_DIR)

    if os.path.exists(inject_dir):
        print(f"[清理] 删除 {inject_dir} ...")
        run_cmd(f"rm -rf {inject_dir}")

    cleanup_state(FAULT_ID)
    print("[OK] LD_PRELOAD 注入库已清理")


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
