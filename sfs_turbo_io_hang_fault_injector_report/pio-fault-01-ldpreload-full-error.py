#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-01: LD_PRELOAD 全量 I/O 错误注入
=============================================
使用 LD_PRELOAD 拦截进程的所有 I/O 相关 C 库函数，
使 read/write/open/close 全部返回错误 (EIO/ENOMEM/EMFILE 等)。

注入原理:
    编译 C 共享库 (.so)，通过 LD_PRELOAD 环境变量注入到目标进程，
    在用户态拦截 libc 的 I/O 函数并返回错误码。

适用场景:
    - 模拟存储后端完全故障 (所有 I/O 返回 EIO)
    - 模拟文件描述符耗尽 (返回 EMFILE)
    - 模拟权限全部丢失 (返回 EACCES)
    - 测试应用对 I/O 错误的容错能力

优点:
    ✓ 精确控制目标进程，不影响系统其他进程
    ✓ 可选择注入的错误类型 (EIO/EACCES/EMFILE/ENOENT)
    ✓ 可控制失败率 (100% 全失败 / 按比例随机失败)

限制:
    ✗ 只对动态链接的程序有效 (静态编译的程序不受影响)
    ✗ 只能拦截 libc 调用，无法拦截直接 syscall (汇编调用)
    ✗ 对已经运行的进程无效 (需要重启进程才能注入)

现象:
    - 目标进程所有文件操作返回 "Input/output error"
    - 写入数据全部丢失，读取全部失败
    - 进程可能崩溃或进入错误处理逻辑

用法:
    sudo python3 pio-fault-01-ldpreload-full-error.py --mount-point /mnt/sfs_turbo
    sudo python3 pio-fault-01-ldpreload-full-error.py --mount-point /mnt/sfs_turbo --error-type eacces
    sudo python3 pio-fault-01-ldpreload-full-error.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import sys
import stat
import argparse

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, parse_args, run_cmd
)

FAULT_ID = "pio-fault-01"
FAULT_NAME = "LD_PRELOAD 全量 I/O 错误注入"
INJECT_DIR = "/tmp/sfs_fault_pio_01"

# 错误类型映射
ERROR_MAP = {
    "eio":     {"errno_val": "EIO",     "errno_num": 5,  "desc": "Input/output error"},
    "eacces":  {"errno_val": "EACCES",  "errno_num": 13, "desc": "Permission denied"},
    "emfile":  {"errno_val": "EMFILE",  "errno_num": 24, "desc": "Too many open files"},
    "enospc":  {"errno_val": "ENOSPC",  "errno_num": 28, "desc": "No space left on device"},
    "enoent":  {"errno_val": "ENOENT",  "errno_num": 2,  "desc": "No such file or directory"},
    "enotdir": {"errno_val": "ENOTDIR", "errno_num": 20, "desc": "Not a directory"},
}

C_SOURCE_TEMPLATE = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <fcntl.h>

/* 错误类型: {errno_val} ({errno_num}) */
#define INJECT_ERRNO {errno_num}
#define FAIL_RATE {fail_rate}

static int should_fail(void) {{
    if (FAIL_RATE >= 100) return 1;
    static unsigned int seed = 0;
    if (seed == 0) seed = (unsigned int) getpid();
    seed = seed * 1103515245 + 12345;
    return ((seed / 65536) % 100) < FAIL_RATE;
}}

/*
 * 拦截 read()  — 读取返回错误
 */
typedef ssize_t (*orig_read_t)(int, void *, size_t);
ssize_t read(int fd, void *buf, size_t count) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_read_t fn = (orig_read_t)dlsym(RTLD_NEXT, "read");
    return fn(fd, buf, count);
}}

/*
 * 拦截 write() — 写入返回错误
 */
typedef ssize_t (*orig_write_t)(int, const void *, size_t);
ssize_t write(int fd, const void *buf, size_t count) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_write_t fn = (orig_write_t)dlsym(RTLD_NEXT, "write");
    return fn(fd, buf, count);
}}

/*
 * 拦截 open() / open64() — 打开文件返回错误
 */
typedef int (*orig_open_t)(const char *, int, ...);
int open(const char *path, int flags, ...) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {{
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }}
    orig_open_t fn = (orig_open_t)dlsym(RTLD_NEXT, "open");
    return fn(path, flags, mode);
}}

int open64(const char *path, int flags, ...) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    mode_t mode = 0;
    if (flags & (O_CREAT | O_TMPFILE)) {{
        va_list ap;
        va_start(ap, flags);
        mode = va_arg(ap, mode_t);
        va_end(ap);
    }}
    orig_open_t fn = (orig_open_t)dlsym(RTLD_NEXT, "open64");
    return fn(path, flags, mode);
}}

/*
 * 拦截 close() — 关闭返回错误 (可能导致资源泄漏)
 */
typedef int (*orig_close_t)(int);
int close(int fd) {{
    /* stdin/stdout/stderr 不拦截 */
    if (fd <= 2) {{
        orig_close_t fn = (orig_close_t)dlsym(RTLD_NEXT, "close");
        return fn(fd);
    }}
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_close_t fn = (orig_close_t)dlsym(RTLD_NEXT, "close");
    return fn(fd);
}}

/*
 * 拦截 fsync() / fdatasync() — 同步返回错误
 */
typedef int (*orig_fsync_t)(int);
int fsync(int fd) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_fsync_t fn = (orig_fsync_t)dlsym(RTLD_NEXT, "fsync");
    return fn(fd);
}}

int fdatasync(int fd) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_fsync_t fn = (orig_fsync_t)dlsym(RTLD_NEXT, "fdatasync");
    return fn(fd);
}}

/*
 * 拦截 stat() / lstat() — 元数据查询返回错误
 */
typedef int (*orig_stat_t)(const char *, struct stat *);
int stat(const char *path, struct stat *buf) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_stat_t fn = (orig_stat_t)dlsym(RTLD_NEXT, "stat");
    return fn(path, buf);
}}

int lstat(const char *path, struct stat *buf) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_stat_t fn = (orig_stat_t)dlsym(RTLD_NEXT, "lstat");
    return fn(path, buf);
}}

/*
 * 拦截 mkdir() — 创建目录返回错误
 */
typedef int (*orig_mkdir_t)(const char *, mode_t);
int mkdir(const char *path, mode_t mode) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_mkdir_t fn = (orig_mkdir_t)dlsym(RTLD_NEXT, "mkdir");
    return fn(path, mode);
}}

/*
 * 拦截 unlink() — 删除文件返回错误
 */
typedef int (*orig_unlink_t)(const char *);
int unlink(const char *path) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_unlink_t fn = (orig_unlink_t)dlsym(RTLD_NEXT, "unlink");
    return fn(path);
}}

/*
 * 拦截 rename() — 重命名返回错误
 */
typedef int (*orig_rename_t)(const char *, const char *);
int rename(const char *old, const char *new) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_rename_t fn = (orig_rename_t)dlsym(RTLD_NEXT, "rename");
    return fn(old, new);
}}

/*
 * 拦截 chmod() — 修改权限返回错误
 */
typedef int (*orig_chmod_t)(const char *, mode_t);
int chmod(const char *path, mode_t mode) {{
    if (should_fail()) {{
        errno = INJECT_ERRNO;
        return -1;
    }}
    orig_chmod_t fn = (orig_chmod_t)dlsym(RTLD_NEXT, "chmod");
    return fn(path, mode);
}}
"""


def inject(mount_point: str, duration: int, error_type: str, fail_rate: int):
    err_info = ERROR_MAP.get(error_type)
    if not err_info:
        print(f"[ERROR] 不支持的错误类型: {error_type}")
        print(f"  可选: {', '.join(ERROR_MAP.keys())}")
        return

    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    print(f"  错误类型:  {err_info['errno_val']} ({err_info['desc']})")
    print(f"  失败率:    {fail_rate}%")
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    os.makedirs(INJECT_DIR, exist_ok=True)
    c_file = os.path.join(INJECT_DIR, "full_io_error.c")
    so_file = os.path.join(INJECT_DIR, "full_io_error.so")

    c_source = C_SOURCE_TEMPLATE.format(
        errno_val=err_info["errno_val"],
        errno_num=err_info["errno_num"],
        fail_rate=fail_rate,
    )

    print(f"[注入] 编译 LD_PRELOAD 故障注入库...")
    with open(c_file, "w") as f:
        f.write(c_source)

    rc, out, err = run_cmd(f"gcc -shared -fPIC -o {so_file} {c_file} -ldl 2>&1")
    if rc != 0:
        print(f"[ERROR] 编译失败: {err}")
        print("[提示] 请安装 gcc: apt install -y gcc")
        return

    print(f"  [OK] {so_file}")

    save_state(FAULT_ID, {
        "inject_dir": INJECT_DIR,
        "so_file": so_file,
        "error_type": error_type,
        "fail_rate": fail_rate,
    })

    print(f"\n{'='*60}")
    print(f"[效果] 所有 I/O 操作以 {fail_rate}% 概率返回 {err_info['errno_val']}")
    print(f"{'='*60}")
    print(f"\n  使用方法:")
    print(f"    # 全部 I/O 返回错误")
    print(f"    LD_PRELOAD={so_file} cp /etc/hosts {mount_point}/test_copy")
    print(f"    LD_PRELOAD={so_file} cat {mount_point}/test_file")
    print(f"    LD_PRELOAD={so_file} python3 -c \"open('{mount_point}/test','w').write('hello')\"")
    print(f"")
    print(f"  对业务进程注入:")
    print(f"    # 1. 找到目标进程 PID")
    print(f"    ps aux | grep <your_app>")
    print(f"    # 2. 停止进程")
    print(f"    kill -STOP <pid>")
    print(f"    # 3. 在 LD_PRELOAD 环境下重启")
    print(f"    LD_PRELOAD={so_file} <your_app_command>")
    print(f"")
    print(f"  注意: LD_PRELOAD 只对新启动的进程生效，已在运行的进程不受影响")


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
    parser = argparse.ArgumentParser(description=FAULT_NAME)
    parser.add_argument("--mount-point", default="/mnt/sfs_turbo",
                        help="SFS Turbo 挂载点 (默认: /mnt/sfs_turbo)")
    parser.add_argument("--duration", type=int, default=0,
                        help="故障持续时间(秒), 0=持续直到手动恢复")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复故障")
    parser.add_argument("--error-type", default="eio",
                        choices=list(ERROR_MAP.keys()),
                        help="注入的错误类型 (默认: eio)")
    parser.add_argument("--fail-rate", type=int, default=100,
                        help="失败率百分比, 1-100 (默认: 100=全部失败)")
    args = parser.parse_args()

    if args.cleanup:
        do_cleanup(args.mount_point)
    else:
        inject(args.mount_point, args.duration, args.error_type, args.fail_rate)


if __name__ == "__main__":
    main()
