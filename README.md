# 鱼泡求职 MCP

独立 Python 项目，通过鱼泡网页实际使用的接口搜索职位、读取完整招聘描述，向 MCP 客户端返回结构化数据。本版本不依赖逐个点击鱼泡网页。

## 当前可用

| 工具 | 实际行为 |
| --- | --- |
| `yupao_capabilities` | 查看功能状态、支持城市和未启用的写入能力 |
| `yupao_search_jobs` | 按关键词、城市、页码实时请求岗位搜索接口 |
| `yupao_get_job_detail` | 读取完整 JD，返回职位、公司、标签、公开链接及描述 |
| `yupao_check_session` | 检查明确配置的本机登录文件；没有登录文件时返回公开只读模式 |
| `yupao_prepare_application` | 保存本地投递草稿；不投递、不上传简历 |
| `yupao_prepare_message` | 保存本地消息草稿；不创建远端聊天、不发送消息 |

已通过标准 MCP stdio 协议实测初始化、工具发现、搜索、详情和草稿。公开搜索及详情已在未配置登录凭证的情况下验证可用。项目可接入支持 stdio 的 MCP 客户端，安装后需自行配置。

## 岗位匹配设计

本 MCP 负责获取真实、完整的 JD，调用它的 AI 结合用户的真实简历和项目材料做判断。建议每个结论给出 JD 与简历的依据，区分“明确满足”“明确不满足”“信息不足”，并列出学历、经验、工作地点等硬性条件及技术匹配点。

第一版没有内置关键词评分器，也没有另行调用模型服务。外部招聘内容一律作为不可信数据，不能触发工具操作。模型匹配分析与真实投递是独立步骤。

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

## 登录与写入的后续边界

可以通过 `YUPAO_SESSION_FILE` 指定本人在本机保存的登录 JSON 文件，字段为 `token` 和 `userrole`。不要将该文件提交仓库或复制到聊天。程序不会自行扫描浏览器 Cookie、密码库或其他应用配置。

目前实际投递和消息发送没有启用，也没有暴露可执行远端写入的 MCP 工具。网页源码中已确认的流程：

- 投递/发起沟通：业务前置检查、`/job/v3/contact/job/im/call`、`/reach/v2/im/chat/init` 等路径；部分岗位使用 `/resume-delivery/v1/delivery/submit`。这些是不同岗位类型的路径，不能一律混用。
- 文本消息：网页使用腾讯云 Chat SDK，`createTextMessage` 后调用 `sendMessage`，并请求已读回执。本人登录后还需要通过鱼泡获取 SDK 登录资料和 UserSig；不能使用管理员 REST 密钥替代。
- 开启实际写入前，需要核对本人登录状态、岗位类型、简历选择和接收方，完成明确授权的单条试验，并增加持久化去重、业务回执及历史回查。当前只创建本地草稿，不把草稿当作投递成功。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .tmp/pytest-run
# 显式联网，只读搜索、详情及本地草稿，不投递或发送：
.\.venv\Scripts\python.exe -X utf8 tests/smoke_stdio.py
```

2026-10-30 实测：9 项单元测试通过；MCP 协议调用返回 3 条上海“大模型”相关结果，读取到 `agent/大模型算法工程师` 的 468 字 JD，并成功生成本地草稿。实际投递 0 次，实际发送 0 条。

联网验证脚本会将本机结果保存到 `.local/live-smoke-result.json`，该文件不随仓库分发。账号文件、草稿、虚拟环境和缓存均在 `.gitignore` 中排除。

## 接口来源与限制

接口依据为当日鱼泡官方网页加载的公开构建 `1029202661437` / release `9.40.0`，主要模块为 `70859`（请求格式）、`21550`（HTTP 客户端）和岗位推荐页中的搜索参数构造。

- 搜索：`POST https://yupao-prod.yupaowang.com/job/v2/search/job/search`
- 公开详情：`POST https://yupao-prod.yupaowang.com/job/v3/detail/infoSeo`

请求格式按公开客户端实现独立改写，含嵌套对象排序、timestamp、nonce 与摘要；这是普通请求格式，不代替账号认证。没有修改网站脚本或关闭安全检查。公开前端版本变动可能影响兼容性；接口返回失败、验证或限流时停止，不能将空数据或缓存冒充实时成功。

## 开源许可

本项目代码采用 [MIT License](LICENSE)。这是独立社区项目，与鱼泡官方无隶属关系。鱼泡名称、服务和返回的招聘内容不属于本项目的授权范围。
