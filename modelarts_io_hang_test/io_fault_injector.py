"""
SFS Turbo IO 卡死故障注入器

在 ModelArts 训练作业中模拟 SFS Turbo（NFS）IO 卡死故障，
测试 ModelArts 服务的故障恢复能力。

使用方式：
    from io_fault_injector import IOHangInjector

    injector = IOHangInjector(sfs_mount_path="/mnt/sfs-turbo")
    injector.inject_read_hang(duration=300)            # 读卡死 5 分钟
    injector.inject_checkpoint_hang(duration=None)     # checkpoint 保存无限卡死
    injector.schedule_hang(60, "full_io_hang")         # 60 秒后触发全局 IO 卡死
"""

import os
import sys
import time
import threading
import logging
import signal
import traceback
import builtins
from pathlib import Path
from typing import Optional, Callable

logging.basicConfig(
    level=logging.INFO,
    format="[IO-FaultInjector] %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("IOFaultInjector")


class IOHangInjector:
    """SFS Turbo IO 卡死故障注入器"""

    def __init__(self, sfs_mount_path: str = "/mnt/sfs-turbo"):
        self.sfs_mount_path = sfs_mount_path
        self._stop_event = threading.Event()
        self._hang_threads: list = []
        self._original_open = builtins.open
        self._original_torch_save = None
        self._fault_active = False
        self._trigger_check_interval = 2  # 触发文件检查间隔（秒）

    # ----------------------------------------------------------------
    # 核心工具方法
    # ----------------------------------------------------------------

    def _block(self, duration: Optional[float] = None, label: str = "hang"):
        """
        阻塞当前线程。
        duration=None 时无限阻塞，直到 _stop_event 被设置。
        """
        logger.info(f"[{label}] IO 卡死开始, duration={'∞' if duration is None else f'{duration}s'}")
        start = time.time()

        if duration is None:
            # 无限阻塞，每 30 秒打一次日志确认还活着
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=30)
                elapsed = time.time() - start
                logger.info(f"[{label}] IO 卡死已持续 {elapsed:.0f}s")
        else:
            # 限时阻塞
            self._stop_event.wait(timeout=duration)

        elapsed = time.time() - start
        logger.info(f"[{label}] IO 卡死结束, 实际持续 {elapsed:.0f}s")

    def _do_io_read(self, path: str):
        """在指定路径上执行真实的 NFS 读取操作"""
        filepath = os.path.join(path, f".io_fault_test_read_{os.getpid()}")
        try:
            # 先写一个测试文件
            with self._original_open(filepath, "w") as f:
                f.write("io-fault-test-data " * 100)
            # 然后读回来，如果 NFS 卡死这里会阻塞
            with self._original_open(filepath, "r") as f:
                data = f.read()
            # 清理
            os.unlink(filepath)
            return data
        except Exception as e:
            logger.warning(f"IO read test failed (expected for fault injection): {e}")
            return None

    def _do_io_write(self, path: str, data: str = "x" * 1024 * 1024):
        """在指定路径上执行真实的 NFS 写入操作"""
        filepath = os.path.join(path, f".io_fault_test_write_{os.getpid()}")
        try:
            with self._original_open(filepath, "w") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.unlink(filepath)
        except Exception as e:
            logger.warning(f"IO write test failed (expected for fault injection): {e}")

    # ----------------------------------------------------------------
    # 故障注入方法
    # ----------------------------------------------------------------

    def inject_read_hang(self, path: Optional[str] = None, duration: Optional[float] = None):
        """
        模拟读 IO 卡死。
        启动后台线程在 SFS Turbo 上执行 read 操作并阻塞。
        """
        path = path or self.sfs_mount_path
        logger.info(f"注入读 IO 卡死: path={path}, duration={duration}")

        def _read_hang_worker():
            self._fault_active = True
            # 先触发一次 NFS IO 读取
            self._do_io_read(path)
            # 然后阻塞
            self._block(duration, label="read_hang")
            self._fault_active = False

        t = threading.Thread(target=_read_hang_worker, daemon=True, name="io-read-hang")
        t.start()
        self._hang_threads.append(t)

    def inject_write_hang(self, path: Optional[str] = None, duration: Optional[float] = None):
        """
        模拟写 IO 卡死。
        启动后台线程在 SFS Turbo 上执行 write 操作并阻塞。
        """
        path = path or self.sfs_mount_path
        logger.info(f"注入写 IO 卡死: path={path}, duration={duration}")

        def _write_hang_worker():
            self._fault_active = True
            # 持续写文件并阻塞
            filepath = os.path.join(path, f".io_fault_hang_write_{os.getpid()}")
            try:
                f = self._original_open(filepath, "w")
                f.write("hang-write-test " * 65536)  # ~1MB
                f.flush()
                # 阻塞，不 close 文件，也不删除
                self._block(duration, label="write_hang")
                f.close()
                os.unlink(filepath)
            except Exception as e:
                logger.warning(f"write_hang worker exception: {e}")
            finally:
                self._fault_active = False

        t = threading.Thread(target=_write_hang_worker, daemon=True, name="io-write-hang")
        t.start()
        self._hang_threads.append(t)

    def inject_checkpoint_hang(self, duration: Optional[float] = None):
        """
        模拟 checkpoint 保存卡死。
        Monkey-patch torch.save 使其在调用时阻塞。
        """
        try:
            import torch
        except ImportError:
            logger.error("torch 未安装，无法注入 checkpoint 卡死")
            return

        logger.info(f"注入 checkpoint 保存卡死: duration={duration}")
        original_save = torch.save
        self._original_torch_save = original_save
        _stop = self._stop_event
        _block_fn = self._block
        _duration = duration

        def _hanging_save(*args, **kwargs):
            logger.info("[checkpoint_hang] torch.save() 被调用，开始卡死...")
            _block_fn(_duration, label="checkpoint_hang")
            logger.info("[checkpoint_hang] 卡死结束，执行实际 torch.save()")
            return original_save(*args, **kwargs)

        torch.save = _hanging_save
        logger.info("torch.save() 已被替换为卡死版本")

    def inject_dataloader_hang(self, duration: Optional[float] = None):
        """
        模拟数据加载卡死。
        Monkey-patch DataLoader 的数据集 __getitem__ 方法使其阻塞。
        """
        try:
            from torch.utils.data import Dataset
        except ImportError:
            logger.error("torch 未安装，无法注入 DataLoader 卡死")
            return

        logger.info(f"注入 DataLoader 卡死: duration={duration}")
        _stop = self._stop_event
        _block_fn = self._block
        _duration = duration

        original_getitem = Dataset.__getitem__

        def _hanging_getitem(self_ds, index):
            logger.info(f"[dataloader_hang] __getitem__({index}) 被调用，开始卡死...")
            _block_fn(_duration, label=f"dataloader_hang[idx={index}]")
            logger.info(f"[dataloader_hang] 卡死结束，读取数据 idx={index}")
            return original_getitem(self_ds, index)

        Dataset.__getitem__ = _hanging_getitem
        logger.info("Dataset.__getitem__() 已被替换为卡死版本")

    def inject_full_io_hang(self, duration: Optional[float] = None):
        """
        模拟全局 IO 卡死。
        替换内置 open() 函数，对 SFS Turbo 路径的文件操作全部阻塞。
        """
        logger.info(f"注入全局 IO 卡死: duration={duration}")
        _original_open = self._original_open
        _stop = self._stop_event
        _block_fn = self._block
        _duration = duration
        sfs_path = self.sfs_mount_path

        def _hanging_open(file, mode="r", *args, **kwargs):
            file_str = str(file)
            if file_str.startswith(sfs_path) or sfs_path in file_str:
                logger.info(f"[full_io_hang] open('{file_str}', '{mode}') 被阻塞")
                _block_fn(_duration, label=f"full_io_hang[{file_str}]")
            return _original_open(file, mode, *args, **kwargs)

        builtins.open = _hanging_open
        self._fault_active = True
        logger.info("builtins.open() 已被替换为卡死版本（仅阻塞 SFS 路径）")

    # ----------------------------------------------------------------
    # 延时触发和信号触发
    # ----------------------------------------------------------------

    def schedule_hang(self, delay_seconds: float, fault_type: str, **kwargs):
        """
        延时触发故障。
        在 delay_seconds 秒后自动注入指定类型的故障。

        Args:
            delay_seconds: 延时秒数
            fault_type: 故障类型（read_hang / write_hang / checkpoint_hang /
                        dataloader_hang / full_io_hang）
        """
        logger.info(f"计划在 {delay_seconds}s 后注入 {fault_type} 故障")

        def _scheduled_worker():
            logger.info(f"等待 {delay_seconds}s...")
            time.sleep(delay_seconds)
            logger.info(f"延时到达，开始注入 {fault_type}")
            self._dispatch_fault(fault_type, **kwargs)

        t = threading.Thread(target=_scheduled_worker, daemon=True, name=f"scheduled-{fault_type}")
        t.start()
        self._hang_threads.append(t)

    def wait_for_trigger_file(self, trigger_file: str, fault_type: str, **kwargs):
        """
        等待触发文件出现后注入故障。
        当 trigger_file 指定的文件在文件系统中存在时，触发故障。

        使用方式：在外部通过 touch 命令创建触发文件来控制故障注入时机。
        """
        logger.info(f"等待触发文件: {trigger_file}，故障类型: {fault_type}")

        def _trigger_watcher():
            while not self._stop_event.is_set():
                if os.path.exists(trigger_file):
                    logger.info(f"触发文件 {trigger_file} 已检测到！注入 {fault_type}")
                    # 删除触发文件（如果可以）
                    try:
                        os.unlink(trigger_file)
                    except Exception:
                        pass
                    self._dispatch_fault(fault_type, **kwargs)
                    return
                self._stop_event.wait(timeout=self._trigger_check_interval)

        t = threading.Thread(target=_trigger_watcher, daemon=True, name=f"trigger-watcher-{fault_type}")
        t.start()
        self._hang_threads.append(t)

    def wait_for_release_file(self, release_file: str):
        """
        等待释放文件出现后解除所有故障。
        当 release_file 存在时，设置 stop_event 解除所有阻塞。
        """
        logger.info(f"等待释放文件: {release_file}")

        def _release_watcher():
            while not self._stop_event.is_set():
                if os.path.exists(release_file):
                    logger.info(f"释放文件 {release_file} 已检测到！解除所有故障")
                    try:
                        os.unlink(release_file)
                    except Exception:
                        pass
                    self.release()
                    return
                self._stop_event.wait(timeout=self._trigger_check_interval)

        t = threading.Thread(target=_release_watcher, daemon=True, name="release-watcher")
        t.start()
        self._hang_threads.append(t)

    # ----------------------------------------------------------------
    # 调度辅助
    # ----------------------------------------------------------------

    def _dispatch_fault(self, fault_type: str, **kwargs):
        """根据故障类型分发到对应的注入方法"""
        dispatch = {
            "read_hang": self.inject_read_hang,
            "write_hang": self.inject_write_hang,
            "checkpoint_hang": self.inject_checkpoint_hang,
            "dataloader_hang": self.inject_dataloader_hang,
            "full_io_hang": self.inject_full_io_hang,
        }
        handler = dispatch.get(fault_type)
        if handler:
            handler(**kwargs)
        else:
            logger.error(f"未知故障类型: {fault_type}，可用类型: {list(dispatch.keys())}")

    # ----------------------------------------------------------------
    # 控制方法
    # ----------------------------------------------------------------

    def release(self):
        """解除所有故障注入"""
        logger.info("释放所有故障注入...")
        self._stop_event.set()

        # 恢复 torch.save
        try:
            import torch
            if self._original_torch_save is not None:
                torch.save = self._original_torch_save
                self._original_torch_save = None
                logger.info("torch.save() 已恢复")
        except ImportError:
            pass

        # 恢复 builtins.open
        builtins.open = self._original_open
        logger.info("builtins.open() 已恢复")

        self._fault_active = False
        logger.info("所有故障注入已解除")

    def wait_for_threads(self, timeout: Optional[float] = None):
        """等待所有后台线程结束"""
        for t in self._hang_threads:
            t.join(timeout=timeout)

    @property
    def is_fault_active(self) -> bool:
        """当前是否有活跃的故障"""
        return self._fault_active


# ----------------------------------------------------------------
# 便捷函数：在训练脚本中快速注入故障
# ----------------------------------------------------------------

def inject_from_env():
    """
    从环境变量读取配置并注入故障。
    适合在 ModelArts 训练作业的启动脚本中使用。

    环境变量：
        IO_FAULT_TYPE: 故障类型（read_hang / write_hang / checkpoint_hang /
                       dataloader_hang / full_io_hang）
        IO_FAULT_DELAY: 延时秒数（默认 0）
        IO_FAULT_DURATION: 持续秒数（默认 None=无限）
        IO_FAULT_SFS_PATH: SFS Turbo 挂载路径（默认 /mnt/sfs-turbo）
        IO_FAULT_TRIGGER_FILE: 触发文件路径（可选，设置后等待文件出现再注入）
        IO_FAULT_RELEASE_FILE: 释放文件路径（可选，设置后等待文件出现再解除）

    Returns:
        IOHangInjector 实例
    """
    fault_type = os.environ.get("IO_FAULT_TYPE", "")
    if not fault_type:
        logger.info("IO_FAULT_TYPE 未设置，跳过故障注入")
        return None

    sfs_path = os.environ.get("IO_FAULT_SFS_PATH", "/mnt/sfs-turbo")
    delay = float(os.environ.get("IO_FAULT_DELAY", "0"))
    duration_str = os.environ.get("IO_FAULT_DURATION", "")
    duration = float(duration_str) if duration_str else None
    trigger_file = os.environ.get("IO_FAULT_TRIGGER_FILE", "")
    release_file = os.environ.get("IO_FAULT_RELEASE_FILE", "")

    injector = IOHangInjector(sfs_mount_path=sfs_path)

    # 注册信号处理：SIGTERM 时释放故障
    def _signal_handler(signum, frame):
        logger.info(f"收到信号 {signum}，释放故障注入")
        injector.release()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    if trigger_file:
        injector.wait_for_trigger_file(trigger_file, fault_type, duration=duration)
    elif delay > 0:
        injector.schedule_hang(delay, fault_type, duration=duration)
    else:
        injector._dispatch_fault(fault_type, duration=duration)

    if release_file:
        injector.wait_for_release_file(release_file)

    logger.info(f"故障注入已配置: type={fault_type}, delay={delay}s, "
                f"duration={duration}s, trigger={trigger_file}, release={release_file}")
    return injector


if __name__ == "__main__":
    # 命令行测试
    import argparse
    parser = argparse.ArgumentParser(description="SFS Turbo IO 故障注入器")
    parser.add_argument("--type", required=True, choices=[
        "read_hang", "write_hang", "checkpoint_hang", "dataloader_hang", "full_io_hang"
    ])
    parser.add_argument("--path", default="/mnt/sfs-turbo")
    parser.add_argument("--duration", type=float, default=None, help="持续秒数（默认无限）")
    parser.add_argument("--delay", type=float, default=0, help="延时秒数")
    args = parser.parse_args()

    inj = IOHangInjector(sfs_mount_path=args.path)
    if args.delay > 0:
        inj.schedule_hang(args.delay, args.type, duration=args.duration)
    else:
        inj._dispatch_fault(args.type, duration=args.duration)

    logger.info(f"故障已注入，按 Ctrl+C 释放...")
    try:
        inj.wait_for_threads()
    except KeyboardInterrupt:
        inj.release()
