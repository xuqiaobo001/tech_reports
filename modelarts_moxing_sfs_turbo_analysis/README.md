# ModelArts 服务分析：Moxing 组件与 SFS-Turbo 的关系

> 分析日期：2026-05-22

## 一、各组件定位

| 组件 | 定位 | 说明 |
|------|------|------|
| **ModelArts** | 华为云一站式 AI 开发平台 | 提供数据标注、训练、部署全流程能力，面向 AI 开发者 |
| **Moxing（模兴）** | ModelArts 内置分布式训练加速框架 | 封装 TensorFlow/PyTorch/MindSpore 等引擎的分布式复杂性，提供 `mox.file` 统一文件操作 API |
| **SFS-Turbo** | 高性能弹性共享文件存储 | 亚毫秒级延迟、千万级 IOPS、百 GB 带宽，专为 AI 训练场景优化 |

## 二、三者关系架构

```
┌──────────────────────────────────────────────────────────┐
│                    ModelArts 平台                          │
│                                                            │
│  ┌──────────────┐                    ┌──────────────────┐  │
│  │    Moxing    │   训练时通过        │    SFS-Turbo     │  │
│  │  (训练框架)   │◄──挂载路径读写───►│  (高速共享存储)    │  │
│  └──────┬───────┘                    └────────┬─────────┘  │
│         │                                      │           │
│    mox.file API                        NFS 挂载到容器       │
│   (统一文件操作)                        (平台自动完成)       │
│         │                                      │           │
│         └────────── OBS ←── 数据预热 ──────────┘           │
└──────────────────────────────────────────────────────────┘
```

### 核心关系总结

**Moxing 不直接操作 SFS-Turbo 的存储管理功能**，而是通过 ModelArts 平台将 SFS-Turbo 挂载到训练容器中，Moxing 训练代码以**本地文件路径**的方式访问 SFS-Turbo 上的数据。

| 维度 | 关系描述 |
|------|----------|
| **ModelArts ↔ SFS Turbo** | 通过**网络直通**将 SFS Turbo 挂载到 Notebook 和训练环境中，作为训练数据的高速存储后端 |
| **ModelArts ↔ Moxing** | Moxing 是 ModelArts 平台内置的分布式训练框架，用户在 ModelArts 中直接调用 Moxing API |
| **Moxing ↔ SFS Turbo** | Moxing 训练代码通过容器内挂载路径（本地路径方式）读写 SFS Turbo 上的数据和模型 |

## 三、Moxing 操作 SFS-Turbo 的方式

### 方式一：通过挂载路径直接访问（主要方式）

ModelArts 在创建训练作业时自动将 SFS-Turbo 挂载到容器内路径，Moxing 训练代码像读本地文件一样直接读取：

```python
# SFS-Turbo 已被 ModelArts 挂载到容器的 /mnt/data/ 目录
# 直接使用标准 Python 文件操作即可
import os

data_dir = "/mnt/data/"  # SFS-Turbo 挂载路径
for file in os.listdir(data_dir):
    with open(os.path.join(data_dir, file), 'r') as f:
        data = f.read()
```

### 方式二：通过 `mox.file` API 桥接 OBS 与 SFS-Turbo

`mox.file` 的核心设计是**统一本地文件系统和 OBS 的文件操作**。当 SFS-Turbo 已挂载为本地路径时，`mox.file` 可用于数据在 OBS 和 SFS-Turbo 之间的搬运：

```python
import moxing as mox

# 将 OBS 上的训练数据并行下载到 SFS-Turbo 挂载路径
mox.file.copy_parallel(
    'obs://bucket_name/training_data/',  # OBS 源
    '/mnt/data/training_data/'            # SFS-Turbo 挂载路径
)

# 一键切换：让所有 os.* 接口支持 obs:// 路径
mox.file.shift('os', 'mox')

# 之后 os.listdir、open 等可以同时操作本地/SFS-Turbo 路径和 OBS 路径
```

### 方式三：典型的训练加速流水线

```python
import moxing as mox

# 1. 数据预热：OBS → SFS-Turbo（利用 mox.file 并行拷贝加速）
mox.file.copy_parallel('obs://my-bucket/dataset/', '/mnt/sfs/dataset/')

# 2. 训练：从 SFS-Turbo 高速读取（千万级 IOPS）
#    所有 Moxing 分布式训练 worker 共享同一 SFS-Turbo
train_data = load_dataset('/mnt/sfs/dataset/')

# 3. Checkpoint 保存到 SFS-Turbo（亚毫秒级延迟，秒级保存大模型）
model.save('/mnt/sfs/checkpoints/model_epoch_10.ckpt')

# 4. 训练结束后持久化到 OBS
mox.file.copy_parallel('/mnt/sfs/checkpoints/', 'obs://my-bucket/checkpoints/')
```

## 四、`mox.file` API 详解

### 4.1 一键切换模式

```python
import moxing as mox

# 将 os 模块的操作重定向到 mox，使所有 os.* 接口支持 obs:// 路径
mox.file.shift('os', 'mox')

# 之后可以使用标准 os 操作访问 OBS
import os
print(os.listdir('obs://bucket_name'))
with open('obs://bucket_name/hello.txt') as f:
    print(f.read())
```

### 4.2 API 对照表

| Python 标准库 | mox.file | 说明 |
|--------------|----------|------|
| `glob.glob` | `mox.file.glob` | 全局模式匹配 |
| `os.listdir` | `mox.file.list_directory` | 列举目录 |
| `os.makedirs` | `mox.file.make_dirs` | 递归创建目录 |
| `os.path.exists` | `mox.file.exists` | 判断路径是否存在 |
| `os.path.isdir` | `mox.file.is_directory` | 判断是否为目录 |
| `os.remove` | `mox.file.remove` | 删除文件/目录 |
| `os.rename` | `mox.file.rename` | 移动/重命名 |
| `os.stat` | `mox.file.stat` | 获取文件元信息 |
| `os.walk` | `mox.file.walk` | 递归遍历目录 |
| `open` | `mox.file.File` | 打开文件 |
| `shutil.copyfile` | `mox.file.copy` | 复制单个文件 |
| `shutil.copytree` | `mox.file.copy_parallel` | 并行复制文件夹 |

### 4.3 常用操作示例

#### 文件读写

```python
import moxing as mox

# 读取文件
content = mox.file.read('obs://bucket_name/file.txt')

# 写入文件
mox.file.write('obs://bucket_name/file.txt', 'Hello World!')

# 使用文件对象（推荐 with 语句）
with mox.file.File('obs://bucket_name/file.txt', 'r') as f:
    lines = f.readlines()
```

#### 文件/文件夹复制

```python
import moxing as mox

# 单文件复制（支持 obs↔obs, obs↔本地, 本地↔本地）
mox.file.copy('obs://bucket/a.txt', '/mnt/data/a.txt')

# 文件夹并行复制（高性能，默认 16 线程并发）
mox.file.copy_parallel('obs://bucket/dataset/', '/mnt/data/dataset/')

# 指定并发数
mox.file.copy_parallel('obs://bucket/dataset/', '/mnt/data/dataset/', threads=32)

# 只复制指定文件
mox.file.copy_parallel('/tmp/dir/', 'obs://bucket/dir/',
                        file_list=['train/1.jpg', 'eval/2.jpg'])
```

#### 大文件分片下载参数（环境变量）

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MOX_FILE_PARTIAL_MAXIMUM_SIZE` | 大文件判断阈值（超过则启用分片并发） | 5GB |
| `MOX_FILE_LARGE_FILE_PART_SIZE` | 分片大小 | 10MB |
| `MOX_FILE_LARGE_FILE_TASK_NUM` | 下载并发线程数 | 32 |

#### 与 Pandas 配合使用

```python
import pandas as pd
import moxing as mox

# 读取 OBS 上的 CSV
with mox.file.File("obs://bucket/data.csv", "r") as f:
    df = pd.read_csv(f)

# 写入 CSV 到 OBS
with mox.file.File("obs://bucket/output.csv", "w") as f:
    df.to_csv(f)
```

### 4.4 认证配置

在 ModelArts 环境中认证信息自动配置。其他环境需手动设置：

```python
import moxing as mox

mox.file.set_auth(
    ak='your_access_key',
    sk='your_secret_key',
    server='obs.cn-north-4.myhuaweicloud.com',
    is_secure=True
)
```

## 五、协作流程总览

```
                    ┌─────────────┐
                    │  OBS 存储    │
                    │ (持久化数据)  │
                    └──────┬──────┘
                           │ ① 数据预热
                    mox.file.copy_parallel
                           │
                           ▼
                    ┌──────────────┐
                    │  SFS-Turbo   │
                    │ (高速缓存)    │◄── ② ModelArts 挂载到训练容器
                    └──────┬───────┘
                           │ ③ 本地路径高速读取
                           ▼
                    ┌──────────────┐
                    │   Moxing     │
                    │ (分布式训练)   │
                    └──────┬───────┘
                           │ ④ Checkpoint 秒级保存
                           ▼
                    ┌──────────────┐
                    │  SFS-Turbo   │
                    │ (模型存储)    │
                    └──────┬───────┘
                           │ ⑤ 持久化回 OBS
                    mox.file.copy_parallel
                           │
                           ▼
                    ┌─────────────┐
                    │  OBS 存储    │
                    │ (长期保存)    │
                    └─────────────┘
```

## 六、关键约束与注意事项

| 约束项 | 说明 |
|--------|------|
| **网络直通前置** | 必须先在 ModelArts 控制台配置与 SFS-Turbo 的网络直通，否则无法挂载 |
| **挂载上限** | 每个训练作业最多挂载 **5 个** SFS-Turbo 文件系统，挂载路径不可重复 |
| **mox.file 定位** | 主要解决 OBS ↔ 本地（含 SFS-Turbo 挂载路径）的数据搬运，不是直接操作 SFS-Turbo 的 SDK |
| **SFS-Turbo 管理** | 存储管理（创建/扩容/权限配置）通过华为云控制台或 API 操作，不通过 Moxing |
| **OBS 兼容性** | 对于 OBS 并行文件系统部分接口可能存在兼容性问题，建议生产环境直接使用 OBS Python SDK |
| **大文件追加** | OBS 文件超过 5MB 时追加性能较低，建议使用写入模式 |
| **容器隔离** | 训练作业运行在容器中，`/cache` 目录是安全的临时存储空间 |

## 七、总结

**Moxing 与 SFS-Turbo 是互补关系而非直接操作关系**：

1. **SFS-Turbo** 解决的是**存储性能**问题（高速 I/O、共享访问、亚毫秒延迟）
2. **Moxing** 解决的是**分布式训练编程复杂度**问题（自动分布式、数据并行、统一文件 API）
3. **ModelArts** 作为平台层将两者串联：负责将 SFS-Turbo 挂载到训练容器，Moxing 代码以本地路径方式直接享受 SFS-Turbo 的高速存储
4. `mox.file` API 主要用于 **OBS 与 SFS-Turbo 之间的数据搬运**（预热/持久化），而非直接管理 SFS-Turbo

---

## 参考资料

| 资源 | 链接 |
|------|------|
| MoXing 功能介绍（官方文档） | https://support.huaweicloud.com/usermanual-standard-modelarts/develop-moxing-0002.html |
| MoXing API File（GitHub） | https://github.com/huaweicloud/ModelArts-Lab/blob/master/docs/moxing_api_doc/MoXing_API_File.md |
| 配置 ModelArts 和 SFS Turbo 网络直通 | https://support.huaweicloud.com/intl/zh-cn/bestpractice-sfsturbo/sfsturbo_03_0025.html |
| SFS Turbo 创建训练任务最佳实践 | https://support.huaweicloud.com/bestpractice-sfsturbo/sfsturbo_03_0031.html |
| 模型训练存储加速 | https://support.huaweicloud.com/usermanual-standard-modelarts/develop-modelarts-1421.html |
| OBS+SFS Turbo 存储加速方案 | https://www.huaweicloud.com/solution/implementations/accelerating-ai-training.html |
| MoXing API 完整文档（GitHub） | https://github.com/huaweicloud/ModelArts-Lab/blob/master/docs/moxing_api_doc/obsolete/MoXing_API.md |
