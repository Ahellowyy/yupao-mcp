# 鱼泡求职 MCP

独立 Python 项目，通过鱼泡网页实际使用的接口搜索职位、读取完整招聘描述，向 MCP 客户端返回结构化数据。本版本不依赖逐个点击鱼泡网页。

## 当前可用

| 工具 | 实际行为 |
| --- | --- |
| `yupao_capabilities` | 查看功能状态、支持城市和未启用的写入能力 |
| `yupao_search_jobs` | 按关键词、城市、页码实时请求岗位搜索接口 |
| `yupao_get_job_detail` | 读取完整 JD，返回职位、公司、标签、公开链接及描述 |
| `yupao_check_session` | 检查明确配置的本机登录文件；没有登录文件时返回公开只读模式 |
| `yupao_list_resume_attachments` | 读取本人已有简历的 ID、文件名和审核状态；区分投递与聊天附件，不返回下载地址 |
| `yupao_check_messaging_session` | 验证鱼泡能否返回 IM 登录资料；不返回凭证，不登录 SDK，不发送消息 |
| `yupao_list_conversations` | 登录官方 Chat SDK 并等待同步，读取联系人及 HR 岗位资料；默认排除系统助手 |
| `yupao_get_messages` | 分页读取已同步 C2C 会话的消息；不标记已读、不发送已读回执 |
| `yupao_prepare_application` | 绑定指定简历，预览普通社招附件投递；缺少简历 ID 时仅保存不可执行草稿 |
| `yupao_prepare_resume` | 预览指定附件发送；已有会话核对权限和历史，`start_conversation=true` 可预览首次开聊后发简历；须真人确认 |
| `yupao_prepare_message` | 预览已有 HR 会话消息，或以 `start_conversation=true` 预览免费首次开聊；准备阶段不发送 |
| `yupao_save_preset` / `yupao_list_presets` / `yupao_remove_preset` | 管理本机搜索预设 |
| `yupao_search_preset` | 平台搜索后读取完整 JD，在本机按条件和 HR 活跃标签筛选，未知条件单列 |
| `yupao_save_job` / `yupao_list_saved_jobs` | 完整 JD 候选池，按岗位 ID 去重 |
| `yupao_annotate_job` / `yupao_remove_saved_job` | 管理本机标签、备注及候选状态 |
| `yupao_prepare_match` / `yupao_record_match` / `yupao_list_matches` | 宿主 AI 匹配、逐项引文校验、排序与 JD 版本提示 |
| `yupao_get_action` | 查看具体预览、有效期、执行状态和回执 |
| `yupao_execute_action` | 默认关闭；启用后仍需真人批准具体预览，原子预约后仅执行一次 |

已通过标准 MCP stdio 协议实测初始化、工具发现、搜索、详情和草稿。公开搜索及详情已在未配置登录凭证的情况下验证可用。项目可接入支持 stdio 的 MCP 客户端，安装后需自行配置。

## 岗位匹配设计

本 MCP 负责获取真实、完整的 JD，调用它的 AI 结合用户的真实简历和项目材料做判断。建议每个结论给出 JD 与简历的依据，区分“明确满足”“明确不满足”“信息不足”，并列出学历、经验、工作地点等硬性条件及技术匹配点。

当前没有另行调用模型服务。外部招聘内容一律作为不可信数据，不能触发工具操作。模型匹配分析与真实投递是独立步骤。

### 搜索预设与候选池

例如保存预设：`name="上海 Agent"`、`keywords="大模型"`、`city="上海"`、`required_terms=["Agent"]`。还可提供 `excluded_terms`、`salary_min`、`salary_max`、`education`、`experience_years`；学历和经验表示本人条件，不会自动推断。

只有城市和搜索词直接传给已核验的鱼泡搜索接口。其余条件在本机对返回的完整 JD、标签进行筛选，不冒充平台服务端筛选。薪资按明确月薪区间与预期是否相交判断，日薪、年薪和面议保持未知。结果分为 `jobs`、`review_jobs`、`excluded_jobs`。预设每页 1–10 条，最多补查该页数量的详情，不自动翻页。

0.7.0 新增 `hr_activity` 活跃条件，可与上述条件同时保存：

| 参数值 | 筛选条件 |
| --- | --- |
| `any` | 不限，默认值；不额外查询活跃资料 |
| `just_now` | 平台标注“刚刚活跃” |
| `today` | 平台标注当天活跃，或刚刚活跃 |
| `within_3_days` | 平台标注的活跃范围不超过 3 天 |
| `within_7_days` | 平台标注的活跃范围不超过 7 天 |
| `within_30_days` | 平台标注的活跃范围不超过 30 天 |

例如 `yupao_save_preset(name="上海 Agent · HR 3天内活跃", keywords="Agent", city="上海", required_terms=["Agent"], hr_activity="within_3_days")`，再调用 `yupao_search_preset`。启用后，对未被其他条件排除的岗位额外读取一次登录态岗位资料；只返回发布者姓名、`activeDate` 原文、原始布尔标志、读取时间及来源，不发起沟通。结果中的 `hr_activity` 保留这些依据，`hr_activity_requests` 表示尝试读取次数，旧预设默认不限。

这是按平台标签范围判断，不推算精确最后在线时间：例如“3天内活跃”可通过 7 天条件，“昨日活跃”或“前天活跃”可通过 3 天条件，“4个月内活跃”不通过 3 天条件；后者不证明 HR 实际已离线四个月。“1天内”不能保证是当天，“1个月内”不能保证不超过 30 天，因此不通过相应更窄的条件。`isActive` 布尔值不单独作为活跃时间依据。缺失标签、未知标签、“当前未在线”、缺少登录或活跃资料结构变化时，其他条件符合的岗位进入 `review_jobs`；平台拒绝、限流或网络错误仍立即停止，不重试。

候选池保存在 `YUPAO_DATA_DIR/workspace.sqlite3`（默认 `.local`），保留完整 JD、标签、备注、抓取时间及摘要。重复保存按 `job_biz_id` 更新，保留原有标签与备注。状态为 `candidate`、`priority`、`excluded`、`archived`，这些是本机整理状态，不代表平台已经投递。

### 宿主 AI 匹配

1. 调用 `yupao_prepare_match(job_biz_id, resume_text, profile_name)`，保存完整 JD 和本人明确提供的材料。
2. 当前调用 MCP 的 AI 阅读两份材料，按技能、业务场景、学历、年限等逐项判断。
3. 调用 `yupao_record_match(match_input_id, analysis)` 保存结论；程序校验每项 JD 和简历引文确实存在于对应快照中。
4. `yupao_list_matches` 读取排序结果，并标出候选池 JD 更新后旧分析是否已过时。

`analysis` 含 `summary`、`model`、`criteria`。每项格式：

```json
{
  "requirement": "Python 与 Agent 开发",
  "weight": 5,
  "hard": true,
  "status": "satisfied",
  "jd_quote": "来自该 JD 的逐字引文",
  "resume_quote": "来自本人材料的逐字引文",
  "explanation": "两段依据如何对应"
}
```

状态为 `satisfied`、`not_satisfied`、`unknown`。明确满足和不满足均需简历证据，简历未提到只能标未知。程序只校验引文存在性，语义判断由宿主模型负责；这不证明引文能支持结论的全部细节。分数为所选判断项的加权满足比例，`evidence_coverage` 为其中有明确判断的权重比例，均不是录用概率。硬条件明确不满足时建议 `not_recommended`，硬条件未知时保持 `review`。不会自动改写简历或执行投递。

## 运行与接入

需要 Python 3.11+。在项目目录创建虚拟环境并安装（Windows PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

以 stdio 模式启动：

```powershell
.\.venv\Scripts\python.exe -m yupao_mcp
```

MCP 客户端应将 `command` 配置为本目录 `.venv/Scripts/python.exe` 的绝对路径，`args` 为 `["-m", "yupao_mcp"]`。Windows 示例见 `mcp-client.example.json`，请将 `C:\\path\\to\\yupao-mcp` 替换为实际项目目录。macOS/Linux 对应使用 `.venv/bin/python`。只在本机运行；stdout 用于 MCP 协议，日志写 stderr。

也可以使用 uv 安装：

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
```

支持城市：全国、北京、上海、广州、深圳、杭州、成都、南京、苏州、武汉。目前实际联网验证了上海。每页 1–30 条，页码 1–10，关键词最多 100 字。请求有固定间隔，不自动重试。平台搜索相关性不保证每条都符合岗位方向，返回数据后还需分析与筛选。

### 可选聊天 SDK 接入

搜索和简历元数据不需要此依赖。读取会话和聊天记录时，在项目目录安装可选依赖，并下载固定版本的腾讯云官方浏览器 SDK：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[chat,dev]"
.\.venv\Scripts\python.exe -m yupao_mcp.setup_chat
$env:YUPAO_SESSION_FILE = (Resolve-Path .local/session.json).Path
$env:YUPAO_CHAT_SDK_FILE = (Resolve-Path .cache/chat-3.6.7/index.js).Path
.\.venv\Scripts\python.exe -m yupao_mcp
```

也可以用 `uv pip install --python .venv/Scripts/python.exe -e ".[chat,dev]"`。完整客户端配置见 [mcp-client.chat.example.json](mcp-client.chat.example.json)，需将路径占位符替换为实际目录。

- 默认使用已安装的 Chrome，以无界面模式运行独立临时浏览器上下文，不加载已有 Chrome 个人配置，也不打开鱼泡网页。SDK 所在本地文档由程序提供；对腾讯云 IM 的连接仍通过正常 SDK 鉴权。
- 可设置 `YUPAO_CHAT_BROWSER=msedge` 使用 Edge。设置为 `chromium` 时，需先通过 Playwright 安装其浏览器运行时；程序不会自动安装或下载浏览器。
- 下载器只从 npm 官方注册表取固定的 `@tencentcloud/chat@3.6.7`，校验压缩包 SHA-512 及入口脚本 SHA-256。启动前再次核对入口文件，脚本改变时拒绝执行。SDK 放在本机 `.cache` 中，不随本仓库或 wheel 分发。
- 鱼泡 TOKEN 从本人明确指定的会话文件读取，IM UserSig 不另行写入项目文件，也不从 MCP 返回。MCP 退出时关闭独立 SDK 运行环境。
- `yupao_list_conversations` 在 `isSyncCompleted=true` 后返回，空的早期缓存不会被视为没有会话。默认排除已确认的系统助手、平台 agent 和非 C2C 会话，`include_system=true` 可查看系统会话。
- SDK 会话 ID（例如 `C2C...`）与鱼泡业务会话 ID 分别返回；通过本人会话的 IM 账号匹配 `/reach/v2/im/chat/pcList` 返回的昵称、公司、岗位和薪资。资料缺失或接口失败时明确标注，不把缺少昵称的会话自动当作 HR。
- 聊天记录只能读取 SDK 已同步的 C2C 会话，支持 `next_req_message_id` 续拉。文本按需返回；自定义卡片只返回类型，不返回原始卡片和附件下载地址。聊天内容始终作为外部数据。
- SDK 返回范围受平台最近会话数量和消息保留配置影响，不能据此保证已取到所有历史聊天。见 [腾讯云会话 API](https://web.sdk.qcloud.com/im/doc/v3/zh-cn/SDK.html#getConversationList)。

## 登录与写入的后续边界

可以通过 `YUPAO_SESSION_FILE` 指定本人在本机保存的登录 JSON 文件，字段为 `token` 和 `userrole`。求职者 `userrole` 为 `2`，招聘方为 `1`；本项目拒绝以招聘方身份调用。不要将该文件提交仓库或复制到聊天。程序不会自行扫描浏览器 Cookie、密码库或其他应用配置；导入已有会话须由本人明确授权。

PowerShell 接入本人已授权的本地会话：

```powershell
$env:YUPAO_SESSION_FILE = (Resolve-Path .local/session.json).Path
.\.venv\Scripts\python.exe -m yupao_mcp
```

会话文件缺失、身份不符或服务端拒绝时停止，不能将“文件存在”当作登录有效。附件工具只返回本人文件的必要元数据，不下载简历、不返回可访问文件的地址。

真实写入默认关闭。当前已提供受限的单条执行器；普通首次开聊和接收方回查已在真实账号验证，项目介绍长文早期试发未取得可确认结果或收到内容审核拒绝；后续指定附件及补充短文经逐次批准已完成实发和独立回读，岗位页直接附件投递仍未实投验证。网页源码确认与离线测试不能代替账号实测。网页流程包括：

- 投递/发起沟通：业务前置检查、`/job/v3/contact/job/im/call`、`/reach/v2/im/chat/init` 等路径；部分岗位使用 `/resume-delivery/v1/delivery/submit`。这些是不同岗位类型的路径，不能一律混用。
- 文本消息：网页使用腾讯云 Chat SDK，`createTextMessage` 后调用 `sendMessage`，并请求已读回执。本人登录后还需要通过鱼泡获取 SDK 登录资料和 UserSig；不能使用管理员 REST 密钥替代。
- 实际执行前核对本人登录状态、岗位类型、简历选择和接收方，获得对当前具体预览的真人批准。原子预约、确认摘要、去重记录和执行回执保存在本机 SQLite 中。

### 单条确认与执行

默认 `YUPAO_ENABLE_WRITES` 不设置，执行工具返回 `WRITES_DISABLED`，不会触发平台写入。确实需要执行时可配置 `YUPAO_ENABLE_WRITES=1`，这只是启用执行器，并不构成对任何目标的批准。

- **已有会话消息**：`yupao_prepare_message(job_biz_id, text, conversation_id)`，校验 SDK 会话、鱼泡业务会话和岗位关联。
- **首次沟通消息**：使用 `start_conversation=true` 且不传已有会话 ID。预览公司、岗位、发布者、完整正文及平台可能生成的标准招呼。执行器按普通“立即沟通”分支建立业务会话，再回查接收方与岗位关联，最后才通过 SDK 发送这一条正文；不主动交换电话或微信。无法核实接收方时停止，保留已经开聊的事实，不自动重新开聊。
- **指定附件投递**：`yupao_prepare_application(job_biz_id, resume_file_id)`，只允许已核验的普通白领社招岗位、审核通过的本人附件、明确在招状态、平台附件权限及免费额度。校招、聚合、代理、其他租户、权限不明和付费路径均停止，不混用接口。
- **已有会话发送附件简历**：先用 `yupao_list_resume_attachments(for_chat=true)` 选择本人审核通过的文件，再调用 `yupao_prepare_resume(job_biz_id, conversation_id, resume_file_id)`。准备阶段只读取并保存预览，不发送附件。执行仍使用 `yupao_execute_action` 的逐次真人确认；不新建会话、不附加文本、不支付或自动解锁。核对 SDK 会话、业务接收方及岗位，检查 `sendMsg` 聊天权益与 `exchangeResumeFile` 入口权限。与网页工具栏一致，仅允许附件权限状态 1 或 3。
- **首次开聊后发附件**（0.6.0）：`yupao_prepare_resume(job_biz_id, resume_file_id=..., start_conversation=true)`，不传已有会话 ID。预览绑定具体发布者、岗位与附件，并明确平台可能生成标准招呼或职位卡片。确认后按免费普通沟通路径先开聊，回查接收方和岗位，再检查会话附件权限及完整可读历史，最后仅调用一次附件业务接口。不使用岗位页的附件投递权限冒充新会话的附件发送权限；开聊后附件权限不允许、接收方无法核实或已有附件记录时停止，不自动重开或重试。回执保留已开聊的事实及 `resume_send_invoked`，不会将开聊成功当作简历发送成功。预览本身不发起沟通。
- 不传会话 ID、不选择首次沟通，也不传附件 ID时，兼容旧版不可执行的纯本地草稿。

生成的预览有效期 30 分钟，绑定账号、操作类型、接收方、岗位快照、正文或简历及其 SHA-256 摘要。调用 `yupao_execute_action(action_id)` 后，MCP 宿主须把 `elicitation` 确认弹窗转交真人，不能自动接受或由 Agent 代答。工具没有 `yes=true` 或修改正文参数。

宿主不支持确认弹窗时，工具拒绝执行。本人可在交互终端核对完整预览并输入指定确认短语：

```powershell
.\.venv\Scripts\python.exe -m yupao_mcp.actions show <action_id>
.\.venv\Scripts\python.exe -m yupao_mcp.actions approve <action_id>
```

批准命令不发送任何东西，不接受管道输入或自动批准参数。批准后两分钟内可调用执行工具；每次批准只允许一次原子预约。重新登录到其他账号、修改目标/正文/简历或改变岗位快照均不能沿用旧批准。取消或拒绝确认不会发送。

执行状态为 `awaiting_confirmation` → `approved` → `dispatching` → `sent` / `submitted` / `outcome_unknown`。SDK 返回消息 ID 后还需回读相同接收方和正文；附件投递需回查平台投递状态。受理、确认出现在历史、送达、已读分别记录，不混为一谈。超时、进程中断或结果不明时保留预约，不自动重发；已有受理回执也不会因后续读取失败而丢失。

明确的服务端内容拒绝另记为 `rejected`。0.4.2 识别腾讯云 `80001`，返回 `MESSAGE_CONTENT_REJECTED`，保留 SDK 错误码与计划消息 ID，不再把明确拒绝仅描述为未知。相同正文的拒绝记录会阻止重复创建；程序不自动修改文案重试。错误码说明见 [腾讯云官方文档](https://intl.cloud.tencent.com/zh/document/product/1047/34348)。该错误不提供具体命中的文字或链接，不能擅自推断触发项。

0.4.1 修复：真实环境复现了 `SDK_READY` 已触发而 `isSyncCompleted=false` 的启动阶段。发送路径现与读取路径一样，最多等待 15 秒让会话同步完成，再检查接收方及创建消息；同步未完成时不调用 `sendMessage`。失败回执保留脱敏 SDK 错误码、`sdk_send_invoked` 和可用的计划消息 ID，便于区分本地拦截和发送调用后的结果未知。此修复不会清除旧的未知结果记录，回查未发现正文也不会自行重发。

### 消息与简历流程核对

0.5.0 新增已有会话附件发送。使用鱼泡业务接口 `applyExchange`，由平台生成 `710.1` 简历卡片；不使用通用文件消息代替。准备与执行前均分页检查 SDK 当前保留的历史（最多 20 页，未完成则停止），已有相同附件或待处理请求时禁止重复发送，也覆盖用户手动发送的记录。执行后回查新的、发向所选 HR 且文件 ID 相同的简历卡片：`subType=3` 记录为 `sent` / `attachment_shared`；`subType=1` 仅记录为 `submitted` / `awaiting_peer_consent`，不声称附件已经送出。没有匹配卡片、回读失败或超时时记为 `outcome_unknown`，保留受理信息并禁止自动重试。历史核对受平台消息保留范围限制，不代表全生命周期消息。聊天读取仅新增文件 ID、名称、交换消息 ID 和子类型，不返回下载地址。

下表的“源码确认”表示在鱼泡公开前端中核对过调用关系和参数，并不表示已对真实 HR 执行过。鉴权只读接口已另行通过本人授权的求职者会话验证。

| 能力 | 接口或 SDK 流程 | 当前验证范围 |
| --- | --- | --- |
| 本人投递附件列表 | `POST /resume/v3/attach/myAttach` | 已联网验证并接入 MCP；读取 `data.fileList` |
| 本人聊天附件列表 | `POST /resume/v3/attach/myImAttach` | 已联网验证并接入 MCP；读取 `data.fileList` |
| IM 登录资料 | `POST /reach/v2/im/account/getImToken` | 已联网验证有 `imAccount`、`imToken`，仅返回可用状态 |
| 会话及聊天记录 | Chat SDK `getConversationList`、`getMessageList` | 已完成 SDK 登录、同步及 MCP 联网读取验证；未发送已读回执 |
| HR 对应的岗位资料 | `POST /reach/v2/im/chat/pcList` | 已验证非空会话的姓名、公司、岗位、薪资和业务会话关联 |
| 业务会话接收方核对 | `POST /reach/v2/im/chat/detailV2` → `pcList` | 已用现有会话验证接收方和岗位身份；不会据此自动发送 |
| 发送文本 | Chat SDK `createTextMessage` → `sendMessage` | 已经明确批准实发补充短文，并独立回读核对消息 ID、正文和接收人；不代表任意正文都能通过审核 |
| 普通首次沟通 | `im/preCheck` → `im/call` → `chat/init` | 已完成真实开聊及业务会话、岗位接收方核对；平台自动招呼不等于自定义正文已发出 |
| 普通社招附件投递 | 前置检查 → 联系 → `chat/init`，`postType=1` | 已实现权限检查与回读；真实投递待明确批准 |
| 聊天中发送附件简历 | `POST /reach/v2/im/exchange/applyExchange` | 已明确批准实发，包含首次开聊后发送指定附件，并独立回查相同文件 ID 的成功卡片；不代表已读或下载 |
| 处理对方的附件简历交换请求 | `POST /reach/v2/im/exchange/handleExchange` | 源码确认；尚未发送实测 |
| 面试邀请后补发附件 | `POST /reach/v2/im/invite/sendResumeFile` | 源码确认；只适用于对应邀请流程 |
| 某些岗位类型的正式投递 | `/resume-delivery/v1/delivery/preSubmit`、`requirement`、`submit` | 源码确认；尚未投递实测 |
| 上传新的附件简历 | 上传组件 → `/resume/v3/attach/publishAnalysis` 或 `addSync` 等 | 尚未核对完整上传凭证、存储与绑定链路；未实现 |

调用约束：

- IM 登录：将鱼泡返回的 `imAccount` 用作 SDK `userID`，`imToken` 用作 `userSig`；获取资料不等于 SDK 登录成功，必须等待 SDK 就绪。生产环境 SDKAppID 来源为公开前端的环境配置，不能使用开发者自己新建的 IM 应用替代。
- 文本：网页使用 C2C 会话，接收方为对方的 IM 账号，并设置 `needReadReceipt=true`。成功要核对 SDK 返回的消息 ID、接收方及内容，并回查记录；发送受理、送达、已读是不同状态。
- 主动交换附件：参数为 `{conversationId, exchangeType: "EXCHANGE_RESUME_FILE", extData: {resumeFileId}}`。这里的业务 `conversationId` 不能直接拿腾讯云的 `conversationID` 或岗位 ID 替代。需要确认所选文件属于本人，并核对平台审核状态和该会话的业务权限。
- 接受附件请求：使用 `exchangeMsgId`、`agree`、`exchangeType` 和所选附件，不能当作主动发送流程调用。
- 面试邀请：`{inviteId, fileId}` 只适用于对应邀请，不是通用发简历接口。
- 校招等投递分支的源码参数包括 `jobIds`、`resumeSubUuid`、`infoKind`、`type`、`infoId`。必须按真实岗位类型走相应分支，不能只传一个 job ID。
- `chat/init`、`im/call`、投递预检查和 `applyExchange` 不在只读工具的允许列表中，只能由获确认的单条执行器调用。`handleExchange` 与面试邀请附件接口尚未接入执行器；不能为了探测参数而对真实招聘方执行。

腾讯云 SDK 官方参考：[消息发送](https://cloud.tencent.com/document/product/269/75316)、[客户端 API](https://cloud.tencent.com/document/product/269/37411)。SDK 支持通用文件消息，不代表鱼泡接受用通用文件消息代替业务简历交换流程。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest-run
# JavaScript 适配层的离线测试（开发环境需 Node.js）：
node --test tests/chat_reader.test.cjs
# 显式联网，只读搜索、详情及本地草稿，不投递或发送：
.\.venv\Scripts\python.exe -X utf8 tests/smoke_stdio.py
# 需先明确授权并配置 YUPAO_SESSION_FILE；仅核验账号、简历列表和 IM 资料：
.\.venv\Scripts\python.exe -X utf8 tests/smoke_authenticated.py
# 同样需要已授权登录；真实搜索及HR活跃条件筛选，不投递或开聊：
.\.venv\Scripts\python.exe -X utf8 tests/smoke_activity.py
# 已安装 chat 可选依赖、下载 SDK 并明确授权读取当前账号的会话后：
.\.venv\Scripts\python.exe -X utf8 tests/smoke_chat.py
# 本人明确指定项目/简历文本文件，只读搜索、本机存储与匹配材料准备：
$env:YUPAO_MATCH_TEXT_FILE = "C:\path\to\private-profile.txt"
.\.venv\Scripts\python.exe -X utf8 tests/smoke_workflows.py
```

2026-10-30：MCP 提供 24 个工具，172 项 Python 测试、11 项 JavaScript 测试通过。真实只读验证覆盖搜索、完整 JD、账号、附件、SDK 会话与消息、本地预设筛选、候选池、宿主 AI 分析保存和消息预览；写入关闭时执行工具确实返回拒绝。经明确批准的首次真实尝试验证了开聊及接收方回查，但正文发送结果仍未确认；后续经单独批准的正文试发明确收到内容审核拒绝 80001，独立完整历史回查未发现正文或计划消息 ID；拒绝已记录，不继续重试。新增聊天附件功能已通过 MCP 真实只读预览验证：能识别既有附件卡片并阻止重复发送，关闭写入时执行工具返回 WRITES_DISABLED；后续另经逐次明确批准，已完成首次开聊后发送指定附件，以及补发项目演示说明短文，两者均独立核对消息或文件 ID、接收方和成功状态；这不等于对方已读或下载。岗位页直接附件投递仍未实测。离线回归另覆盖 SDK_READY 之后的会话同步等待和发送调用阶段记录。

联网验证脚本会将本机结果保存到 `.local/live-smoke-result.json`、`.local/authenticated-mcp-smoke.json`、`.local/chat-mcp-result.json`。会话名单与消息仅保存在本机，不随仓库分发。账号文件、草稿、虚拟环境和缓存均在 `.gitignore` 中排除。

## 接口来源与限制

接口依据为当日鱼泡官方网页加载的公开构建 `1029202661437` / release `9.40.0`，主要模块为 `70859`（请求格式）、`21550`（HTTP 客户端）和岗位推荐页中的搜索参数构造。

- 搜索：`POST https://yupao-prod.yupaowang.com/job/v2/search/job/search`
- 公开详情：`POST https://yupao-prod.yupaowang.com/job/v3/detail/infoSeo`

请求格式按公开客户端实现独立改写，含嵌套对象排序、timestamp、nonce 与摘要；这是普通请求格式，不代替账号认证。没有修改网站脚本或关闭安全检查。公开前端版本变动可能影响兼容性；接口返回失败、验证或限流时停止，不能将空数据或缓存冒充实时成功。

## 开源许可

本项目代码采用 [MIT License](LICENSE)。这是独立社区项目，与鱼泡官方无隶属关系。鱼泡名称、服务和返回的招聘内容不属于本项目的授权范围。
