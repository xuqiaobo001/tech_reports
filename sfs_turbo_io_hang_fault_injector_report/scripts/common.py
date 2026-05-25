#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SFS Turbo IO 故障注入公共库
===========================
所有 fault-XX.py 脚本共用的工具函数。

用法（在各故障脚本中）:
    from common import run_cmd, get_sfs_ip, get_interface, save_state, load_state, cleanup_state, require_root
"""

import json
import os
import socket
import subprocess
import sys
import time


STATE_DIR = "/tmp/sfs_turbo_fault_state"


def run_cmd(cmd: str, timeout: int = 30, check: bool = False) -> tuple:
    """执行 shell 命令，返回 (returncode, stdout, stderr)"""
    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"命令失败: {cmd}\n{proc.stderr}")
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    except Exception as e:
        return -1, "", str(e)


def require_root():
    """检查 root 权限"""
    if os.geteuid() != 0:
        print("[ERROR] 需要 root 权限，请使用 sudo 执行")
        sys.exit(1)


def get_sfs_ip(mount_point: str) -> str:
    """从 mount 信息中提取 SFS Turbo 服务端 IP 或域名"""
    rc, out, _ = run_cmd(f"mount | grep '{mount_point}' | grep nfs")
    if rc == 0 and out:
        host = out.split()[0].split(":")[0]
        try:
            socket.inet_aton(host)
            return host
        except socket.error:
            try:
                return socket.gethostbyname(host)
            except socket.gaierror:
                return host
    return ""


def get_interface(target_ip: str) -> str:
    """获取到达目标 IP 的网卡接口名"""
    rc, out, _ = run_cmd(f"ip route get {target_ip} 2>/dev/null")
    if rc == 0 and out:
        parts = out.split()
        for i, p in enumerate(parts):
            if p == "dev" and i + 1 < len(parts):
                return parts[i + 1]
    return ""


def save_state(fault_id: str, data: dict):
    """保存故障状态到文件"""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{fault_id}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_state(fault_id: str) -> dict:
    """读取故障状态"""
    path = os.path.join(STATE_DIR, f"{fault_id}.json")
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return {}


def cleanup_state(fault_id: str):
    """删除故障状态文件"""
    path = os.path.join(STATE_DIR, f"{fault_id}.json")
    if os.path.exists(path):
        os.remove(path)


def confirm_inject(name: str, danger: str, mount_point: str, duration: int):
    """注入前确认提示"""
    print("=" * 60)
    print(f"  故障注入: {name}")
    print(f"  危险等级: {danger}")
    print(f"  挂载点:   {mount_point}")
    print(f"  持续时间: {'自动恢复 ' + str(duration) + ' 秒' if duration else '无限（需手动恢复）'}")
    print("=" * 60)
    ans = input("\n确认注入? 输入 YES 继续: ")
    if ans != "YES":
        print("[取消] 已取消")
        sys.exit(0)


def banner(fault_id: str, name: str, mount_point: str, sfs_ip: str, interface: str = "", duration: int = 0):
    """打印注入信息"""
    print()
    print("=" * 60)
    print(f"  [{fault_id}] {name}")
    print(f"  挂载点: {mount_point}")
    if sfs_ip:
        print(f"  SFS Turbo IP: {sfs_ip}")
    if interface:
        print(f"  网卡接口: {interface}")
    if duration:
        print(f"  自动恢复: {duration} 秒后")
    print("=" * 60)
    print()


def auto_wait_cleanup(duration: int, cleanup_fn, fault_id: str):
    """如果有 duration 参数，等待后自动执行 cleanup"""
    if duration > 0:
        print(f"\n[AUTO] {duration} 秒后自动恢复...")
        try:
            time.sleep(duration)
        except KeyboardInterrupt:
            pass
        cleanup_fn()
    else:
        print(f"\n[提示] 手动恢复: sudo python3 {sys.argv[0]} --cleanup")


def parse_args(description: str):
    """统一参数解析"""
    import argparse
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--mount-point", default="/mnt/sfs_turbo", help="SFS Turbo 挂载点 (默认: /mnt/sfs_turbo)")
    parser.add_argument("--duration", type=int, default=0, help="故障持续时间(秒), 0=持续直到手动恢复")
    parser.add_argument("--cleanup", action="store_true", help="清理/恢复故障")
    return parser.parse_args()
