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

1. 在[飞书开放平台](https://open.feishu.cn/app)创建应用
2. 启用"机器人"能力
3. 配置权限：
   - `im:message`、`im:message:send_as_bot`、`im:message:readonly`
   - `im:message.group_at_msg:readonly`、`im:message.p2p_msg:readonly`
   - `im:chat.access_event.bot_p2p_chat:read`、`im:chat.members:bot_access`
   - `im:resource`
4. 事件订阅：启用 WebSocket 方式，添加 `im.message.receive_v1`
5. 将 App ID 和 App Secret 填入 `.env`

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

## 许可证

MIT
