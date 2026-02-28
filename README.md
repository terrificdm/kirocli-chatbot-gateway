# KiroCLI Chatbot Gateway

[中文文档](README.zh-CN.md)

Multi-platform chatbot gateway that bridges chat platforms to Kiro CLI via ACP protocol.

## Supported Platforms

| Platform | Status | Description |
|----------|--------|-------------|
| Feishu (Lark) | ✅ Ready | Group chat (@mention) and private chat |
| Discord | 🚧 Planned | Coming soon |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          Gateway                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │   Feishu    │  │   Discord   │  │   (more)    │   Adapters   │
│  │   Adapter   │  │   Adapter   │  │             │              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         │                │                │                      │
│         └────────────────┼────────────────┘                      │
│                          ▼                                       │
│              ┌───────────────────────┐                           │
│              │   Platform Router     │                           │
│              └───────────┬───────────┘                           │
│                          │                                       │
│         ┌────────────────┼────────────────┐                      │
│         ▼                ▼                ▼                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │  kiro-cli   │  │  kiro-cli   │  │  kiro-cli   │  Per-platform│
│  │  (feishu)   │  │  (discord)  │  │   (...)     │  instances   │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

## Features

- **🔌 Multi-Platform**: Single gateway serves multiple chat platforms
- **🔒 Platform Isolation**: Each platform gets its own Kiro CLI instance
- **📁 Flexible Workspace Modes**: `per_chat` (user isolation) or `fixed` (shared project)
- **🔐 Interactive Permission Approval**: User approves sensitive operations (y/n/t)
- **⚡ On-Demand Startup**: Kiro CLI starts only when needed
- **⏱️ Auto Idle Shutdown**: Configurable idle timeout per platform
- **🛑 Cancel Operation**: Send "cancel" to interrupt
- **🔧 MCP & Skills Support**: Global or project-level configuration

## Workspace Modes

This is the most important configuration to understand:

### `per_chat` Mode (Default, Recommended for Multi-User)

```
User A ──→ Session A ──→ /workspace/chat_id_A/
User B ──→ Session B ──→ /workspace/chat_id_B/
User C ──→ Session C ──→ /workspace/chat_id_C/
```

- Each user gets an **isolated subdirectory**
- Users cannot see or modify each other's files
- Kiro CLI loads **global** `~/.kiro/` configuration
- Best for: Public bots, multi-user scenarios

### `fixed` Mode (Recommended for Project Work)

```
User A ──→ Session A ──┐
User B ──→ Session B ──┼──→ /path/to/project/
User C ──→ Session C ──┘
```

- All users share the **same directory**
- Kiro CLI loads **project-level** `.kiro/` configuration
- Best for: Team collaboration on a specific codebase

### MCP & Skills Configuration

| Mode | Config Location | Use Case |
|------|-----------------|----------|
| `per_chat` | `~/.kiro/settings/mcp.json`<br>`~/.kiro/skills/` | Shared tools for all users |
| `fixed` | `{PROJECT}/.kiro/settings/mcp.json`<br>`{PROJECT}/.kiro/skills/` | Project-specific tools |

### Per-Platform Override

Different platforms can use different modes:

```bash
# Global default
KIRO_WORKSPACE_MODE=per_chat

# Override for specific platforms
FEISHU_WORKSPACE_MODE=per_chat   # Public Feishu bot - isolate users
DISCORD_WORKSPACE_MODE=fixed     # Team Discord - shared project
```

## Prerequisites

- Python 3.9+
- [kiro-cli](https://kiro.dev/docs/cli/) installed and logged in
- Platform-specific bot credentials (see below)

## Installation

```bash
cd kirocli-chatbot-gateway
pip install -e .
```

## Configuration

```bash
cp .env.example .env
# Edit .env with your configuration
```

See `.env.example` for detailed configuration options and explanations.

## Platform Setup

### Feishu (Lark)

1. Create app on [Feishu Open Platform](https://open.feishu.cn/app)
2. Enable "Bot" capability
3. Configure permissions:
   - `im:message`, `im:message:send_as_bot`, `im:message:readonly`
   - `im:message.group_at_msg:readonly`, `im:message.p2p_msg:readonly`
   - `im:chat.access_event.bot_p2p_chat:read`, `im:chat.members:bot_access`
   - `im:resource`
4. Event subscription: Enable WebSocket mode, add `im.message.receive_v1`
5. Copy App ID and App Secret to `.env`

### Discord

Coming soon.

## Running

```bash
python main.py
```

## Usage

### Chat Commands

| Platform | Trigger |
|----------|---------|
| Feishu Group | @bot + message |
| Feishu Private | Direct message |
| Discord | Coming soon |

### Slash Commands

| Command | Description |
|---------|-------------|
| `/agent` | List available agents |
| `/agent <name>` | Switch to agent |
| `/model` | List available models |
| `/model <name>` | Switch to model |
| `/help` | Show help |

### Other Commands

| Command | Description |
|---------|-------------|
| `cancel` / `stop` | Cancel current operation |

### Permission Approval

When Kiro needs to perform sensitive operations:

```
🔐 Kiro requests permission:
📋 Creating file: hello.txt
Reply: y(allow) / n(deny) / t(trust)
⏱️ Auto-deny in 60s
```

- **y** / yes / ok - Allow once
- **n** / no - Deny
- **t** / trust / always - Always allow this operation type

## Icon Legend

| Icon | Meaning |
|------|---------|
| 📄 | File read |
| 📝 | File edit |
| ⚡ | Terminal command |
| 🔧 | Other tool |
| ✅ | Success |
| ❌ | Failed |
| ⏳ | In progress |
| 🚫 | Rejected |
| 🔐 | Permission request |

## Project Structure

```
kirocli-chatbot-gateway/
├── main.py              # Entry point
├── gateway.py           # Core gateway logic
├── config.py            # Configuration management
├── acp_client.py        # ACP protocol client
└── adapters/
    ├── __init__.py      # Package exports
    ├── base.py          # ChatAdapter interface
    ├── feishu.py        # Feishu implementation
    └── discord.py       # Discord implementation (stub)
```

## Adding New Platforms

1. Create `adapters/yourplatform.py`
2. Implement `ChatAdapter` interface from `adapters/base.py`
3. Add configuration in `config.py`
4. Register adapter in `main.py`

## License

MIT
