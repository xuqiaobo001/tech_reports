# 视频生成大模型全景调研报告

> 覆盖主流视频生成大模型的架构、参数量、训练框架（预训练/强化学习/SFT/LoRA）、使用场景及常见故障分析。

---

## 一、主流视频生成大模型概览

### 1.1 开源模型

| 模型 | 开发方 | 参数量 | 架构 | 支持任务 | 开源协议 |
|------|--------|--------|------|----------|----------|
| **Wan2.1** | 阿里巴巴 | 1.3B / 14B | DiT + Flow Matching | T2V, I2V | Apache 2.0 |
| **HunyuanVideo** | 腾讯 | 13B | DiT + 3D Full Attention + VAE | T2V | Apache 2.0 |
| **CogVideoX** | 清华/智谱 | 2B / 5B | Expert Transformer + 3D Causal VAE | T2V, I2V | Apache 2.0 |
| **Open-Sora 2.0** | HPC-AI Tech | 700M / 1.1B / 11B | STDiT + Flow Matching | T2V | Apache 2.0 |
| **LTX-Video** | Lightricks | ~3B | DiT | T2V, I2V | Apache 2.0 |
| **CogVideoX 1.5** | 清华/智谱 | 5B | Expert Transformer | T2V, I2V | Apache 2.0 |
| **Cosmos** | NVIDIA | 多尺寸 | DiT | T2V, I2V | NVIDIA License |

### 1.2 闭源/商业模型

| 模型 | 开发方 | 估计参数量 | 核心能力 | API 访问 |
|------|--------|-----------|----------|---------|
| **Sora / Sora 2** | OpenAI | 未公开（推测 >20B） | 60s+ 高质量视频，强语义理解 | ChatGPT Plus/Pro |
| **Veo 2 / Veo 3** | Google DeepMind | 未公开 | 电影级画质，4K 分辨率 | Google AI Studio |
| **Kling 1.6 / 2.0** | 快手 | 未公开 | 强运动一致性，长视频 | API / Web |
| **Runway Gen-3/Gen-4** | Runway | 未公开 | 专业视频编辑，多风格 | API / Web |
| **Luma Ray2/3** | Luma AI | 未公开 | 3D 一致性，创意灵活 | API / Web |
| **Seedance 1.0** | 字节跳动 | 未公开 | 高质量舞蹈/人物动作 | 火山引擎 API |

### 1.3 架构统一趋势

```
当前主流视频生成模型架构 = Diffusion Transformer (DiT) 变体

┌─────────────────────────────────────────────────┐
│                 统一架构范式                       │
│                                                   │
│  ┌─────────┐    ┌──────────┐    ┌──────────┐    │
│  │ Text     │    │ DiT      │    │ VAE/     │    │
│  │ Encoder  │ →  │ Backbone │ →  │ Decoder  │    │
│  │ (CLIP/   │    │ (3D Attn)│    │ (3D      │    │
│  │  T5/LLM) │    │          │    │  Causal) │    │
│  └─────────┘    └──────────┘    └──────────┘    │
│       ↓              ↓               ↓           │
│  文本特征注入   时空联合建模      潜空间→像素重建   │
│  Cross-Attn     Flow Matching     视频解码        │
│  /AdaLN        /DDPM Schedule                     │
└─────────────────────────────────────────────────┘
```

---

## 二、训练框架与方法详解

### 2.1 预训练（Pre-training）

所有主流模型都采用 **多阶段预训练** 策略：

```
Stage 1: 图像预训练（Text-to-Image）
    ↓ 复用成熟的 T2I 数据和模型
Stage 2: 视频预训练（Text-to-Video，低分辨率）
    ↓ 大规模视频数据
Stage 3: 高质量视频微调（高分辨率、长视频）
    ↓ 精选高质量数据
Stage 4: 偏好对齐（RLHF/DPO）
```

| 模型 | 预训练框架 | 分布式策略 | 训练成本 |
|------|-----------|-----------|----------|
| **Wan2.1** | 内部框架 + DeepSpeed | FSDP + Sequence Parallel | 未公开 |
| **HunyuanVideo** | 自研框架 | ColossalAI 风格分布式 | 大规模 |
| **CogVideoX** | 自研框架 + DeepSpeed | FSDP | 未公开 |
| **Open-Sora 2.0** | **ColossalAI** | Pipeline Parallel + Tensor Parallel | **$200K** |

**ColossalAI（Open-Sora 选用）**：
```bash
# Open-Sora 预训练示例
python -m colossalai bootstrap_launch --nproc_per_node 8 \
    scripts/train.py --config configs/opensora-v2-11b.py
```

**DeepSpeed + FSDP（通用）**：
```bash
# 典型 DeepSpeed FSDP 训练启动
deepspeed --num_gpus 8 train.py \
    --deepspeed_config ds_config.json \
    --model_name "Wan2.1-14B" \
    --batch_size 1 \
    --gradient_checkpointing
```

### 2.2 监督微调（SFT）

SFT 阶段使用高质量的"文本-视频"对数据，让模型学习特定风格的视频生成。

**典型做法**：
```python
# 使用 HuggingFace Diffusers 进行 SFT
from diffusers import CogVideoXPipeline
from diffusers.training_utils import EMAModel

pipeline = CogVideoXPipeline.from_pretrained("THUDM/CogVideoX-5b")
# 加载高质量视频数据集进行微调
trainer = VideoTrainer(
    model=pipeline,
    train_dataset=custom_video_dataset,
    args=TrainingArguments(
        learning_rate=1e-5,
        max_train_steps=5000,
        gradient_checkpointing=True,
        mixed_precision="bf16",
    ),
)
trainer.train()
```

**各框架 SFT 支持**：

| 框架 | SFT 支持 | 说明 |
|------|---------|------|
| HuggingFace Diffusers | ✅ | 官方训练脚本，支持 full fine-tuning |
| VideoX-Fun | ✅ | 阿里巴巴出品，结构化训练管线 |
| DiffSynth-Studio | ✅ | 支持 ExVideo 扩展，长视频 SFT |
| Diffusion-Pipe | ✅ | Pipeline Parallel 加速 |

### 2.3 LoRA 微调

LoRA（Low-Rank Adaptation）是视频生成模型微调的**主流方法**，仅训练少量参数即可适配新风格/场景。

#### VideoX-Fun（阿里巴巴）

```bash
# Wan2.1 LoRA 训练
python scripts/wan2.1/train_lora.py \
    --pretrained_model_name_or_path "Wan-AI/Wan2.1-T2V-14B" \
    --output_dir "./lora_output" \
    --dataset_name "my_video_dataset" \
    --rank 64 \
    --lora_alpha 32 \
    --gradient_checkpointing \
    --mixed_precision bf16 \
    --train_batch_size 1 \
    --max_train_steps 2000

# 推理使用 LoRA
python scripts/wan2.1/inference_lora.py \
    --base_model "Wan-AI/Wan2.1-T2V-14B" \
    --lora_path "./lora_output" \
    --prompt "A cat playing piano"
```

#### Diffusion-Pipe（高效 GPU 利用）

```bash
# HunyuanVideo LoRA 训练（支持 fp8）
python train.py \
    --model hunyuan_video \
    --dataset.path /path/to/videos \
    --lora.rank 64 \
    --lora.alpha 32 \
    --fp8_transformer \
    --gradient_checkpointing

# Wan2.1 I2V LoRA 训练
python train.py \
    --model wan2.1_i2v \
    --dataset.path /path/to/videos \
    --lora.rank 32
```

#### DiffSynth-Studio（ComfyUI 集成）

```bash
# CogVideoX LoRA 训练
python train.py \
    --model CogVideoX-5B \
    --data_path ./training_data \
    --output_path ./output_lora \
    --lora_rank 64 \
    --max_frames 49
```

**LoRA 框架选择指南**：

| 框架 | 适合模型 | GPU 需求 | 特点 |
|------|---------|----------|------|
| **VideoX-Fun** | Wan2.1, CogVideoX | 24GB+ | 结构化管线，文档完善 |
| **Diffusion-Pipe** | HunyuanVideo, Wan2.1, LTX-Video | 16GB+(fp8) | Pipeline Parallel，fp8 支持 |
| **DiffSynth-Studio** | CogVideoX, Wan2.1 | 24GB+ | ComfyUI 集成，长视频 |
| **Finetrainers** | CogVideoX, HunyuanVideo, Wan2.1 | 24GB+ | Diffusers 原生，多模型 |
| **AI Toolkit** | Wan2.1 | 24GB+ | Web UI，易上手 |

### 2.4 强化学习（RL / RLHF）

视频生成模型的 RL 训练主要用于**偏好对齐**，提升视频质量和文本一致性。

| 模型 | RL 方法 | 奖励模型 | 说明 |
|------|--------|---------|------|
| **HunyuanVideo** | RLHF | 学习的视频质量奖励模型 | 使用人类偏好数据训练奖励模型 |
| **CogVideoX** | DPO | 偏好对比 | Direct Preference Optimization |
| **Open-Sora 2.0** | 偏好对齐 | 质量评估模型 | 数据筛选 + 偏好学习 |
| **Wan2.1** | 未公开 | 未公开 | 阿里内部训练流程 |

**RL 训练通用流程**：
```
1. 收集人类偏好数据（好视频 vs 差视频配对）
2. 训练奖励模型（Reward Model）
3. 使用 PPO/DPO 对生成模型进行强化学习优化
4. 目标：生成更符合人类审美的视频
```

**相关开源资源**：
- [Awesome-RL-for-Video-Generation](https://github.com/wendell0218/Awesome-RL-for-Video-Generation) — 视频生成 RL 研究汇总

---

## 三、模型参数量与使用场景

### 3.1 参数量对比

```
参数量 (Billion)
  |
20 ┤
   |       ■ Sora (推测>20B)
15 ┤
   |  ■ HunyuanVideo (13B)    ■ Wan2.1 (14B)
10 ┤
   |              ■ Open-Sora 2.0 (11B)
 5 ┤   ■ CogVideoX (5B)
   |
 3 ┤              ■ LTX-Video (~3B)
   |   ■ CogVideoX (2B)
 1 ┤  ■ Wan2.1 (1.3B)   ■ Open-Sora (700M/1.1B)
   |
 0 ┼───┴──────┴──────┴──────┴──────┴──────┴─────
```

### 3.2 使用场景分析

| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| **广告/营销视频** | Kling, Runway, Veo | 高画质，商业授权 |
| **影视预览/分镜** | Sora 2, Veo 3 | 电影级画质，长视频 |
| **社交媒体内容** | Wan2.1 (1.3B), Kling | 快速生成，低门槛 |
| **游戏资产生成** | HunyuanVideo, Cosmos | 高一致性，3D 场景 |
| **AI 数字人/虚拟主播** | Wan2.1 I2V, Kling | 人物动作自然 |
| **教育培训视频** | CogVideoX (5B), Wan2.1 | 文本理解强，准确性高 |
| **科学研究/实验** | Open-Sora, CogVideoX | 完全开源，可复现 |
| **风格化创作** | LoRA 微调任何模型 | 定制化风格适配 |
| **实时视频生成** | Wan2.1 (1.3B), LTX-Video | 参数少，推理快 |
| **长视频（>30s）** | Sora, Veo, DiffSynth-Studio | 长视频能力 |

### 3.3 硬件需求参考

| 模型参数量 | 推理 GPU 显存 | LoRA 训练 GPU 显存 | Full Fine-tune GPU 显存 |
|-----------|--------------|-------------------|----------------------|
| 1.3B | 6-8 GB | 16-24 GB | 40-80 GB (单卡) |
| 2-5B | 12-24 GB | 24-40 GB | 80-160 GB (多卡) |
| 11-14B | 24-40 GB | 40-80 GB (fp8) | 320+ GB (多节点) |
| 20B+ | 40-80 GB | 80+ GB (fp8) | 大规模集群 |

---

## 四、训练过程中的常见问题与故障

### 4.1 GPU 显存问题（OOM）

**最常见的问题**，视频模型比图像模型消耗更多显存（视频 = 多帧图像序列）。

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| CUDA Out of Memory | 模型 + 优化器 + 激活值超过显存 | 减小 batch_size，使用 gradient_checkpointing |
| OOM 在第 2 个 batch | 第 1 batch 后梯度未释放 | 检查梯度累积设置，使用 `torch.cuda.empty_cache()` |
| LoRA 训练 OOM | 视频分辨率/帧数过高 | 降低分辨率，减少帧数，使用 fp8 训练 |
| 推理时 OOM | 视频 token 序列过长 | 减少生成帧数，使用 KV cache 优化 |

**典型修复代码**：
```python
# Gradient Checkpointing
model.enable_gradient_checkpointing()

# Mixed Precision Training
from accelerate import Accelerator
accelerator = Accelerator(mixed_precision="bf16")

# fp8 Transformer (Diffusion-Pipe)
python train.py --fp8_transformer --gradient_checkpointing
```

### 4.2 训练 Loss 异常

| 问题 | 症状 | 原因 | 解决方案 |
|------|------|------|---------|
| Loss NaN | loss 突然变为 NaN | 梯度爆炸、精度溢出、CUDA bug | 降低学习率（1e-5→5e-6），检查 bf16/fp16 精度 |
| Loss 不下降 | loss 长期平台 | 学习率过小/过大，数据问题 | 调整学习率，检查数据质量 |
| Loss 震荡 | loss 大幅波动 | batch size 过小，数据噪声 | 增大 batch size（gradient accumulation），清洗数据 |
| Loss 突然跳升 | loss spike | 学习率调度问题，坏数据 | 检查数据集中损坏样本，使用 warmup |

**Loss NaN 排查流程**：
```
Loss NaN
  ├─ 检查输入数据是否有 NaN/Inf
  │   → torch.isnan(input).any()
  ├─ 检查梯度是否正常
  │   → torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
  ├─ 检查学习率是否过大
  │   → 降低到 1e-5 或更低
  ├─ 检查 CUDA 版本
  │   → 升级到最新 CUDA 12.x
  └─ 检查混合精度设置
      → 关键操作用 fp32，其余用 bf16
```

### 4.3 数据相关问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 生成的视频模糊 | 训练数据分辨率低/质量差 | 使用高分辨率、高质量数据集 |
| 文本与视频不匹配 | 文本描述不准确 | 改进文本标注质量，使用更强的 Text Encoder |
| 视频闪烁/不一致 | 时序一致性训练不足 | 增加时序注意力层的训练数据 |
| 颜色/风格异常 | 数据分布偏差 | 数据增强，平衡数据集 |
| 视频帧率异常 | 帧采样策略不当 | 统一帧率（如 24fps），合理采样 |

### 4.4 分布式训练问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| NCCL Timeout | GPU 间通信超时 | 增大 `NCCL_TIMEOUT`，检查网络 |
| 梯度不同步 | 各 GPU 数据/模型不一致 | 确保相同的随机种子和数据加载 |
| 检查点保存失败 | 磁盘空间不足 | 定期清理，使用分布式文件系统 |
| Pipeline Bubble | PP 空闲等待 | 调整 micro-batch 数，使用 interleaved PP |
| DeepSpeed 初始化失败 | 配置文件错误 | 检查 `ds_config.json` 中的参数 |

### 4.5 LoRA 微调特有问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| LoRA 训练后过拟合 | 数据量太少（<50 视频） | 增加数据量，降低 rank（64→32） |
| 风格遗忘 | LoRA 权重过大 | 降低 lora_alpha，使用正则化 |
| LoRA 无法加载 | 版本不匹配 | 确保训练和推理使用相同的框架版本 |
| 生成质量差 | LoRA rank 过低 | 增大 rank（16→32→64） |
| 训练时间过长 | 视频数据分辨率高 | 降低分辨率，减少帧数，使用 fp8 |

### 4.6 推理阶段问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 生成速度极慢 | DiT 推理步数过多 | 减少推理步数（50→20），使用一致性模型 |
| 视频质量差 | CFG Scale 设置不当 | 调整 guidance_scale（通常 5-10） |
| 显存溢出（推理） | 长视频 token 数量过大 | 分块生成，使用 KV cache 优化 |
| 多卡推理不一致 | DP 推理各卡输入不同 | 确保相同的 prompt 和 seed |

---

## 五、框架选择推荐

### 5.1 按任务选择

```
你要做什么？
│
├─ 从零预训练视频模型
│   └─ ColossalAI (Open-Sora 方案)
│
├─ Full Fine-tuning（SFT）
│   ├─ HuggingFace Diffusers + DeepSpeed
│   └─ VideoX-Fun
│
├─ LoRA 微调
│   ├─ Wan2.1 → VideoX-Fun 或 Diffusion-Pipe
│   ├─ HunyuanVideo → Diffusion-Pipe
│   ├─ CogVideoX → VideoX-Fun 或 DiffSynth-Studio
│   └─ 多模型 → Finetrainers 或 Diffusion-Pipe
│
├─ RLHF / 偏好对齐
│   └─ TRL (HuggingFace) + 自定义奖励模型
│
└─ 快速推理
    ├─ ComfyUI + DiffSynth-Studio
    └─ 模型原生推理脚本
```

### 5.2 按硬件选择

| GPU 显存 | 推荐方案 |
|----------|---------|
| 16-24 GB (4090等) | Wan2.1-1.3B LoRA / fp8 训练 |
| 40-48 GB (A6000等) | Wan2.1-14B LoRA (fp8) / CogVideoX-5B LoRA |
| 80 GB (A100/H100) | 全部模型 LoRA / 5B 以下 full fine-tune |
| 多卡 (4×A100+) | 全部模型 full fine-tune / 预训练 |

---

## 六、总结

| 维度 | 现状 |
|------|------|
| **架构** | DiT (Diffusion Transformer) 已成为视频生成的事实标准 |
| **训练** | 多阶段预训练 + LoRA 微调是主流范式，RLHF 用于偏好对齐 |
| **参数量** | 开源模型覆盖 1.3B-14B，闭源模型估计 >20B |
| **训练成本** | Open-Sora 2.0 证明 $200K 可训练商业级 11B 模型 |
| **微调生态** | VideoX-Fun / Diffusion-Pipe / DiffSynth-Studio 三大框架 |
| **最大挑战** | GPU 显存（OOM）、训练稳定性（Loss NaN）、数据质量 |

---

## 参考资源

### 模型仓库
- [Wan2.1](https://github.com/Wan-Video/Wan2.1)
- [HunyuanVideo](https://github.com/Tencent-Hunyuan/HunyuanVideo)
- [CogVideoX](https://github.com/THUDM/CogVideo)
- [Open-Sora](https://github.com/hpcaitech/Open-sora)

### 训练框架
- [VideoX-Fun](https://github.com/aigc-apps/VideoX-Fun) — 阿里巴巴视频生成训练管线
- [Diffusion-Pipe](https://github.com/tdrussell/diffusion-pipe) — Pipeline Parallel LoRA 训练
- [DiffSynth-Studio](https://github.com/modelscope/diffsynth-studio) — 长视频生成 + LoRA
- [Finetrainers](https://github.com/a-r-r-o-w/finetrainers) — Diffusers 原生多模型 LoRA
- [ColossalAI](https://github.com/hpcaitech/ColossalAI) — 分布式预训练框架

### 技术报告
- [Open-Sora 2.0 Technical Report (arXiv:2503.09642)](https://arxiv.org/html/2503.09642v1)
- [HunyuanVideo Technical Report (arXiv:2412.03603)](https://arxiv.org/abs/2412.03603)
- [CogVideoX (ICLR 2025)](https://openreview.net/forum?id=LQzN6TRFg9)
- [Awesome-RL-for-Video-Generation](https://github.com/wendell0218/Awesome-RL-for-Video-Generation)

### 对比分析
- [AI Video Models Guide 2025](https://ulazai.com/ai-video-models-guide-2025/)
- [Best AI Video Generators 2026](https://aimlapi.com/blog/best-ai-video-generators-2026-veo-3-1-kling-sora-2-seedance-more-compared)
- [Luma Video Models Comparison](https://lumalabs.ai/learning-hub/luma-video-models-guide-ray3.14-veo-sora-kling-compared)
