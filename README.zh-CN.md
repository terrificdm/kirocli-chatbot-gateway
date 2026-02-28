# KiroCLI 聊天机器人网关

多平台聊天机器人网关，通过 ACP 协议连接各种聊天平台到 Kiro CLI。

## 支持的平台

| 平台 | 状态 | 说明 |
|------|------|------|
| 飞书 | ✅ 可用 | 群聊（@机器人）和私聊 |
| Discord | 🚧 开发中 | 即将支持 |

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                          Gateway                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │    飞书     │  │   Discord   │  │   (更多)    │   适配器     │
│  │   Adapter   │  │   Adapter   │  │             │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         │                │                │                      │
│         └────────────────┼────────────────┘                      │
│                          ▼                                       │
│              ┌───────────────────────┐                           │
│              │      平台路由器        │                           │
│              └───────────┬───────────┘                           │
│                          │                                       │
│         ┌────────────────┼────────────────┐                      │
│         ▼                ▼                ▼                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  kiro-cli   │  │  kiro-cli   │  │  kiro-cli   │  每平台独立   │
│  │   (飞书)    │  │  (discord)  │  │   (...)     │  实例        │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

## 功能特性

- **🔌 多平台支持**：一个网关服务多个聊天平台
- **🔒 平台隔离**：每个平台独立的 Kiro CLI 实例
- **📁 灵活的工作空间模式**：`per_chat`（用户隔离）或 `fixed`（共享项目）
- **🔐 交互式权限审批**：敏感操作需用户确认（y/n/t）
- **⚡ 按需启动**：仅在收到消息时启动 Kiro CLI
- **⏱️ 空闲自动关闭**：可配置的空闲超时
- **🛑 取消操作**：发送 "cancel" 中断当前操作
- **🔧 MCP 和 Skills 支持**：全局或项目级配置

## 工作空间模式（重要！）

这是最重要的配置，请仔细理解：

### `per_chat` 模式（默认，推荐多用户场景）

```
用户 A ──→ 会话 A ──→ /workspace/chat_id_A/
用户 B ──→ 会话 B ──→ /workspace/chat_id_B/
用户 C ──→ 会话 C ──→ /workspace/chat_id_C/
```

- 每个用户获得**独立的子目录**
- 用户之间无法看到或修改彼此的文件
- Kiro CLI 加载**全局** `~/.kiro/` 配置
- 适用于：公开机器人、多用户场景

### `fixed` 模式（推荐项目协作场景）

```
用户 A ──→ 会话 A ──┐
用户 B ──→ 会话 B ──┼──→ /path/to/project/
用户 C ──→ 会话 C ──┘
```

- 所有用户共享**同一个目录**
- Kiro CLI 加载**项目级** `.kiro/` 配置
- 适用于：团队协作、特定代码库

### MCP 和 Skills 配置位置

| 模式 | 配置位置 | 使用场景 |
|------|----------|----------|
| `per_chat` | `~/.kiro/settings/mcp.json`<br>`~/.kiro/skills/` | 所有用户共享的工具 |
| `fixed` | `{项目}/.kiro/settings/mcp.json`<br>`{项目}/.kiro/skills/` | 项目专用工具 |

### 按平台覆盖配置

不同平台可以使用不同模式：

```bash
# 全局默认
KIRO_WORKSPACE_MODE=per_chat

# 针对特定平台覆盖
FEISHU_WORKSPACE_MODE=per_chat   # 公开飞书机器人 - 隔离用户
DISCORD_WORKSPACE_MODE=fixed     # 团队 Discord - 共享项目
```

## 前置要求

- Python 3.9+
- [kiro-cli](https://kiro.dev/docs/cli/) 已安装并登录
- 各平台的机器人凭证

## 安装

```bash
cd kirocli-chatbot-gateway
pip install -e .
```

## 配置

```bash
cp .env.example .env
# 编辑 .env 填入你的配置
```

详细配置选项请查看 `.env.example` 文件中的注释说明。

## 平台配置

### 飞书

1. 在[飞书开放平台](https://open.feishu.cn/app)创建企业自建应用
   - 点击**创建企业自建应用**
   - 填写应用名称和描述

2. 获取凭证：在**凭证与基础信息**中，复制 **App ID**（格式：`cli_xxx`）和 **App Secret** 到 `.env` 文件

3. 添加"机器人"能力：在**应用能力** > **机器人**中启用机器人 — `.env` 中的 `FEISHU_BOT_NAME` 必须与飞书中机器人的显示名称一致（通常与应用名称相同）

4. 配置权限（可在飞书开放平台权限页面批量导入）：
   - `im:message` - 读写消息（基础权限）
   - `im:message:send_as_bot` - 以机器人身份发送消息
   - `im:message:readonly` - 读取消息历史
   - `im:message.group_at_msg:readonly` - 接收群 @消息
   - `im:message.p2p_msg:readonly` - 接收私聊消息
   - `im:chat.access_event.bot_p2p_chat:read` - 私聊事件
   - `im:chat.members:bot_access` - 机器人群成员访问
   - `im:resource` - 访问消息资源（图片、文件等）

   <details>
   <summary>批量导入 JSON</summary>

   ```json
   {
     "scopes": {
       "tenant": [
         "im:message",
         "im:message:send_as_bot",
         "im:message:readonly",
         "im:message.group_at_msg:readonly",
         "im:message.p2p_msg:readonly",
         "im:chat.access_event.bot_p2p_chat:read",
         "im:chat.members:bot_access",
         "im:resource"
       ],
       "user": []
     }
   }
   ```

   </details>

5. 先启动机器人（保存事件订阅需要先建立连接）：
   ```bash
   python main.py
   ```
   此时机器人只连接飞书 WebSocket，还不会收到消息，但下一步需要这个连接。

6. 事件订阅：在**事件订阅**中，选择**使用长连接接收事件**（WebSocket）— 无需公网 Webhook URL
   - 添加事件：`im.message.receive_v1`

7. 发布应用：在**版本管理与发布**中，创建版本并发布
   - 企业自建应用通常自动审批
   - 权限变更需要发布新版本才能生效

### Discord

即将支持。

## 运行

```bash
python main.py
```

## 使用方法

### 触发方式

| 平台 | 触发方式 |
|------|----------|
| 飞书群聊 | @机器人 + 消息 |
| 飞书私聊 | 直接发送消息 |
| Discord | 即将支持 |

### 斜杠命令

| 命令 | 说明 |
|------|------|
| `/agent` | 列出可用的 Agent |
| `/agent <名称>` | 切换 Agent |
| `/model` | 列出可用的模型 |
| `/model <名称>` | 切换模型 |
| `/help` | 显示帮助 |

### 其他命令

| 命令 | 说明 |
|------|------|
| `cancel` / `stop` | 取消当前操作 |

### 权限审批

当 Kiro 需要执行敏感操作时：

```
🔐 Kiro 请求权限：
📋 创建文件：hello.txt
回复：y(允许) / n(拒绝) / t(信任)
⏱️ 60秒后自动拒绝
```

- **y** / yes / ok - 允许一次
- **n** / no - 拒绝
- **t** / trust / always - 本会话始终允许此类操作

## 图标说明

| 图标 | 含义 |
|------|------|
| 📄 | 读取文件 |
| 📝 | 编辑文件 |
| ⚡ | 执行命令 |
| 🔧 | 其他工具 |
| ✅ | 成功 |
| ❌ | 失败 |
| ⏳ | 进行中 |
| 🚫 | 已拒绝 |
| 🔐 | 权限请求 |

## 项目结构

```
kirocli-chatbot-gateway/
├── main.py              # 入口
├── gateway.py           # 核心网关逻辑
├── config.py            # 配置管理
├── acp_client.py        # ACP 协议客户端
└── adapters/
    ├── __init__.py      # 包导出
    ├── base.py          # ChatAdapter 接口
    ├── feishu.py        # 飞书实现
    └── discord.py       # Discord 实现（待完成）
```

## 添加新平台

1. 创建 `adapters/yourplatform.py`
2. 实现 `adapters/base.py` 中的 `ChatAdapter` 接口
3. 在 `config.py` 中添加配置
4. 在 `main.py` 中注册适配器
