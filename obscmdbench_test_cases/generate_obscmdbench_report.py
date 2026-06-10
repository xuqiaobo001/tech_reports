#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 obscmdbench 完整测试用例报告，每个用例包含完整可复制粘贴的 config.dat"""

def make_config(testcase, users, threads_per_user, object_size, objects_per_bucket,
                object_lexical, object_name_prefix, is_random_get=None,
                mix_operations=None, mix_loop_count=None, run_seconds=None,
                buckets_per_user=1):
    """生成完整的 config.dat 内容"""
    L = []
    L.append("#################test environment##################################################################")
    L.append("")
    L.append("# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。")
    L.append("# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2")
    L.append("OSCs = 127.0.0.1")
    L.append("")
    L.append("###################test case plan##################################################################")
    L.append("# 配置测试用例，用例对应操作见下。")
    L.append("Testcase = %s" % testcase)
    L.append("")
    L.append("# 用户数，1个用户对应users.dat中的一行用户信息")
    L.append("Users = %s" % users)
    L.append("")
    L.append("# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。")
    L.append("UserStartIndex = 0")
    L.append("")
    L.append("# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.")
    L.append("# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。")
    L.append("ThreadsPerUser = %s" % threads_per_user)
    L.append("")
    L.append("############# \"100=ListUserBuckets\" #############################################################")
    L.append("# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。")
    L.append("RequestsPerThread = 2000")
    L.append("")
    L.append("############# \"101=CreateBucket\" ###############################################################")
    L.append("# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。")
    L.append("BucketsPerUser = %s" % buckets_per_user)
    L.append("")
    L.append("# 创建时指定桶Location, 不能包含空格，空代表不指定。")
    L.append("BucketLocation =")
    L.append("")
    L.append("# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |")
    L.append("# bucket-owner-read | bucket-owner-full-control, 空不携带")
    L.append("CreateWithACL = public-read-write")
    L.append("")
    L.append("# 桶名中自定义标识")
    L.append("BucketNamePrefix = bucket.test")
    L.append("")
    L.append("# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER")
    L.append("StorageClass =")
    L.append("")
    L.append("# 是否创建文件网关桶")
    L.append("IsFileInterface = false")
    L.append("")
    L.append("############# \"102=ListObjectsInBucket\" #########################################################")
    L.append("# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效")
    L.append("Max-keys = 1000")
    L.append("")
    L.append("# 列举不带多版本。")
    L.append("prefix =")
    L.append("")
    L.append("############# \"111=PutBucketVersioning\" #########################################################")
    L.append("# 桶多版本状态，可选值Enabled | Suspended")
    L.append("VersionStatus = Enabled")
    L.append("")
    L.append("############# \"151=PutBucketCORS\" ############################################################")
    L.append("# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法")
    L.append("AllowedMethod = GET")
    L.append("")
    L.append("############# \"161=PutBucketTag\" ############################################################")
    L.append("# 配置key-value对数，默认1，最大10")
    L.append("KeyValueNumber = 10")
    L.append("")
    L.append("############# \"201=PutObject\" ##################################################################")
    L.append("# 上传的对象大小（字节）")
    L.append("# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值")
    L.append("ObjectSize = %s" % object_size)
    L.append("")
    L.append("# 每个并发在每个桶中上传的对象数")
    L.append("ObjectsPerBucketPerThread = %s" % objects_per_bucket)
    L.append("")
    L.append("# 每个对象名上传次数，多次上传覆盖。")
    L.append("PutTimesForOneObj = 1")
    L.append("")
    L.append("# 上传对象同时指定ACL")
    L.append("PutWithACL = public-read")
    L.append("")
    L.append("# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。")
    L.append("ObjectLexical = %s" % ("true" if object_lexical else "false"))
    L.append("")
    L.append("# 对象名前缀")
    L.append("ObjectNamePrefix = %s" % object_name_prefix)
    L.append("")
    L.append("# 对象名pattern, 字典序时有效")
    L.append("ObjectNamePartten = processID-ObjectNamePrefix-Index")
    L.append("")
    L.append("# 创建对象指定x-default-storage-class")
    L.append("ObjectStorageClass =")
    L.append("")
    L.append("# 设置x-obs-expires头域的值")
    L.append("Expires =")
    L.append("")
    L.append("# 以指定对象的内容作为上传对象的实际内容而不从内存中生成")
    L.append("IsDataFromFile = False")
    L.append("")
    L.append("# 指定对象路径")
    L.append("LocalFilePath =")
    L.append("")
    L.append("############# \"202=GetObject\" ##################################################################")
    L.append("# 按以下顺序查找对象处理：")
    L.append("# 1) 查看是否指定了上传时生成的detail文件")
    L.append("objectDesFile =")
    L.append("")
    L.append("# 指定Range下载对象，空表示不指定。")
    L.append("Range =")
    L.append("")
    L.append("# 是否随机获取开关")
    L.append("IsRandomGet = %s" % ("true" if is_random_get else "false"))
    L.append("")
    L.append("# cdn开关")
    L.append("IsCdn = false")
    L.append("CdnAK =")
    L.append("CdnSK =")
    L.append("CdnSTSToken =")
    L.append("")
    L.append("############# \"204=DeleteObject\" ################################################################")
    L.append("# 是否随机删除")
    L.append("IsRandomDelete = false")
    L.append("")
    L.append("############# \"205=DeleteMultiObjects\" #######################################################")
    L.append("# 一次请求删除的对象数，1~100个")
    L.append("DeleteObjectsPerRequest = 3")
    L.append("")
    L.append("############# \"206=CopyObject\" ##############################################################")
    L.append("copySrcObjFixed =")
    L.append("copyDstObjFixed =")
    L.append("copySrcSrvSideEncryptType = SSE-C")
    L.append("")
    L.append("############# \"207=RestoreObject\" ##############################################################")
    L.append("RestoreDays =")
    L.append("RestoreTier =")
    L.append("")
    L.append("############# \"208=AppendObject\" ##############################################################")
    L.append("GetPositionFromMeta = True")
    L.append("")
    L.append("############# \"209=ImageProcess\" ##############################################################")
    L.append("ImageManipulationType =")
    L.append("ImageFormat =")
    L.append("CropParams =")
    L.append("ResizeParams =")
    L.append("")
    L.append("############# \"211=InitMultiUpload\" ########################################################")
    L.append("MultiUploadStorageClass =")
    L.append("")
    L.append("############# \"212=UploadPart\" ############################################################")
    L.append("# 每个uploadID要上传的段数量[1~10000]")
    L.append("PartsForEachUploadID = 3")
    L.append("")
    L.append("# 针对每个uploadID是否并发上传段")
    L.append("ConcurrentUpParts = false")
    L.append("")
    L.append("# 上传的段大小,obs协议要求最小5M")
    L.append("PartSize = 5242880")
    L.append("")
    L.append("# 每个段上传次数")
    L.append("PutTimesForOnePart = 1")
    L.append("")
    L.append("############# \"900=MixOperation\" ########################################################")
    L.append("# 设置混合操作类型，可设置以上除900外的所有操作。")
    L.append("MixOperations = %s" % (mix_operations if mix_operations else "100,101,104,201,102,202,203,204,103"))
    L.append("")
    L.append("# 循环次数")
    L.append("MixLoopCount = %s" % (mix_loop_count if mix_loop_count else "10"))
    L.append("")
    L.append("###### Advanced Configuration ############################################################")
    L.append("# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。")
    L.append("BucketNameFixed =")
    L.append("")
    L.append("# 固定的对象名，默认为空。")
    L.append("ObjectNameFixed =")
    L.append("")
    L.append("# 鉴权签名算法,可选AWSV2 | AWSV4 | 空")
    L.append("AuthAlgorithm = AWSV2")
    L.append("")
    L.append("# 请求所在Region名称")
    L.append("Region =")
    L.append("")
    L.append("# 是否使用域名")
    L.append("UseDomainName = true")
    L.append("")
    L.append("# 是否使用虚拟主机方式请求")
    L.append("VirtualHost = false")
    L.append("")
    L.append("# 域名地址（请替换<region>为实际区域，如cn-north-4）")
    L.append("DomainName = obs.<region>.myhuaweicloud.com")
    L.append("")
    L.append("# 使用HTTP还是HTTPs请求")
    L.append("IsHTTPs = true")
    L.append("")
    L.append("# 是否使用Http2.0")
    L.append("IsHTTP2 = false")
    L.append("")
    L.append("# 链接是否多路复用")
    L.append("IsShareConnection = false")
    L.append("")
    L.append("# ssl协议版本号配置")
    L.append("sslVersion =")
    L.append("")
    L.append("# 服务器端数据加密方法")
    L.append("SrvSideEncryptType =")
    L.append("")
    L.append("# 指定服务端加密算法")
    L.append("SrvSideEncryptAlgorithm = aws:kms")
    L.append("")
    L.append("# 指定KMS master encryption key ID")
    L.append("SrvSideEncryptAWSKMSKeyId =")
    L.append("")
    L.append("# 指定服务端器加密context")
    L.append("SrvSideEncryptContext =")
    L.append("")
    L.append("# 是否复用连接。True=长连接复用; False=短连接每次新建")
    L.append("LongConnection = true")
    L.append("")
    L.append("# 客户端发送的http header connection值")
    L.append("ConnectionHeader =")
    L.append("")
    L.append("# 连接建立/请求等待超时时间(秒)")
    L.append("ConnectTimeout = 30")
    L.append("")
    L.append("# 上传下载是否计算MD5")
    L.append("CalHashMD5 = false")
    L.append("")
    L.append("# 统计结果时间段(单位：ms)")
    L.append("LatencySections = 500,1000,3000,10000")
    L.append("")
    L.append("# 是否记录每个请求的详细结果到detail文件")
    L.append("RecordDetails = true")
    L.append("")
    L.append("# 性能统计时间间隔(单位:s)")
    L.append("StatisticsInterval = 3")
    L.append("")
    L.append("# 性能统计结果是否包含错误请求")
    L.append("BadRequestCounted = false")
    L.append("")
    L.append("# 是否避免多并发对同一个桶进行上传、删除对象操作")
    L.append("AvoidSinBkOp = true")
    L.append("")
    L.append("# 运行时长（秒），0或空表示按请求数完成后退出")
    L.append("RunSeconds = %s" % (run_seconds if run_seconds else ""))
    L.append("")
    L.append("# 限制每并发每秒的最大请求数")
    L.append("TpsPerThread =")
    L.append("")
    L.append("# 限制每并发运行的周期窗口时间")
    L.append("RunWindowSeconds =")
    L.append("StopWindowSeconds =")
    L.append("")
    L.append("# 匿名访问，不带鉴权相关的头域")
    L.append("Anonymous = false")
    L.append("")
    L.append("# 是否打印运行中的实时结果和进度")
    L.append("PrintProgress = true")
    L.append("")
    L.append("# 性能统计结果是否包含各个请求的时延")
    L.append("LatencyPercentileMap = true")
    L.append("")
    L.append("# 时延百分位点")
    L.append("LatencyPercentileMapSections = 10,50,90,95,99")
    L.append("")
    L.append("# 性能统计结果是否包含各个时延段请求数")
    L.append("LatencyRequestsNumber = false")
    L.append("LatencyRequestsNumberSections = 20")
    L.append("")
    L.append("# 是否将ObjectNamePattern通过ProcessID产生HashId")
    L.append("ObjNamePatternHash = true")
    L.append("")
    L.append("# 是否只需要打印基本数据")
    L.append("CollectBasicData = false")
    L.append("")
    L.append("# 是否在业务过程中通过curl进行网络检查")
    L.append("TestNetwork = false")
    L.append("")
    L.append("# 运行obsPyTool工具的模式 1=集成式 2=分布式")
    L.append("Mode = 1")
    L.append("IsMaster = false")
    L.append("")
    return "\n".join(L)


def sz_label(s):
    return {4096:"4KB", 32768:"32KB", 1048576:"1MB", 4194304:"4MB"}[s]

def sz_short(s):
    return {4096:"4K", 32768:"32K", 1048576:"1M", 4194304:"4M"}[s]

def obj_count(size, conc):
    """根据对象大小和并发数确定每桶每线程对象数"""
    if conc <= 1:
        return {4096:1000, 32768:1000, 1048576:200, 4194304:100}[size]
    elif conc <= 10:
        return {4096:1000, 32768:1000, 1048576:100, 4194304:50}[size]
    else:
        return {4096:500, 32768:500, 1048576:50, 4194304:20}[size]


def fmt_size(bytes_val):
    """将字节数格式化为可读字符串"""
    if bytes_val >= 1073741824:
        return "%.1f GB" % (bytes_val / 1073741824.0)
    elif bytes_val >= 1048576:
        return "%.1f MB" % (bytes_val / 1048576.0)
    elif bytes_val >= 1024:
        return "%.1f KB" % (bytes_val / 1024.0)
    else:
        return "%d B" % bytes_val


def case_block(case_id, test_type, obj_size, conc, cfg, run_cmd, preconditions, expected, goal="",
               users=1, buckets_per_user=1, objects_per_bucket=1000):
    """生成单个用例的完整markdown"""
    # 计算对象总数和数据量
    total_objects = users * conc * buckets_per_user * objects_per_bucket
    total_bytes = total_objects * obj_size

    b = []
    b.append("#### 用例编号：%s" % case_id)
    b.append("")
    b.append("| 项目 | 内容 |")
    b.append("|------|------|")
    b.append("| **用例编号** | %s |" % case_id)
    b.append("| **测试类型** | %s |" % test_type)
    b.append("| **对象大小** | %s (%d 字节) |" % (sz_label(obj_size), obj_size))
    b.append("| **并发数** | %d (Users=%d, ThreadsPerUser=%d) |" % (conc, users, conc))
    b.append("| **对象总数** | %s |" % "{:,}".format(total_objects))
    b.append("| **预计写入/读取数据量** | %s |" % fmt_size(total_bytes))
    b.append("| **计算公式** | Users(%d) × ThreadsPerUser(%d) × BucketsPerUser(%d) × ObjectsPerBucketPerThread(%d) = **%s** |" % (users, conc, buckets_per_user, objects_per_bucket, "{:,}".format(total_objects)))
    if goal:
        b.append("| **测试目标** | %s |" % goal)
    b.append("")
    b.append("**预置条件**：")
    for i, p in enumerate(preconditions, 1):
        b.append("%d. %s" % (i, p))
    b.append("")
    b.append("**完整 config.dat（直接复制全部内容到 config.dat 文件）**：")
    b.append("```ini")
    b.append(cfg)
    b.append("```")
    b.append("")
    b.append("**执行命令**：")
    b.append("```bash")
    b.append(run_cmd)
    b.append("```")
    b.append("")
    b.append("**预期结果**：")
    for i, e in enumerate(expected, 1):
        b.append("%d. %s" % (i, e))
    b.append("")
    b.append("---")
    b.append("")
    return "\n".join(b)


# ====== 开始拼装 ======
out = []

out.append("# 基于 obscmdbench 工具的 OBS 性能测试用例")
out.append("")
out.append("> 工具来源：https://github.com/huaweicloud-obs/obscmdbench")
out.append("> 编写日期：2026-06-09")
out.append("> 总用例数：**72 个**（5 大场景）")
out.append('> **核心说明**：每个用例均包含 **完整 config.dat 全部参数**，TE 可直接复制粘贴到 `config.dat` 文件，替换 `DomainName` 中的 `<region>` 后即可执行。无需自行拆解参数。')
out.append("")
out.append("---")
out.append("")

# ========== 测试用例总览表 ==========
out.append("## 测试用例总览")
out.append("")
out.append("> 共 **5 大场景、72 个用例**")
out.append("")

SIZES = [4096, 32768, 1048576, 4194304]
CONCS_S1 = [1, 10, 100]

# 场景一总览
out.append("### 场景一：单客户端全覆盖测试（48 个用例）")
out.append("")
out.append("| 用例编号 | 读写类型 | 对象大小 | 并发数 | 测试点 |")
out.append("|:--------:|:-------:|:-------:|:------:|-------|")
tp = {"SEQ-W":"PutObject 字典序命名顺序写，验证TPS/延迟基线",
      "RAND-W":"PutObject 随机对象名写入，验证随机写TPS/延迟",
      "SEQ-R":"GetObject 顺序遍历读取，验证TPS/吞吐",
      "RAND-R":"GetObject 随机选取读取，验证TPS/吞吐"}
for t,tl in [("SEQ-W","顺序写"),("RAND-W","随机写"),("SEQ-R","顺序读"),("RAND-R","随机读")]:
    for s in SIZES:
        for c in CONCS_S1:
            out.append("| S1-%s-%s-%d | %s | %s | %d | %s |" % (t,sz_short(s),c,tl,sz_label(s),c,tp[t]))
out.append("")

# 场景二~五总览
for sn, slbl, sdesc, ssize in [
    (2,"4KB IOPS峰值","4KB小块在100/500高并发下压测IOPS极限",4096),
    (3,"32KB IOPS峰值","32KB中块在100/500高并发下压测IOPS极限",32768),
    (4,"1MB 带宽峰值","1MB大块在100/500高并发下压测带宽极限",1048576),
    (5,"4MB 带宽峰值","4MB最大块在100/500高并发下压测带宽极限",4194304)]:
    out.append("### 场景%d：%s测试（6 个用例）" % (sn, slbl))
    out.append("")
    out.append("> %s" % sdesc)
    out.append("")
    out.append("| 用例编号 | 读写模式 | 对象大小 | 并发数 | 测试点 |")
    out.append("|:--------:|:-------:|:-------:|:------:|-------|")
    ss = sz_short(ssize)
    modes = [("READ","混合读（纯读）","纯GetObject"),("WRITE","混合写（纯写）","纯PutObject"),("MIX","混合读写 2:1","2Get+1Put")]
    for m,ml,mdesc in modes:
        for conc in [100,500]:
            out.append("| S%d-%s-%s-%d | %s | %s | %d | %s %d并发 |" % (sn,m,ss,conc,ml,sz_label(ssize),conc,mdesc,conc))
    out.append("")

out.append("---")
out.append("")

# ========== 全局预置条件 ==========
out.append("## 全局预置条件（适用于所有场景）")
out.append("")
out.append("### 1. 环境准备")
out.append("")
out.append("| 序号 | 预置条件 | 说明 |")
out.append("|:---:|---------|------|")
out.append("| 1 | 测试客户端已安装 Python 2.7.9+ | obscmdbench 依赖 Python 环境 |")
out.append("| 2 | 已下载 obscmdbench 工具 | `git clone https://github.com/huaweicloud-obs/obscmdbench.git` |")
out.append("| 3 | 已创建华为云 OBS 桶 | 桶已创建且可正常访问 |")
out.append("| 4 | 已配置 AK/SK 测试账号 | 在 `users.dat` 中配置测试账号 |")
out.append("| 5 | 测试客户端与 OBS 网络连通 | 延迟 < 5ms，带宽充足 |")
out.append("| 6 | 已关闭 DNS 缓存服务 | `service nscd stop`（若使用域名方式） |")
out.append("")
out.append("### 2. users.dat 配置（所有场景通用）")
out.append("")
out.append("```")
out.append("testuser,<your_access_key>,<your_secret_key>")
out.append("```")
out.append("")
out.append("### 3. 使用方法（3 步执行）")
out.append("")
out.append("1. **复制配置**：将用例中的完整 config.dat 内容复制到工具目录下的 `config.dat` 文件（覆盖原有内容）")
out.append("2. **修改域名**：将 `DomainName` 中的 `<region>` 替换为实际区域代码（如 `cn-north-4`）")
out.append("3. **执行命令**：运行用例中给出的 `./run.py` 命令")
out.append("")
out.append("### 4. 结果查看")
out.append("")
out.append("- `./result/*_brief.txt`：汇总结果（TPS、平均延迟、延迟分布）")
out.append("- `./result/*_detail.csv`：每个请求的详细结果")
out.append("- `./result/*_realtime.txt`：实时性能统计（TPS、SendBytes、RecvBytes）")
out.append("")
out.append("---")
out.append("")


# ========== 场景一 ==========
out.append("## 场景一：单客户端全覆盖测试")
out.append("")
out.append("在单个测试客户端上，覆盖顺序读、随机读、顺序写、随机写 4 种读写类型，并发数覆盖 1/10/100，对象大小覆盖 4KB/32KB/1MB/4MB。")
out.append("")
out.append("> **测试矩阵**：4 种读写类型 × 3 种并发数 × 4 种对象大小 = **48 个测试用例**")
out.append("")

# --- 顺序写 12 个 ---
out.append("### 场景一-1：顺序写（Testcase=201, ObjectLexical=true）")
out.append("")
out.append("PutObject 操作，对象名按字典序排列（ObjectLexical=true）。后续读取测试依赖这些有序对象。")
out.append("")

for s in SIZES:
    for c in CONCS_S1:
        cid = "S1-SEQ-W-%s-%d" % (sz_short(s), c)
        prefix = "obj.seq.%s.c%d" % (sz_short(s).lower(), c)
        oc = obj_count(s, c)
        cfg = make_config(201, 1, c, s, oc, True, prefix)
        run = "./run.py 201 1 config.dat"
        pcs = [
            "全局预置条件已满足",
            "目标桶已创建（通过 BucketNamePrefix 自动命名）",
            "桶内无同名对象（或可覆盖）",
        ]
        ers = [
            "所有请求返回 200 OK，错误率 %s" % ("= 0%%" if c < 100 else "< 0.1%%"),
            "结果文件 `./result/` 下生成 `*_PutObject_%d_brief.txt`" % c,
            "记录 TPS、平均延迟、吞吐量指标",
        ]
        goal = "%s %s顺序写入，验证TPS/延迟基线，%d并发" % (sz_label(s), "顺序" if s <= 32768 else "大对象顺序", c)
        out.append(case_block(cid, "顺序写", s, c, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=oc))

# --- 随机写 12 个 ---
out.append("### 场景一-2：随机写（Testcase=201, ObjectLexical=false）")
out.append("")
out.append("PutObject 操作，对象名为随机生成（ObjectLexical=false），长度 15~1024 字节。")
out.append("")

for s in SIZES:
    for c in CONCS_S1:
        cid = "S1-RAND-W-%s-%d" % (sz_short(s), c)
        prefix = "obj.rand.%s.c%d" % (sz_short(s).lower(), c)
        oc = obj_count(s, c)
        cfg = make_config(201, 1, c, s, oc, False, prefix)
        run = "./run.py 201 1 config.dat"
        pcs = [
            "全局预置条件已满足",
            "目标桶已创建",
        ]
        ers = [
            "错误率 %s" % ("= 0%%" if c < 100 else "< 0.1%%"),
            "对象名为随机生成（长度 15~1024 字节）",
            "记录 TPS、平均延迟",
        ]
        goal = "%s 随机对象名写入，验证随机写TPS，%d并发" % (sz_label(s), c)
        out.append(case_block(cid, "随机写", s, c, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=oc))

# --- 顺序读 12 个 ---
out.append("### 场景一-3：顺序读（Testcase=202, IsRandomGet=false）")
out.append("")
out.append("GetObject 操作，按字典序遍历桶内所有对象。**前置依赖：需先执行对应的顺序写用例上传对象。**")
out.append("")

for s in SIZES:
    for c in CONCS_S1:
        cid = "S1-SEQ-R-%s-%d" % (sz_short(s), c)
        # 读操作需匹配上传时的 prefix 和 object count
        wprefix = "obj.seq.%s.c%d" % (sz_short(s).lower(), c)
        woc = obj_count(s, c)
        cfg = make_config(202, 1, c, s, woc, True, wprefix, is_random_get=False)
        run = "./run.py 202 1 config.dat"
        pcs = [
            "全局预置条件已满足",
            "**前置步骤已完成**：已使用 S1-SEQ-W-%s-%d 上传了 %d 个 %s 对象" % (sz_short(s), c, woc, sz_label(s)),
            "config.dat 中的 ObjectNamePrefix、ObjectsPerBucketPerThread 与上传时一致",
        ]
        ers = [
            "所有请求返回 200 OK，错误率 %s" % ("= 0%%" if c < 100 else "< 0.1%%"),
            "顺序遍历桶内所有对象进行读取",
            "记录 TPS、平均延迟、下载吞吐量（RecvBytes/s）",
        ]
        goal = "%s 顺序遍历读取（依赖前置写入数据），%d并发" % (sz_label(s), c)
        out.append(case_block(cid, "顺序读", s, c, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=woc))

# --- 随机读 12 个 ---
out.append("### 场景一-4：随机读（Testcase=202, IsRandomGet=true）")
out.append("")
out.append("GetObject 操作，随机选取桶内对象读取，不按顺序遍历。**前置依赖：需先执行对应的顺序写用例上传对象。**")
out.append("")

for s in SIZES:
    for c in CONCS_S1:
        cid = "S1-RAND-R-%s-%d" % (sz_short(s), c)
        wprefix = "obj.seq.%s.c%d" % (sz_short(s).lower(), c)
        woc = obj_count(s, c)
        cfg = make_config(202, 1, c, s, woc, True, wprefix, is_random_get=True)
        run = "./run.py 202 1 config.dat"
        pcs = [
            "全局预置条件已满足",
            "**前置步骤已完成**：已使用 S1-SEQ-W-%s-%d 上传了 %d 个 %s 对象" % (sz_short(s), c, woc, sz_label(s)),
            "对象名为字典序（ObjectLexical=true 时上传的对象）",
        ]
        ers = [
            "错误率 %s" % ("= 0%%" if c < 100 else "< 0.1%%"),
            "随机选取桶内对象进行读取，不按顺序遍历",
            "记录 TPS、平均延迟、下载吞吐量",
        ]
        goal = "%s 随机选取读取（依赖前置写入数据），%d并发" % (sz_label(s), c)
        out.append(case_block(cid, "随机读", s, c, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=woc))


# ========== 场景二：4KB IOPS 峰值 ==========
out.append("## 场景二：4KB 块大小 IOPS 峰值测试")
out.append("")
out.append("使用 4KB 对象大小，在 100 和 500 并发下测试 IOPS 峰值。覆盖混合读（纯读）、混合写（纯写）、混合读写（2:1）三种模式。")
out.append("")
out.append("> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**")
out.append("")

out.append("### 前置数据准备（场景二通用）")
out.append("")
out.append("执行读操作前需先上传足够 4KB 对象。**将以下内容复制到 config.dat 并执行**：")
out.append("")
out.append("```ini")
out.append(make_config(201, 1, 500, 4096, 2000, True, "perf.4k"))
out.append("```")
out.append("")
out.append("```bash")
out.append("./run.py 201 1 config.dat")
out.append("```")
out.append("")
out.append("---")
out.append("")

# 混合读
out.append("### 场景二-1：混合读（纯读 IOPS 峰值）")
out.append("")
out.append("MixOperation 模式仅执行 GetObject(202)，测试纯读 IOPS 极限。运行 300 秒取稳态数据。")
out.append("")

for conc in [100, 500]:
    cid = "S2-READ-4K-%d" % conc
    oc = 2000 if conc == 100 else 1000
    loop = 50 if conc == 100 else 20
    cfg = make_config(900, 1, conc, 4096, oc, True, "perf.4k", mix_operations="202", mix_loop_count=loop, run_seconds="300")
    run = "./run.py 900 1 config.dat"
    pcs = [
        "全局预置条件已满足",
        "已通过前置数据准备上传了足够数量的 4KB 对象",
    ]
    ers = [
        "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
        "运行 300 秒后自动结束",
        "从 `*_realtime.txt` 取稳态区间 TPS 均值作为 IOPS 峰值",
        "从 `*_brief.txt` 读取 P50/P90/P99 延迟",
        "IOPS 达到或接近 OBS 4KB 读性能规格上限",
        "P99 延迟 < 100ms",
    ]
    out.append(case_block(cid, "混合读（纯 GetObject）", 4096, conc, cfg, run, pcs, ers,
                          "测试4KB纯读IOPS峰值，%d并发" % conc,
                          users=1, buckets_per_user=1, objects_per_bucket=oc))

# 混合写
out.append("### 场景二-2：混合写（纯写 IOPS 峰值）")
out.append("")
out.append("MixOperation 模式仅执行 PutObject(201)，测试纯写 IOPS 极限。")
out.append("")

for conc in [100, 500]:
    cid = "S2-WRITE-4K-%d" % conc
    oc = 2000 if conc == 100 else 1000
    loop = 50 if conc == 100 else 20
    cfg = make_config(900, 1, conc, 4096, oc, True, "perf.4k.write", mix_operations="201", mix_loop_count=loop, run_seconds="300")
    run = "./run.py 900 1 config.dat"
    pcs = ["全局预置条件已满足", "目标桶已创建"]
    ers = [
        "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
        "运行 300 秒后自动结束",
        "从 `*_realtime.txt` 取稳态 TPS 作为写 IOPS 峰值",
        "写 IOPS 达到或接近 OBS 4KB 写性能规格上限",
        "P99 延迟 < 200ms",
    ]
    out.append(case_block(cid, "混合写（纯 PutObject）", 4096, conc, cfg, run, pcs, ers,
                          "测试4KB纯写IOPS峰值，%d并发" % conc,
                          users=1, buckets_per_user=1, objects_per_bucket=oc))

# 混合读写
out.append("### 场景二-3：混合读写 2:1（IOPS 峰值）")
out.append("")
out.append("MixOperation 模式执行 `202,202,201`（2 个 Get + 1 个 Put），实现读写比 2:1 的混合负载。")
out.append("")

for conc in [100, 500]:
    cid = "S2-MIX-4K-%d" % conc
    oc = 2000 if conc == 100 else 1000
    loop = 100 if conc == 100 else 50
    cfg = make_config(900, 1, conc, 4096, oc, True, "perf.4k.mix", mix_operations="202,202,201", mix_loop_count=loop, run_seconds="300")
    run = "./run.py 900 1 config.dat"
    pcs = ["全局预置条件已满足", "目标桶已创建", "前置数据已上传（桶内有可读 4KB 对象）"]
    ers = [
        "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
        "运行 300 秒后自动结束",
        "总 IOPS = 读 TPS + 写 TPS，其中读 TPS ≈ 2 × 写 TPS",
        "混合 IOPS 介于纯读和纯写之间",
        "P99 延迟 < 150ms",
    ]
    out.append(case_block(cid, "混合读写（读:写=2:1）", 4096, conc, cfg, run, pcs, ers,
                          "测试4KB混合读写(2:1)IOPS峰值，%d并发" % conc,
                          users=1, buckets_per_user=1, objects_per_bucket=oc))


# ========== 场景三：32KB IOPS 峰值 ==========
out.append("## 场景三：32KB 块大小 IOPS 峰值测试")
out.append("")
out.append("使用 32KB 对象大小，在 100 和 500 并发下测试 IOPS 峰值。与场景二对比：32KB IOPS 应低于 4KB，但吞吐量（MB/s）更高。")
out.append("")

out.append("### 前置数据准备（场景三通用）")
out.append("")
out.append("```ini")
out.append(make_config(201, 1, 500, 32768, 1000, True, "perf.32k"))
out.append("```")
out.append("")
out.append("```bash")
out.append("./run.py 201 1 config.dat")
out.append("```")
out.append("")
out.append("---")
out.append("")

for section, label, mix_op, desc_extra in [
    ("场景三-1","混合读（纯读 IOPS 峰值）","202",
     "MixOperation 仅执行 GetObject(202)，测试 32KB 纯读 IOPS 极限。与 S2-READ-4K 对比 IOPS/吞吐差异。"),
    ("场景三-2","混合写（纯写 IOPS 峰值）","201",
     "MixOperation 仅执行 PutObject(201)，测试 32KB 纯写 IOPS 极限。"),
    ("场景三-3","混合读写 2:1（IOPS 峰值）","202,202,201",
     "MixOperation 执行 `202,202,201`（2Get+1Put），实现读写比 2:1。")]:
    out.append("### %s" % section)
    out.append("")
    out.append(desc_extra)
    out.append("")
    for conc in [100, 500]:
        if "READ" in section or section == "场景三-1":
            cid_prefix = "READ"
            pfx = "perf.32k"
        elif "WRITE" in section or section == "场景三-2":
            cid_prefix = "WRITE"
            pfx = "perf.32k.write"
        else:
            cid_prefix = "MIX"
            pfx = "perf.32k.mix"
        cid = "S3-%s-32K-%d" % (cid_prefix, conc)
        oc = 1000 if conc == 100 else 500
        loop = (50 if conc == 100 else 20) if cid_prefix != "MIX" else (100 if conc == 100 else 50)
        cfg = make_config(900, 1, conc, 32768, oc, True, pfx, mix_operations=mix_op, mix_loop_count=loop, run_seconds="300")
        run = "./run.py 900 1 config.dat"
        if "READ" in cid:
            pcs = ["全局预置条件已满足", "已通过前置数据准备上传了足够 32KB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "32KB 读 IOPS 应低于 4KB 读 IOPS",
                "32KB 读吞吐量(MB/s)应高于 4KB 读吞吐量",
                "P99 延迟 < 100ms",
            ]
        elif "WRITE" in cid:
            pcs = ["全局预置条件已满足", "目标桶已创建"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "32KB 写 IOPS 达到或接近 OBS 32KB 写规格上限",
                "P99 延迟 < 200ms",
            ]
        else:
            pcs = ["全局预置条件已满足", "桶中已上传足够的 32KB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "总 IOPS = 读 TPS + 写 TPS，读 TPS ≈ 2 × 写 TPS",
                "混合 IOPS 介于纯读和纯写之间",
            ]
        goal = "测试32KB%s，%d并发" % (label.split("（")[0], conc)
        out.append(case_block(cid, label, 32768, conc, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=oc))


# ========== 场景四：1MB 带宽峰值 ==========
out.append("## 场景四：1MB 块大小带宽峰值测试")
out.append("")
out.append("使用 1MB 对象大小，在 100 和 500 并发下测试带宽（吞吐量）峰值。关注 MB/s 或 Gbps 级别表现。")
out.append("")

out.append("### 前置数据准备（场景四通用）")
out.append("")
out.append("```ini")
out.append(make_config(201, 1, 500, 1048576, 200, True, "perf.1m"))
out.append("```")
out.append("")
out.append("```bash")
out.append("./run.py 201 1 config.dat")
out.append("```")
out.append("")
out.append("---")
out.append("")

for section, label, mix_op, desc_extra in [
    ("场景四-1","混合读（纯读带宽峰值）","202",
     "MixOperation 仅执行 GetObject(202)，测试 1MB 纯读带宽极限。提取 RecvBytes 计算吞吐量。"),
    ("场景四-2","混合写（纯写带宽峰值）","201",
     "MixOperation 仅执行 PutObject(201)，测试 1MB 纯写带宽极限。提取 SendBytes 计算吞吐量。"),
    ("场景四-3","混合读写 2:1（带宽峰值）","202,202,201",
     "MixOperation 执行 `202,202,201`（2Get+1Put），测试 1MB 混合带宽极限。")]:
    out.append("### %s" % section)
    out.append("")
    out.append(desc_extra)
    out.append("")
    for conc in [100, 500]:
        if "READ" in section or section == "场景四-1":
            cid_prefix = "READ"; pfx = "perf.1m"
        elif "WRITE" in section or section == "场景四-2":
            cid_prefix = "WRITE"; pfx = "perf.1m.write"
        else:
            cid_prefix = "MIX"; pfx = "perf.1m.mix"
        cid = "S4-%s-1M-%d" % (cid_prefix, conc)
        oc = 500 if conc == 100 else 200
        loop = (30 if conc == 100 else 10) if cid_prefix != "MIX" else (60 if conc == 100 else 30)
        cfg = make_config(900, 1, conc, 1048576, oc, True, pfx, mix_operations=mix_op, mix_loop_count=loop, run_seconds="300")
        run = "./run.py 900 1 config.dat"
        if "READ" in cid:
            pcs = ["全局预置条件已满足", "桶中已上传足够的 1MB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "从 `*_realtime.txt` 提取 RecvBytes 计算带宽：带宽(MB/s) = RecvBytes / StatisticsInterval / 1048576",
                "读带宽达到或接近 OBS 1MB 读吞吐量规格上限",
                "稳态区间带宽波动 < 10%%",
                "P99 延迟 < 500ms",
            ]
        elif "WRITE" in cid:
            pcs = ["全局预置条件已满足", "目标桶已创建"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "从 `*_realtime.txt` 提取 SendBytes 计算写带宽",
                "写带宽达到或接近 OBS 1MB 写吞吐量规格上限",
                "P99 延迟 < 800ms",
            ]
        else:
            pcs = ["全局预置条件已满足", "桶中已上传足够的 1MB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "读带宽 ≈ 2 × 写带宽（符合 2:1 配比）",
                "混合总带宽 = 读带宽 + 写带宽，介于纯读和纯写之间",
            ]
        goal = "测试1MB%s，%d并发" % (label.split("（")[0], conc)
        out.append(case_block(cid, label, 1048576, conc, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=oc))


# ========== 场景五：4MB 带宽峰值 ==========
out.append("## 场景五：4MB 块大小带宽峰值测试")
out.append("")
out.append("使用 4MB 对象大小，在 100 和 500 并发下测试带宽峰值。验证最大吞吐量是否达到 OBS 规格上限。")
out.append("")

out.append("### 前置数据准备（场景五通用）")
out.append("")
out.append("> **注意**：4MB 对象上传耗时较长，建议提前执行。")
out.append("")
out.append("```ini")
out.append(make_config(201, 1, 500, 4194304, 100, True, "perf.4m"))
out.append("```")
out.append("")
out.append("```bash")
out.append("./run.py 201 1 config.dat")
out.append("```")
out.append("")
out.append("---")
out.append("")

for section, label, mix_op, desc_extra in [
    ("场景五-1","混合读（纯读带宽峰值）","202",
     "MixOperation 仅执行 GetObject(202)，测试 4MB 纯读带宽极限。与 1MB 场景对比带宽提升。"),
    ("场景五-2","混合写（纯写带宽峰值）","201",
     "MixOperation 仅执行 PutObject(201)，测试 4MB 纯写带宽极限。"),
    ("场景五-3","混合读写 2:1（带宽峰值）","202,202,201",
     "MixOperation 执行 `202,202,201`（2Get+1Put），测试 4MB 混合带宽极限。")]:
    out.append("### %s" % section)
    out.append("")
    out.append(desc_extra)
    out.append("")
    for conc in [100, 500]:
        if "READ" in section or section == "场景五-1":
            cid_prefix = "READ"; pfx = "perf.4m"
        elif "WRITE" in section or section == "场景五-2":
            cid_prefix = "WRITE"; pfx = "perf.4m.write"
        else:
            cid_prefix = "MIX"; pfx = "perf.4m.mix"
        cid = "S5-%s-4M-%d" % (cid_prefix, conc)
        oc = 200 if conc == 100 else 100
        loop = (20 if conc == 100 else 10) if cid_prefix != "MIX" else (40 if conc == 100 else 20)
        cfg = make_config(900, 1, conc, 4194304, oc, True, pfx, mix_operations=mix_op, mix_loop_count=loop, run_seconds="300")
        run = "./run.py 900 1 config.dat"
        if "READ" in cid:
            pcs = ["全局预置条件已满足", "桶中已上传足够的 4MB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "从 `*_realtime.txt` 提取 RecvBytes 计算带宽：带宽(MB/s) = RecvBytes / StatisticsInterval / 1048576",
                "4MB 读带宽达到或接近 OBS 读吞吐量规格上限",
                "相比 1MB 场景带宽可能进一步提升",
                "稳态区间带宽波动 < 10%%",
                "P99 延迟 < 1000ms",
            ]
        elif "WRITE" in cid:
            pcs = ["全局预置条件已满足", "目标桶已创建"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "从 `*_realtime.txt` 提取 SendBytes 计算写带宽",
                "4MB 写带宽达到或接近 OBS 写吞吐量规格上限",
                "P99 延迟 < 1500ms",
            ]
        else:
            pcs = ["全局预置条件已满足", "桶中已上传足够的 4MB 对象"]
            ers = [
                "错误率 %s" % ("= 0%%" if conc == 100 else "< 0.1%%"),
                "读带宽 ≈ 2 × 写带宽（符合 2:1 配比）",
                "混合总带宽 = 读带宽 + 写带宽，介于纯读和纯写之间",
            ]
        goal = "测试4MB%s，%d并发" % (label.split("（")[0], conc)
        out.append(case_block(cid, label, 4194304, conc, cfg, run, pcs, ers, goal,
                              users=1, buckets_per_user=1, objects_per_bucket=oc))


# ========== 全场景汇总 ==========
out.append("## 全场景测试用例汇总")
out.append("")
out.append("### 总用例数统计")
out.append("")
out.append("| 场景 | 用例数 | 说明 |")
out.append("|:----:|:-----:|------|")
out.append("| 场景一：单客户端全覆盖 | **48** | 4读写类型 × 3并发 × 4大小 |")
out.append("| 场景二：4KB IOPS 峰值 | **6** | 3读写模式 × 2并发 |")
out.append("| 场景三：32KB IOPS 峰值 | **6** | 3读写模式 × 2并发 |")
out.append("| 场景四：1MB 带宽峰值 | **6** | 3读写模式 × 2并发 |")
out.append("| 场景五：4MB 带宽峰值 | **6** | 3读写模式 × 2并发 |")
out.append("| **合计** | **72** | |")
out.append("")
out.append("### 场景执行顺序建议")
out.append("")
out.append("```")
out.append("Step 1: 场景一 - 顺序写（12个用例）→ 产出桶内数据供后续读取")
out.append("Step 2: 场景一 - 顺序读（12个用例）→ 使用 Step 1 上传的数据")
out.append("Step 3: 场景一 - 随机写（12个用例）")
out.append("Step 4: 场景一 - 随机读（12个用例）→ 使用 Step 1 上传的数据")
out.append("Step 5: 场景二 - 前置数据准备 + 6个测试用例")
out.append("Step 6: 场景三 - 前置数据准备 + 6个测试用例")
out.append("Step 7: 场景四 - 前置数据准备 + 6个测试用例")
out.append("Step 8: 场景五 - 前置数据准备 + 6个测试用例")
out.append("```")
out.append("")
out.append("### 结果分析方法")
out.append("")
out.append("#### IOPS 计算（场景二、三）")
out.append("")
out.append("```")
out.append("IOPS = TPS（从 *_brief.txt 中 Total TPS 字段读取）")
out.append("读 IOPS = GetObject 的 TPS")
out.append("写 IOPS = PutObject 的 TPS")
out.append("混合总 IOPS = 读 IOPS + 写 IOPS")
out.append("```")
out.append("")
out.append("#### 带宽计算（场景四、五）")
out.append("")
out.append("```")
out.append("读带宽（MB/s）= RecvBytes / 统计间隔 / 1048576")
out.append("写带宽（MB/s）= SendBytes / 统计间隔 / 1048576")
out.append("总带宽 = 读带宽 + 写带宽")
out.append("```")
out.append("")
out.append("从 `*_realtime.txt` 中提取稳态区间（去除前 30 秒预热期）的 RecvBytes / SendBytes 列计算。")
out.append("")
out.append("#### 延迟分析")
out.append("")
out.append("```")
out.append("从 *_brief.txt 中读取：")
out.append("- AvgLatency：平均延迟")
out.append("- LatencySections 分布：各延迟区间的请求占比")
out.append("- LatencyPercentileMap：P10/P50/P90/P95/P99 延迟")
out.append("```")

# 写入文件
path = "/root/huawei_soultion/obscmdbench_test_cases.md"
with open(path, "w") as f:
    f.write("\n".join(out))

# 统计
total_lines = sum(l.count("\n") for l in out) + len(out)
print("Generated: %s" % path)
print("Lines: %d" % total_lines)

# 统计用例数
case_count = 0
for line in out:
    if line.startswith("#### 用例编号："):
        case_count += 1
print("Total test cases: %d" % case_count)

# 统计config块数
config_count = 0
for line in out:
    if "ObjectSize =" in line and "ThreadsPerUser" in "\n".join(out[max(0,out.index(line)-5):out.index(line)]):
        pass
config_blocks = "\n".join(out).count("```ini")
print("Total config blocks (```ini): %d" % config_blocks)
