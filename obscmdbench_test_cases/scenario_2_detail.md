## 场景二：4KB 块大小 IOPS 峰值测试

使用 4KB 对象大小，在 100 和 500 并发下测试 IOPS 峰值。覆盖混合读（纯读）、混合写（纯写）、混合读写（2:1）三种模式。

> **测试矩阵**：3 种读写模式 × 2 种并发 = **6 个测试用例**

### 前置数据准备（场景二通用）

执行读操作前需先上传足够 4KB 对象。**将以下内容复制到 config.dat 并执行**：

```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 201

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 500

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 2000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 100,101,104,201,102,202,203,204,103

# 循环次数
MixLoopCount = 10

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

```bash
./run.py 201 1 config.dat
```

---

### 场景二-1：混合读（纯读 IOPS 峰值）

MixOperation 模式仅执行 GetObject(202)，测试纯读 IOPS 极限。运行 300 秒取稳态数据。

#### 用例编号：S2-READ-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-READ-4K-100 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 100 (Users=1, ThreadsPerUser=100) |
| **测试目标** | 测试4KB纯读IOPS峰值，100并发 |

**预置条件**：
1. 全局预置条件已满足
2. 已通过前置数据准备上传了足够数量的 4KB 对象

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 100

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 2000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 202

# 循环次数
MixLoopCount = 50

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 = 0%%
2. 运行 300 秒后自动结束
3. 从 `*_realtime.txt` 取稳态区间 TPS 均值作为 IOPS 峰值
4. 从 `*_brief.txt` 读取 P50/P90/P99 延迟
5. IOPS 达到或接近 OBS 4KB 读性能规格上限
6. P99 延迟 < 100ms

---

#### 用例编号：S2-READ-4K-500

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-READ-4K-500 |
| **测试类型** | 混合读（纯 GetObject） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 500 (Users=1, ThreadsPerUser=500) |
| **测试目标** | 测试4KB纯读IOPS峰值，500并发 |

**预置条件**：
1. 全局预置条件已满足
2. 已通过前置数据准备上传了足够数量的 4KB 对象

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 500

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 1000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 202

# 循环次数
MixLoopCount = 20

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 < 0.1%%
2. 运行 300 秒后自动结束
3. 从 `*_realtime.txt` 取稳态区间 TPS 均值作为 IOPS 峰值
4. 从 `*_brief.txt` 读取 P50/P90/P99 延迟
5. IOPS 达到或接近 OBS 4KB 读性能规格上限
6. P99 延迟 < 100ms

---

### 场景二-2：混合写（纯写 IOPS 峰值）

MixOperation 模式仅执行 PutObject(201)，测试纯写 IOPS 极限。

#### 用例编号：S2-WRITE-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-WRITE-4K-100 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 100 (Users=1, ThreadsPerUser=100) |
| **测试目标** | 测试4KB纯写IOPS峰值，100并发 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 100

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 2000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k.write

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 201

# 循环次数
MixLoopCount = 50

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 = 0%%
2. 运行 300 秒后自动结束
3. 从 `*_realtime.txt` 取稳态 TPS 作为写 IOPS 峰值
4. 写 IOPS 达到或接近 OBS 4KB 写性能规格上限
5. P99 延迟 < 200ms

---

#### 用例编号：S2-WRITE-4K-500

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-WRITE-4K-500 |
| **测试类型** | 混合写（纯 PutObject） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 500 (Users=1, ThreadsPerUser=500) |
| **测试目标** | 测试4KB纯写IOPS峰值，500并发 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 500

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 1000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k.write

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 201

# 循环次数
MixLoopCount = 20

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 < 0.1%%
2. 运行 300 秒后自动结束
3. 从 `*_realtime.txt` 取稳态 TPS 作为写 IOPS 峰值
4. 写 IOPS 达到或接近 OBS 4KB 写性能规格上限
5. P99 延迟 < 200ms

---

### 场景二-3：混合读写 2:1（IOPS 峰值）

MixOperation 模式执行 `202,202,201`（2 个 Get + 1 个 Put），实现读写比 2:1 的混合负载。

#### 用例编号：S2-MIX-4K-100

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-MIX-4K-100 |
| **测试类型** | 混合读写（读:写=2:1） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 100 (Users=1, ThreadsPerUser=100) |
| **测试目标** | 测试4KB混合读写(2:1)IOPS峰值，100并发 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建
3. 前置数据已上传（桶内有可读 4KB 对象）

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 100

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 2000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k.mix

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 202,202,201

# 循环次数
MixLoopCount = 100

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 = 0%%
2. 运行 300 秒后自动结束
3. 总 IOPS = 读 TPS + 写 TPS，其中读 TPS ≈ 2 × 写 TPS
4. 混合 IOPS 介于纯读和纯写之间
5. P99 延迟 < 150ms

---

#### 用例编号：S2-MIX-4K-500

| 项目 | 内容 |
|------|------|
| **用例编号** | S2-MIX-4K-500 |
| **测试类型** | 混合读写（读:写=2:1） |
| **对象大小** | 4KB (4096 字节) |
| **并发数** | 500 (Users=1, ThreadsPerUser=500) |
| **测试目标** | 测试4KB混合读写(2:1)IOPS峰值，500并发 |

**预置条件**：
1. 全局预置条件已满足
2. 目标桶已创建
3. 前置数据已上传（桶内有可读 4KB 对象）

**完整 config.dat（直接复制全部内容到 config.dat 文件）**：
```ini
#################test environment##################################################################

# [OSCs]OSC的IP地址。若配置项 [UseDomainName]为True，此项忽略。
# 示例：OSCs = 172.20.41.3,172.20.41.4,172.20.41.2
OSCs = 127.0.0.1

###################test case plan##################################################################
# 配置测试用例，用例对应操作见下。
Testcase = 900

# 用户数，1个用户对应users.dat中的一行用户信息
Users = 1

# 从user.dat中加载用户的起始行号,从0开始，空行跳过不计入。
UserStartIndex = 0

# 每个用户对应的的并发数，默认为1，表示1个用户对应1个并发。1个并发表示1个线程.
# 若配置项 [LongConnection]为True, 一个反复使用1个HTTP/HTTPs连接。
ThreadsPerUser = 500

############# "100=ListUserBuckets" #############################################################
# 每个并发的请求次数，仅对100=ListUserBuckets操作有效。
RequestsPerThread = 2000

############# "101=CreateBucket" ###############################################################
# 每个用户要创建的桶数：>=0，超过100，系统会返回409错误。
BucketsPerUser = 1

# 创建时指定桶Location, 不能包含空格，空代表不指定。
BucketLocation =

# 创建桶指定ACL，可选：private | public-read |public-read-write | authenticated-read |
# bucket-owner-read | bucket-owner-full-control, 空不携带
CreateWithACL = public-read-write

# 桶名中自定义标识
BucketNamePrefix = bucket.test

# 创桶指定x-default-storage-class, 可选：STANDARD、STANDARD_IA和GLACIER
StorageClass =

# 是否创建文件网关桶
IsFileInterface = false

############# "102=ListObjectsInBucket" #########################################################
# 一次请求的对象数，对应接口中的max-keys参数，1~1000有效
Max-keys = 1000

# 列举不带多版本。
prefix =

############# "111=PutBucketVersioning" #########################################################
# 桶多版本状态，可选值Enabled | Suspended
VersionStatus = Enabled

############# "151=PutBucketCORS" ############################################################
# AllowedMethod有效值GET、PUT、HEAD、POST、DELETE可带多个方法
AllowedMethod = GET

############# "161=PutBucketTag" ############################################################
# 配置key-value对数，默认1，最大10
KeyValueNumber = 10

############# "201=PutObject" ##################################################################
# 上传的对象大小（字节）
# 示例：ObjectSize = 4096 指定大小; ObjectSize = 0~1024 随机大小; ObjectSize = 0,1024,2048 离散值
ObjectSize = 4096

# 每个并发在每个桶中上传的对象数
ObjectsPerBucketPerThread = 1000

# 每个对象名上传次数，多次上传覆盖。
PutTimesForOneObj = 1

# 上传对象同时指定ACL
PutWithACL = public-read

# 对象是否字典序，若为false，系统则随机生成对象名，长度15~1024字节。
ObjectLexical = true

# 对象名前缀
ObjectNamePrefix = perf.4k.mix

# 对象名pattern, 字典序时有效
ObjectNamePartten = processID-ObjectNamePrefix-Index

# 创建对象指定x-default-storage-class
ObjectStorageClass =

# 设置x-obs-expires头域的值
Expires =

# 以指定对象的内容作为上传对象的实际内容而不从内存中生成
IsDataFromFile = False

# 指定对象路径
LocalFilePath =

############# "202=GetObject" ##################################################################
# 按以下顺序查找对象处理：
# 1) 查看是否指定了上传时生成的detail文件
objectDesFile =

# 指定Range下载对象，空表示不指定。
Range =

# 是否随机获取开关
IsRandomGet = false

# cdn开关
IsCdn = false
CdnAK =
CdnSK =
CdnSTSToken =

############# "204=DeleteObject" ################################################################
# 是否随机删除
IsRandomDelete = false

############# "205=DeleteMultiObjects" #######################################################
# 一次请求删除的对象数，1~100个
DeleteObjectsPerRequest = 3

############# "206=CopyObject" ##############################################################
copySrcObjFixed =
copyDstObjFixed =
copySrcSrvSideEncryptType = SSE-C

############# "207=RestoreObject" ##############################################################
RestoreDays =
RestoreTier =

############# "208=AppendObject" ##############################################################
GetPositionFromMeta = True

############# "209=ImageProcess" ##############################################################
ImageManipulationType =
ImageFormat =
CropParams =
ResizeParams =

############# "211=InitMultiUpload" ########################################################
MultiUploadStorageClass =

############# "212=UploadPart" ############################################################
# 每个uploadID要上传的段数量[1~10000]
PartsForEachUploadID = 3

# 针对每个uploadID是否并发上传段
ConcurrentUpParts = false

# 上传的段大小,obs协议要求最小5M
PartSize = 5242880

# 每个段上传次数
PutTimesForOnePart = 1

############# "900=MixOperation" ########################################################
# 设置混合操作类型，可设置以上除900外的所有操作。
MixOperations = 202,202,201

# 循环次数
MixLoopCount = 50

###### Advanced Configuration ############################################################
# 固定的桶名，默认为空。若配置，所有并发的所有操作均对该桶名进行。
BucketNameFixed =

# 固定的对象名，默认为空。
ObjectNameFixed =

# 鉴权签名算法,可选AWSV2 | AWSV4 | 空
AuthAlgorithm = AWSV2

# 请求所在Region名称
Region =

# 是否使用域名
UseDomainName = true

# 是否使用虚拟主机方式请求
VirtualHost = false

# 域名地址（请替换<region>为实际区域，如cn-north-4）
DomainName = obs.<region>.myhuaweicloud.com

# 使用HTTP还是HTTPs请求
IsHTTPs = true

# 是否使用Http2.0
IsHTTP2 = false

# 链接是否多路复用
IsShareConnection = false

# ssl协议版本号配置
sslVersion =

# 服务器端数据加密方法
SrvSideEncryptType =

# 指定服务端加密算法
SrvSideEncryptAlgorithm = aws:kms

# 指定KMS master encryption key ID
SrvSideEncryptAWSKMSKeyId =

# 指定服务端器加密context
SrvSideEncryptContext =

# 是否复用连接。True=长连接复用; False=短连接每次新建
LongConnection = true

# 客户端发送的http header connection值
ConnectionHeader =

# 连接建立/请求等待超时时间(秒)
ConnectTimeout = 30

# 上传下载是否计算MD5
CalHashMD5 = false

# 统计结果时间段(单位：ms)
LatencySections = 500,1000,3000,10000

# 是否记录每个请求的详细结果到detail文件
RecordDetails = true

# 性能统计时间间隔(单位:s)
StatisticsInterval = 3

# 性能统计结果是否包含错误请求
BadRequestCounted = false

# 是否避免多并发对同一个桶进行上传、删除对象操作
AvoidSinBkOp = true

# 运行时长（秒），0或空表示按请求数完成后退出
RunSeconds = 300

# 限制每并发每秒的最大请求数
TpsPerThread =

# 限制每并发运行的周期窗口时间
RunWindowSeconds =
StopWindowSeconds =

# 匿名访问，不带鉴权相关的头域
Anonymous = false

# 是否打印运行中的实时结果和进度
PrintProgress = true

# 性能统计结果是否包含各个请求的时延
LatencyPercentileMap = true

# 时延百分位点
LatencyPercentileMapSections = 10,50,90,95,99

# 性能统计结果是否包含各个时延段请求数
LatencyRequestsNumber = false
LatencyRequestsNumberSections = 20

# 是否将ObjectNamePattern通过ProcessID产生HashId
ObjNamePatternHash = true

# 是否只需要打印基本数据
CollectBasicData = false

# 是否在业务过程中通过curl进行网络检查
TestNetwork = false

# 运行obsPyTool工具的模式 1=集成式 2=分布式
Mode = 1
IsMaster = false

```

**执行命令**：
```bash
./run.py 900 1 config.dat
```

**预期结果**：
1. 错误率 < 0.1%%
2. 运行 300 秒后自动结束
3. 总 IOPS = 读 TPS + 写 TPS，其中读 TPS ≈ 2 × 写 TPS
4. 混合 IOPS 介于纯读和纯写之间
5. P99 延迟 < 150ms

---
