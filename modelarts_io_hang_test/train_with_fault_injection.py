"""
ModelArts SFS Turbo IO 卡死故障注入测试 — PyTorch 训练脚本

在 ModelArts 训练作业中使用，测试 IO 卡死场景下 ModelArts 的故障恢复能力。

启动命令示例：
    # 场景1：训练 30 秒后注入读 IO 卡死（无限）
    python train_with_fault_injection.py \
        --data-dir /mnt/sfs-turbo/data \
        --checkpoint-dir /mnt/sfs-turbo/checkpoints \
        --fault-type read_hang \
        --fault-delay 30

    # 场景2：通过触发文件控制（外部控制）
    python train_with_fault_injection.py \
        --data-dir /mnt/sfs-turbo/data \
        --checkpoint-dir /mnt/sfs-turbo/checkpoints \
        --fault-type full_io_hang \
        --fault-trigger-file /mnt/sfs-turbo/.fault_trigger

    # 场景3：环境变量配置（推荐用于 ModelArts 训练作业）
    export IO_FAULT_TYPE=checkpoint_hang
    export IO_FAULT_DELAY=60
    export IO_FAULT_SFS_PATH=/mnt/sfs-turbo
    python train_with_fault_injection.py \
        --data-dir /mnt/sfs-turbo/data \
        --checkpoint-dir /mnt/sfs-turbo/checkpoints
"""

import os
import sys
import time
import argparse
import logging

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, Dataset, random_split

logging.basicConfig(
    level=logging.INFO,
    format="[Train] %(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("Train")


# ----------------------------------------------------------------
# 简单的全连接模型（用于演示，不依赖外部数据集）
# ----------------------------------------------------------------

class SimpleNet(nn.Module):
    """用于故障注入测试的简单全连接网络"""

    def __init__(self, input_dim=784, hidden_dim=256, num_classes=10):
        super().__init__()
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.relu = nn.ReLU()
        self.fc2 = nn.Linear(hidden_dim, num_classes)

    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = self.relu(self.fc1(x))
        x = self.fc2(x)
        return x


class RandomDataset(Dataset):
    """
    随机数据集（不依赖外部数据文件，确保能快速启动训练）。
    如果 data_dir 下有 .pt 文件，则加载真实数据。
    """

    def __init__(self, data_dir: str = None, num_samples: int = 1000, input_dim: int = 784):
        self.num_samples = num_samples
        self.input_dim = input_dim

        # 尝试从 data_dir 加载数据
        if data_dir and os.path.isdir(data_dir):
            pt_files = [f for f in os.listdir(data_dir) if f.endswith(".pt")]
            if pt_files:
                logger.info(f"从 {data_dir} 加载 {len(pt_files)} 个 .pt 文件")
                data_list = []
                label_list = []
                for f in sorted(pt_files):
                    loaded = torch.load(os.path.join(data_dir, f))
                    if isinstance(loaded, dict):
                        data_list.append(loaded.get("data", loaded.get("x")))
                        label_list.append(loaded.get("label", loaded.get("y")))
                if data_list:
                    self.data = torch.cat(data_list)
                    self.labels = torch.cat(label_list)
                    self.num_samples = len(self.labels)
                    return

        # 使用随机数据
        logger.info(f"使用随机数据集: {num_samples} 样本, input_dim={input_dim}")
        self.data = torch.randn(num_samples, input_dim)
        self.labels = torch.randint(0, 10, (num_samples,))

    def __len__(self):
        return self.num_samples

    def __getitem__(self, idx):
        return self.data[idx], self.labels[idx]


# ----------------------------------------------------------------
# 训练主逻辑
# ----------------------------------------------------------------

def train_one_epoch(model, loader, criterion, optimizer, device, epoch):
    model.train()
    total_loss = 0
    correct = 0
    total = 0

    for batch_idx, (data, target) in enumerate(loader):
        data, target = data.to(device), target.to(device)

        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        pred = output.argmax(dim=1)
        correct += pred.eq(target).sum().item()
        total += len(target)

        if batch_idx % 20 == 0:
            logger.info(
                f"Epoch {epoch} | Batch {batch_idx}/{len(loader)} | "
                f"Loss: {loss.item():.4f} | Acc: {correct/total:.4f}"
            )

    avg_loss = total_loss / len(loader)
    accuracy = correct / total
    return avg_loss, accuracy


def save_checkpoint(model, optimizer, epoch, loss, path):
    """保存 checkpoint 到 SFS Turbo"""
    checkpoint = {
        "epoch": epoch,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "loss": loss,
        "timestamp": time.time(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    torch.save(checkpoint, path)
    logger.info(f"Checkpoint 已保存: {path}")


def load_checkpoint(path, model, optimizer=None):
    """从 SFS Turbo 加载 checkpoint"""
    if not os.path.exists(path):
        logger.info(f"Checkpoint 不存在，从头开始训练: {path}")
        return 0

    logger.info(f"加载 checkpoint: {path}")
    checkpoint = torch.load(path)
    model.load_state_dict(checkpoint["model_state_dict"])
    if optimizer and "optimizer_state_dict" in checkpoint:
        optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
    start_epoch = checkpoint.get("epoch", 0) + 1
    logger.info(f"从 epoch {start_epoch} 恢复训练")
    return start_epoch


def main():
    parser = argparse.ArgumentParser(description="IO 故障注入测试训练脚本")
    parser.add_argument("--data-dir", default="/mnt/sfs-turbo/data",
                        help="数据目录（SFS Turbo 挂载路径）")
    parser.add_argument("--checkpoint-dir", default="/mnt/sfs-turbo/checkpoints",
                        help="Checkpoint 保存目录")
    parser.add_argument("--epochs", type=int, default=10, help="训练轮数")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lr", type=float, default=0.001)
    parser.add_argument("--save-interval", type=int, default=1, help="每 N 个 epoch 保存一次 checkpoint")

    # 故障注入参数
    parser.add_argument("--fault-type", default=os.environ.get("IO_FAULT_TYPE", ""),
                        choices=["", "read_hang", "write_hang", "checkpoint_hang",
                                 "dataloader_hang", "full_io_hang"],
                        help="故障类型（空=不注入）")
    parser.add_argument("--fault-delay", type=float,
                        default=float(os.environ.get("IO_FAULT_DELAY", "0")),
                        help="训练开始后多少秒注入故障")
    parser.add_argument("--fault-duration", type=float,
                        default=float(os.environ.get("IO_FAULT_DURATION", "0")) or None,
                        help="故障持续秒数（0 或不设置=无限）")
    parser.add_argument("--fault-trigger-file", default=os.environ.get("IO_FAULT_TRIGGER_FILE", ""),
                        help="触发文件路径（文件出现时注入故障）")
    parser.add_argument("--fault-release-file", default=os.environ.get("IO_FAULT_RELEASE_FILE", ""),
                        help="释放文件路径（文件出现时解除故障）")

    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info(f"使用设备: {device}")

    # 创建数据目录（写入测试数据到 SFS Turbo）
    os.makedirs(args.data_dir, exist_ok=True)
    os.makedirs(args.checkpoint_dir, exist_ok=True)

    # 准备数据集
    dataset = RandomDataset(data_dir=args.data_dir)
    train_size = int(0.8 * len(dataset))
    val_size = len(dataset) - train_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])

    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True,
                              num_workers=2, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=args.batch_size, num_workers=2)

    # 创建模型
    model = SimpleNet().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=args.lr)

    # 尝试加载 checkpoint（断点续训）
    checkpoint_path = os.path.join(args.checkpoint_dir, "latest.pt")
    start_epoch = load_checkpoint(checkpoint_path, model, optimizer)

    # ----------------------------------------------------------------
    # 故障注入配置
    # ----------------------------------------------------------------
    injector = None
    if args.fault_type:
        from io_fault_injector import IOHangInjector

        # 推断 SFS 挂载路径（从 data_dir 或 checkpoint_dir）
        sfs_path = args.data_dir
        injector = IOHangInjector(sfs_mount_path=sfs_path)

        duration = args.fault_duration

        if args.fault_trigger_file:
            # 等待触发文件
            injector.wait_for_trigger_file(
                args.fault_trigger_file,
                args.fault_type,
                duration=duration,
            )
            logger.info(f"已配置触发文件监听: {args.fault_trigger_file}")
        elif args.fault_delay > 0:
            # 延时触发
            injector.schedule_hang(args.fault_delay, args.fault_type, duration=duration)
            logger.info(f"已配置延时故障注入: {args.fault_delay}s 后注入 {args.fault_type}")
        else:
            # 立即触发
            injector._dispatch_fault(args.fault_type, duration=duration)
            logger.info(f"已立即注入故障: {args.fault_type}")

        if args.fault_release_file:
            injector.wait_for_release_file(args.fault_release_file)

    # ----------------------------------------------------------------
    # 训练循环
    # ----------------------------------------------------------------
    logger.info(f"开始训练: {args.epochs} epochs, start_epoch={start_epoch}")

    try:
        for epoch in range(start_epoch, args.epochs):
            # 训练一个 epoch
            train_loss, train_acc = train_one_epoch(
                model, train_loader, criterion, optimizer, device, epoch
            )
            logger.info(f"Epoch {epoch} 完成 | Loss: {train_loss:.4f} | Acc: {train_acc:.4f}")

            # 验证
            model.eval()
            val_loss = 0
            correct = 0
            total = 0
            with torch.no_grad():
                for data, target in val_loader:
                    data, target = data.to(device), target.to(device)
                    output = model(data)
                    val_loss += nn.functional.cross_entropy(output, target, reduction="sum").item()
                    pred = output.argmax(dim=1)
                    correct += pred.eq(target).sum().item()
                    total += len(target)

            val_loss /= max(total, 1)
            val_acc = correct / max(total, 1)
            logger.info(f"Epoch {epoch} 验证 | Loss: {val_loss:.4f} | Acc: {val_acc:.4f}")

            # 保存 checkpoint
            if (epoch + 1) % args.save_interval == 0:
                save_checkpoint(model, optimizer, epoch, train_loss, checkpoint_path)

            # 写入训练状态到 SFS Turbo（测试写 IO）
            status_path = os.path.join(args.checkpoint_dir, "training_status.txt")
            with open(status_path, "w") as f:
                f.write(f"epoch={epoch}, train_loss={train_loss:.4f}, "
                        f"train_acc={train_acc:.4f}, val_loss={val_loss:.4f}, "
                        f"val_acc={val_acc:.4f}, timestamp={time.time()}\n")

        logger.info("训练完成!")

    except KeyboardInterrupt:
        logger.info("训练被中断")
    except Exception as e:
        logger.error(f"训练异常: {e}")
        traceback.print_exc()
        raise
    finally:
        if injector:
            injector.release()
            logger.info("故障注入器已释放")


if __name__ == "__main__":
    import traceback
    main()
