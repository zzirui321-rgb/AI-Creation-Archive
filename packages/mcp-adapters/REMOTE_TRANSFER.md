# A → D：远程传输包与上传客户端

任务 T012，现统一到分支 `feature/a-protocol-mcp` 和 [PR #1](https://github.com/AiCA-Team/AiCA/pull/1)，原远程传输任务分支已被包含。用户选择只做 A；实现包含本地打包、离线校验和 HTTPS 上传客户端。D 的接收服务、认证、存储、查询及图片访问接口尚未实现，下面的 HTTP 接收约定是供 D 评审的候选，不表示已完成双方冻结或部署。

## 使用流程

继续使用既定 Conda `aica` 环境和已安装的本地包，不增加依赖。以下命令从待录制项目目录执行，输出目录须已存在。

```sh
aica bundle --project . --output ../project-transfer.zip
aica verify-bundle ../project-transfer.zip
```

`bundle` 先请求本地录制器导出一致快照，再核对读取字节的哈希。如果期间快照被替换，返回 `snapshot_conflict`，重新执行并使用尚不存在的输出文件名。已存在的输出文件不会被覆盖。打包失败不留下目标文件。

对于已停止录制或从其他机器收到的完整项目副本，可直接读取现有快照，不启动协调器：

```sh
aica bundle --project . --output ../project-transfer.zip --offline
```

先审阅档案内容及附件，再由用户明确确认上传目的地。D 提供准确的 HTTPS 导入地址和平台上传令牌；将令牌放入环境变量 `AICA_UPLOAD_TOKEN`，不要放入命令行、协议事件或 Git。上传命令为：

```sh
aica upload ../project-transfer.zip --url https://platform.example/api/projects/PROJECT/imports --confirm-upload
```

该地址仅为占位示例。`--token-env` 可指定其他环境变量名。上传没有自动开启，不因录制授权或过去的 Git 分享授权而开启。调用该命令的 Agent 必须先获得针对目标和内容的上传授权；确认参数本身不能证明用户是否实际授权。

现有 11 个 MCP 工具保持不变，传输能力由 CLI 和同包 Python API 提供；没有安装自动工具拦截、自动同步或 HTTP MCP。图片必须先通过 `aica_capture_artifact` 登记和复制，仅在项目根生成图片不会使其进入传输包。

## 包格式：aica-transfer/0.1

```text
project-transfer.zip
├── manifest.json
├── aica.json
└── objects/
    └── <sha256>        每种内容只保存一份字节
```

唯一契约位于 [transfer.schema.json](../schema/src/aica_schema/transfer.schema.json)，Python 校验入口为 `aica_schema.transfer.require_transfer`。这是独立的传输封装，不改变 `.aica` 档案协议 `0.1`。JSON 使用严格解析，拒绝重复键。

| 清单字段 | 含义 |
| --- | --- |
| `format` | 固定 `aica-transfer/0.1` |
| `archive.path / sha256 / size` | 原始 `aica.json` 在包内的位置、原始字节哈希和大小 |
| `artifacts[].artifact_id` | 对应原档案中的产物实例 ID |
| `artifacts[].path` | 包内 `objects/<sha256>`，与源电脑路径无关 |
| `artifacts[].sha256 / size` | 实际文件字节的哈希和大小 |

`aica.json` 原始字节和历史附件保持不变，不重写 URI、历史定义、ID 或内容哈希。每个 `artifacts[]` 项都必须在清单中出现一次，包括图片、脚本、日志、事件历史及旧修订附件。相同内容的不同产物实例保留各自 ID，可以共用一个对象文件。

后端读取图片时，先在档案查找产物 ID、MIME 和来源，再用清单查找对应对象；保存到平台管理的存储后建立自己的项目/产物索引。前端向平台请求产物，不能直接用原 `uri` 访问源电脑。需要导出新的可搬运平台档案时，可通过协议已有的 `locations` 机制声明经过核验的远程副本；不要把临时签名 URL 当永久身份。

打包只读取档案及显式登记的附件，不遍历上传 `.aica/`，不打包连接凭证、数据库、锁、队列或本机日志。它也不自动脱敏：档案/已登记附件中原本包含的 prompt、路径或其他敏感文本仍会保留，上传前应审阅。此前全量 Git 分享是单独的历史授权。

所有附件须有非空内容哈希和可核验的本地副本。缺失、哈希不符、只有远程 URL 或越界路径都会阻止构建；可以先将合法副本放入项目并按协议登记 `locations`，再打包。不会自动请求外部 URL。未登记的 `preview_uri` 或文本中的外链不属于被打包对象，不能保证这些外链离线可用。

限制：档案 16 MiB、清单 2 MiB、单附件 64 MiB、最多 4096 个产物、ZIP 及展开内容各不超过 512 MiB。只接受未加密、未压缩的 ZIP，拒绝多余或重复成员、符号链接、非法映射及哈希错误。`verify_bundle` 不解压到文件系统；它验证结构和字节完整性，不执行附件、不保证脚本安全或工作流可运行，也不消除档案本身已有的 `snapshot.gaps`。

## D 待接受的 HTTP 约定

客户端对 **D 提供的完整导入 URL** 发起一次 POST，正文为整个 ZIP 二进制，不是 multipart；无需在 A 侧冻结 URL 路径。HTTPS 校验证书，不跟随重定向，不读取环境代理或 `.netrc`。

```text
Authorization: Bearer <platform-upload-token>
Content-Type: application/vnd.aica.bundle+zip
Content-Length: <ZIP 字节数>
Idempotency-Key: sha256:<整个 ZIP 的 SHA-256>
X-AiCA-Bundle-SHA256: <整个 ZIP 的 SHA-256>
Accept: application/json
```

令牌由平台签发、限定项目权限，不能复用本地协调器令牌或 Session 凭证。档案里的 `metadata.project_id` 是源身份，不能凭该字段获得平台项目权限。相同包可导入不同平台项目，幂等范围至少应包含已认证主体、目标项目和幂等键。

D 接收后需要：

1. 先验证用户与目标项目的导入权限，限制上传大小及资源消耗。
2. 将请求体存入私有临时文件，核对实际 ZIP 哈希与请求头，再调用 `aica_mcp.transfer.verify_bundle(path)`。该接口不启动本地录制器。
3. 根据清单读取经过验证的包内对象，保存原档案和对象，建立平台项目/产物映射。不要直接 `extractall`、信任客户端路径或执行上传脚本。
4. 原子发布完整导入结果，记录幂等回执。重复请求返回同一结果；中途失败不能让前端读到一半导入。
5. 提供受项目权限保护的档案查询和产物访问接口，按档案的 MIME 展示并执行平台内容安全策略。对象哈希或原始 URI 都不是授权凭证。

候选成功回执仅在完整持久化后返回 HTTP **200 或 201**：

```json
{
  "status": "success",
  "format": "aica-transfer/0.1",
  "import_id": "platform-import-id",
  "project_id": "authorized-platform-project-id",
  "bundle_sha256": "<64位ZIP哈希>",
  "archive_sha256": "<64位aica.json原始字节哈希>"
}
```

回执也由同一 Schema 的 `$defs.receipt` 定义。平台项目 ID 可不同于源项目 ID；URL/令牌决定授权目标。客户端校验回执结构和两个哈希，才报告成功。HTTP 202 仅表示待处理，本版不作为成功；如 D 采用异步导入，需先协调增加任务查询契约。错误响应使用 HTTP 非成功状态，客户端不打印远端原始错误体。

网络中断可能发生在服务端已成功但客户端尚未收到回执之后，客户端报告 `upload_unconfirmed`。使用**原来的 ZIP 文件**重试，幂等键保持不变；重新打包的字节及键可能不同。本版重试为整包重传，没有分块续传、自动后台重试、实时事件流或多端合并。

## 验证与剩余验收

单元与 CLI 测试覆盖带图片附件的录制快照、离开源目录后的离线校验、去重、私有状态排除、缺失/损坏/越界附件、恶意 ZIP、上传授权、重复请求键、重定向拒绝及错误回执。上传测试使用 HTTPX MockTransport，未向真实平台发送任何项目数据。

D 需先评审 HTTP 契约并提供部署地址及认证方式，再联调：机器 A 上传真实 Graph 和图片，机器 B 仅通过平台查看，两者不共享本机文件系统或录制数据库。这个双机远程验收尚未完成。

实现依据：[Python ZIP 文档](https://docs.python.org/3.12/library/zipfile.html)、[HTTPX 流式上传文档](https://www.python-httpx.org/advanced/clients/#monitoring-upload-progress)。
