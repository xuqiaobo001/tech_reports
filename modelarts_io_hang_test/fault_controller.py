"""
故障触发控制器

在外部（如另一个训练作业或本地机器）运行，通过在 SFS Turbo 上创建/删除标记文件，
远程控制训练作业中的故障注入时机。

使用方式：
    # 触发 read_hang 故障
    python fault_controller.py trigger --trigger-file /mnt/sfs-turbo/.fault_trigger

    # 触发后等待 60 秒再释放
    python fault_controller.py trigger-and-release \
        --trigger-file /mnt/sfs-turbo/.fault_trigger \
        --release-file /mnt/sfs-turbo/.fault_release \
        --hold-seconds 60

    # 监控训练状态文件
    python fault_controller.py monitor --status-file /mnt/sfs-turbo/checkpoints/training_status.txt
"""

import os
import sys
import time
import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="[FaultController] %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("FaultController")


def trigger_fault(trigger_file: str):
    """创建触发文件，触发训练作业中的故障注入"""
    trigger_dir = os.path.dirname(trigger_file)
    if trigger_dir:
        os.makedirs(trigger_dir, exist_ok=True)

    with open(trigger_file, "w") as f:
        f.write(f"triggered_at={time.time()}\n")

    logger.info(f"触发文件已创建: {trigger_file}")
    logger.info("训练作业将在下次检查时检测到触发文件并注入故障")


def release_fault(release_file: str):
    """创建释放文件，解除训练作业中的故障"""
    release_dir = os.path.dirname(release_file)
    if release_dir:
        os.makedirs(release_dir, exist_ok=True)

    with open(release_file, "w") as f:
        f.write(f"released_at={time.time()}\n")

    logger.info(f"释放文件已创建: {release_file}")


def trigger_and_release(trigger_file: str, release_file: str, hold_seconds: float,
                        watch_status: str = None):
    """
    触发故障，等待指定时间后释放。

    Args:
        trigger_file: 触发文件路径
        release_file: 释放文件路径
        hold_seconds: 故障持续时间（秒）
        watch_status: 可选，监控训练状态文件以确认故障已生效
    """
    logger.info(f"=== 故障控制开始 ===")
    logger.info(f"触发文件: {trigger_file}")
    logger.info(f"释放文件: {release_file}")
    logger.info(f"持续时间: {hold_seconds}s")

    # 记录触发前的训练状态
    last_status = None
    if watch_status and os.path.exists(watch_status):
        last_status = _read_status(watch_status)
        logger.info(f"触发前训练状态: {last_status}")

    # 1. 创建触发文件
    trigger_fault(trigger_file)
    logger.info(f"等待 {hold_seconds}s 后释放...")

    # 2. 等待，期间监控训练状态
    start_wait = time.time()
    while time.time() - start_wait < hold_seconds:
        remaining = hold_seconds - (time.time() - start_wait)
        if remaining <= 0:
            break

        # 每 10 秒检查一次训练状态
        if watch_status and os.path.exists(watch_status):
            current_status = _read_status(watch_status)
            if current_status != last_status:
                logger.info(f"训练状态变化: {current_status}")
                last_status = current_status
            else:
                logger.info(f"训练状态未变化（可能 IO 已卡死）: {current_status}")

        time.sleep(min(10, remaining))

    # 3. 创建释放文件
    release_fault(release_file)
    logger.info(f"=== 故障控制结束 ===")

    # 4. 等待一段时间后检查恢复状态
    if watch_status:
        logger.info("等待 30s 后检查恢复状态...")
        time.sleep(30)
        if os.path.exists(watch_status):
            final_status = _read_status(watch_status)
            if final_status != last_status:
                logger.info(f"训练已恢复! 最新状态: {final_status}")
            else:
                logger.warning(f"训练可能未恢复，状态未变化: {final_status}")


def monitor_training(status_file: str, interval: float = 10, timeout: float = 3600):
    """
    持续监控训练状态文件。
    当状态文件长时间不更新时，判定训练可能卡死。

    Args:
        status_file: 训练状态文件路径
        interval: 检查间隔（秒）
        timeout: 超时时间（秒），超过此时间状态未更新则告警
    """
    logger.info(f"开始监控训练状态: {status_file}")
    logger.info(f"检查间隔: {interval}s, 超时阈值: {timeout}s")

    last_content = None
    last_change_time = time.time()

    while True:
        try:
            if not os.path.exists(status_file):
                logger.info("状态文件不存在，等待...")
                time.sleep(interval)
                continue

            current_content = _read_status(status_file)

            if current_content != last_content:
                last_content = current_content
                last_change_time = time.time()
                logger.info(f"状态更新: {current_content}")
            else:
                elapsed = time.time() - last_change_time
                if elapsed > timeout:
                    logger.warning(
                        f"⚠️ 训练状态已 {elapsed:.0f}s 未更新！可能已卡死。"
                        f"最后状态: {current_content}"
                    )
                else:
                    logger.info(f"状态未变化（已 {elapsed:.0f}s），继续等待...")

        except Exception as e:
            logger.error(f"监控异常: {e}")

        time.sleep(interval)


def _read_status(path: str) -> str:
    """读取状态文件内容"""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser(description="故障触发控制器")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # trigger 命令
    trigger_parser = subparsers.add_parser("trigger", help="触发故障")
    trigger_parser.add_argument("--trigger-file", required=True, help="触发文件路径")

    # release 命令
    release_parser = subparsers.add_parser("release", help="释放故障")
    release_parser.add_argument("--release-file", required=True, help="释放文件路径")

    # trigger-and-release 命令
    tr_parser = subparsers.add_parser("trigger-and-release", help="触发后延时释放")
    tr_parser.add_argument("--trigger-file", required=True, help="触发文件路径")
    tr_parser.add_argument("--release-file", required=True, help="释放文件路径")
    tr_parser.add_argument("--hold-seconds", type=float, default=60, help="持续时间")
    tr_parser.add_argument("--watch-status", default="", help="监控训练状态文件")

    # monitor 命令
    monitor_parser = subparsers.add_parser("monitor", help="监控训练状态")
    monitor_parser.add_argument("--status-file", required=True, help="训练状态文件路径")
    monitor_parser.add_argument("--interval", type=float, default=10, help="检查间隔")
    monitor_parser.add_argument("--timeout", type=float, default=300, help="超时阈值")

    args = parser.parse_args()

    if args.command == "trigger":
        trigger_fault(args.trigger_file)
    elif args.command == "release":
        release_fault(args.release_file)
    elif args.command == "trigger-and-release":
        trigger_and_release(
            args.trigger_file, args.release_file,
            args.hold_seconds, args.watch_status,
        )
    elif args.command == "monitor":
        monitor_training(args.status_file, args.interval, args.timeout)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
