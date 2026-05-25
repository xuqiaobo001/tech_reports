#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pio-fault-03: cgroups v2 I/O 限流禁止
=======================================
使用 cgroups v2 的 io.max 控制器将目标进程的 I/O 带宽和 IOPS 限制为 0，
从内核层面完全禁止指定进程的 I/O 操作。

注入原理:
    将目标进程 PID 移入专用 cgroup，设置 io.max 的 rbytes/wbytes/riops/wiops
    为 "0"，内核在 I/O 调度层直接拒绝该 cgroup 的所有 I/O 请求。

适用场景:
    - 从内核层面对运行中进程完全禁止 I/O（无需重启）
    - 模拟存储完全不可达（进程 I/O 请求在调度层被拒绝）
    - 验证应用对 I/O 完全阻塞的容错能力
    - 区分 I/O 饱和（极慢）和 I/O 禁止（完全不可用）

优点:
    ✓ 内核级别控制，对所有类型的 I/O 有效
    ✓ 可对运行中进程热注入，无需重启
    ✓ 对目标进程零侵入（不需要 ptrace 或 LD_PRELOAD）
    ✓ 性能开销极低（cgroup 控制器在内核 I/O 路径上）
    ✓ 可精确到块设备级别控制

限制:
    ✗ 需要内核支持 cgroups v2 + io 控制器（4.5+）
    ✗ 对 NFS 等网络文件系统效果有限（NFS I/O 不走块设备层）
    ✗ 进程已在进行的 I/O 可能不受影响（直到下一次 I/O）
    ✗ 需要知道目标块设备名称

现象:
    - 目标进程的 I/O 请求在内核层被拒绝
    - read/write 返回 EIO 或进程进入 D-state 等待
    - 进程无法读写指定块设备上的任何数据

用法:
    sudo python3 pio-fault-03-cgroups-io-throttle.py --pid 12345
    sudo python3 pio-fault-03-cgroups-io-throttle.py --pid 12345 --device sda1
    sudo python3 pio-fault-03-cgroups-io-throttle.py --cleanup
"""

import os
import sys
import argparse

from common import (
    require_root, save_state, load_state, cleanup_state,
    banner, confirm_inject, run_cmd
)

FAULT_ID = "pio-fault-03"
FAULT_NAME = "cgroups v2 I/O 限流禁止"
CGROUP_ROOT = "/sys/fs/cgroup"
CGROUP_NAME = "sfs_fault_io_throttle"


def get_process_block_devices(pid):
    """获取进程使用的块设备列表"""
    devices = set()
    try:
        # 从 /proc/<pid>/fd 找出打开的文件所在设备
        fd_dir = f"/proc/{pid}/fd"
        if not os.path.isdir(fd_dir):
            return list(devices)
        for fd in os.listdir(fd_dir):
            try:
                link = os.readlink(os.path.join(fd_dir, fd))
                if link.startswith("/") and not link.startswith("/proc"):
                    # 获取文件所在设备
                    rc, out, _ = run_cmd(f"df --output=source '{link}' 2>/dev/null | tail -1")
                    if rc == 0 and out.strip():
                        dev = out.strip().replace("/dev/", "")
                        if dev and dev != "none":
                            devices.add(dev)
            except Exception:
                pass
    except Exception:
        pass
    return sorted(devices)


def list_all_block_devices():
    """列出系统中所有块设备的 major:minor"""
    devices = {}
    try:
        with open("/proc/partitions", "r") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[0].isdigit():
                    major, minor, size, name = parts[0], parts[1], parts[2], parts[3]
                    devices[name] = f"{major}:{minor}"
    except Exception:
        pass
    return devices


def get_device_major_minor(device_name):
    """获取块设备的 major:minor"""
    rc, out, _ = run_cmd(f"cat /sys/block/{device_name}/dev 2>/dev/null || "
                         f"lsblk -n -o MAJ:MIN /dev/{device_name} 2>/dev/null")
    if rc == 0 and out.strip():
        return out.strip().replace(":", ":")
    return None


def inject(pid, device):
    print()
    print("=" * 60)
    print(f"  [{FAULT_ID}] {FAULT_NAME}")
    print(f"  目标 PID:  {pid}")
    print(f"  cgroup:    {CGROUP_ROOT}/{CGROUP_NAME}")
    print("=" * 60)
    print()

    # 检查目标进程存在
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

    # 检查 cgroups v2 io 控制器
    if not os.path.exists(f"{CGROUP_ROOT}/io.max"):
        print(f"[ERROR] cgroups v2 io 控制器不可用")
        print(f"  请检查: cat {CGROUP_ROOT}/cgroup.controllers")
        return

    # 确定目标设备
    if not device:
        devices = get_process_block_devices(pid)
        if not devices:
            # 回退到列出所有设备
            all_devs = list_all_block_devices()
            print(f"\n  未自动检测到进程使用的块设备。可用设备:")
            for name, mm in all_devs.items():
                print(f"    {name} ({mm})")
            print(f"\n  请使用 --device <name> 指定设备")
            return
        device = devices[0]
        if len(devices) > 1:
            print(f"\n  检测到多个设备: {', '.join(devices)}")
            print(f"  将对全部设备限流")

    print(f"  目标设备:  {device}")
    confirm_inject(FAULT_NAME, "HIGH", f"PID {pid}", 0)

    # 创建 cgroup
    cgroup_path = os.path.join(CGROUP_ROOT, CGROUP_NAME)
    os.makedirs(cgroup_path, exist_ok=True)

    # 获取设备 major:minor
    all_devs = list_all_block_devices()
    dev_mm_list = []
    if device in all_devs:
        dev_mm_list.append(all_devs[device])
    else:
        # 可能是分区名如 sda1
        for dev_name, mm in all_devs.items():
            if dev_name.startswith(device) or device in dev_name:
                dev_mm_list.append(mm)

    if not dev_mm_list:
        print(f"[ERROR] 找不到设备 {device} 的 major:minor")
        return

    # 如果自动检测到多个设备，对所有设备限流
    if not device:
        # 对进程使用的所有设备限流
        for dev in get_process_block_devices(pid):
            if dev in all_devs:
                dev_mm_list.append(all_devs[dev])

    dev_mm_list = list(set(dev_mm_list))

    # 设置 I/O 限制为 0（完全禁止）
    for dev_mm in dev_mm_list:
        rc, _, err = run_cmd(f"echo '{dev_mm} rbytes=0 wbytes=0 riops=0 wiops=0' > "
                             f"{cgroup_path}/io.max 2>&1")
        if rc != 0:
            print(f"[ERROR] 设置 io.max 失败: {err}")
            run_cmd(f"rmdir {cgroup_path} 2>/dev/null")
            return
        print(f"  [OK] {dev_mm} → rbytes=0 wbytes=0 riops=0 wiops=0")

    # 将目标进程移入 cgroup
    try:
        # 移动进程的所有线程
        tasks_dir = f"/proc/{pid}/task"
        if os.path.isdir(tasks_dir):
            for tid in os.listdir(tasks_dir):
                with open(f"{cgroup_path}/cgroup.threads", "w") as f:
                    f.write(tid)
        else:
            with open(f"{cgroup_path}/cgroup.procs", "w") as f:
                f.write(str(pid))
    except Exception as e:
        print(f"[ERROR] 移入 cgroup 失败: {e}")
        run_cmd(f"rmdir {cgroup_path} 2>/dev/null")
        return

    print(f"  [OK] 进程 {pid} 已移入 cgroup {CGROUP_NAME}")

    save_state(FAULT_ID, {
        "target_pid": pid,
        "cgroup_path": cgroup_path,
        "devices": dev_mm_list,
    })

    print(f"\n[效果] 进程 {pid} 的 I/O 已从内核层面禁止")
    print(f"  验证: cat {cgroup_path}/io.stat")
    print(f"  恢复: sudo python3 {sys.argv[0]} --cleanup")


def do_cleanup():
    state = load_state(FAULT_ID)
    cgroup_path = state.get("cgroup_path", os.path.join(CGROUP_ROOT, CGROUP_NAME))
    pid = state.get("target_pid")

    if os.path.exists(cgroup_path):
        print(f"[清理] 移除 cgroup {cgroup_path} ...")

        # 将进程移回根 cgroup
        if pid and os.path.exists(f"/proc/{pid}"):
            try:
                with open(f"{CGROUP_ROOT}/cgroup.procs", "w") as f:
                    f.write(str(pid))
                print(f"  [OK] 进程 {pid} 已移回根 cgroup")
            except Exception as e:
                print(f"  [警告] 移回根 cgroup 失败: {e}")

        # 移除 cgroup
        rc, _, err = run_cmd(f"rmdir {cgroup_path} 2>&1")
        if rc == 0:
            print(f"  [OK] cgroup 已移除")
        else:
            print(f"  [警告] 移除 cgroup 失败: {err}")
            print(f"  [手动] rmdir {cgroup_path}")

    cleanup_state(FAULT_ID)
    print("[OK] I/O 限流已恢复")


def main():
    require_root()
    parser = argparse.ArgumentParser(description=FAULT_NAME)
    parser.add_argument("--pid", type=int, required=not '--cleanup' in sys.argv,
                        help="目标进程 PID")
    parser.add_argument("--device", default=None,
                        help="目标块设备名 (如 sda, vda, 不指定则自动检测)")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复")
    args = parser.parse_args()

    if args.cleanup:
        do_cleanup()
    else:
        inject(args.pid, args.device)


if __name__ == "__main__":
    main()
