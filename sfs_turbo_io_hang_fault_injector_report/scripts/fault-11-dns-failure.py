#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fault-11: DNS 解析故障 — 域名无法解析
=======================================
修改 /etc/resolv.conf 模拟 DNS 故障。

现象:
    - nslookup 解析超时/失败
    - 使用域名的新挂载失败
    - 已挂载的可能不受影响 (IP 缓存在内核)

用法:
    sudo python3 fault-11-dns-failure.py --mount-point /mnt/sfs_turbo
    sudo python3 fault-11-dns-failure.py --cleanup --mount-point /mnt/sfs_turbo
"""

import os
import shutil

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, auto_wait_cleanup, parse_args, run_cmd
)

FAULT_ID = "fault-11"
FAULT_NAME = "DNS 解析故障 (域名无法解析)"

RESOLV_CONF = "/etc/resolv.conf"
BACKUP_FILE = "/etc/resolv.conf.fault_injector_bak"


def inject(mount_point: str, duration: int):
    banner(FAULT_ID, FAULT_NAME, mount_point, "", duration=duration)
    confirm_inject(FAULT_NAME, "HIGH", mount_point, duration)

    print(f"[注入] 备份 {RESOLV_CONF} ...")
    shutil.copy2(RESOLV_CONF, BACKUP_FILE)

    print(f"[注入] 替换 DNS 为不可达地址...")
    with open(RESOLV_CONF, "w") as f:
        f.write("# FAULT INJECTOR: DNS 故障注入 - 请勿手动修改\n")
        f.write("nameserver 255.255.255.255\n")

    save_state(FAULT_ID, {"backup": BACKUP_FILE})

    print(f"\n[效果] DNS 解析将失败")
    print(f"  验证: nslookup huawei.com  (应超时)")
    print(f"  [注意] 已挂载的 NFS 不受影响（IP 已缓存在内核）")

    auto_wait_cleanup(duration, do_cleanup, FAULT_ID)


def do_cleanup():
    state = load_state(FAULT_ID)
    backup = state.get("backup", BACKUP_FILE)

    if os.path.exists(backup):
        print(f"[清理] 恢复 {RESOLV_CONF} ...")
        shutil.copy2(backup, RESOLV_CONF)
        os.remove(backup)
        print("[OK] DNS 已恢复")
    else:
        print("[WARN] 备份文件不存在，请手动检查 /etc/resolv.conf")

    cleanup_state(FAULT_ID)


def main():
    require_root()
    args = parse_args(FAULT_NAME)
    if args.cleanup:
        do_cleanup()
    else:
        inject(args.mount_point, args.duration)


if __name__ == "__main__":
    main()
