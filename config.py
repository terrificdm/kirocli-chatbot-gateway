"""Configuration management for kirocli-chatbot-gateway."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


def _parse_workspace_mode(value: str | None, default: str = "per_chat") -> str:
    """Parse and validate workspace_mode value."""
    if not value:
        return default
    value = value.lower().strip()
    return value if value in ("fixed", "per_chat") else default


@dataclass
class FeishuConfig:
    """Feishu adapter configuration."""
    enabled: bool = False
    app_id: str = ""
    app_secret: str = ""
    bot_name: str = ""
    kiro_cwd: str = ""  # Platform-specific working directory (optional)
    workspace_mode: str = ""  # Platform-specific mode (optional, fallback to global)


@dataclass
class DiscordConfig:
    """Discord adapter configuration."""
    enabled: bool = False
    bot_token: str = ""
    kiro_cwd: str = ""  # Platform-specific working directory (optional)
    workspace_mode: str = ""  # Platform-specific mode (optional, fallback to global)


@dataclass
class KiroConfig:
    """Kiro CLI configuration."""
    path: str = "kiro"
    default_cwd: str = ""  # Default working directory if platform doesn't specify
    idle_timeout: int = 300  # seconds
    workspace_mode: str = "per_chat"  # Global default: "fixed" or "per_chat"


@dataclass
class Config:
    """Main configuration."""
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    kiro: KiroConfig = field(default_factory=KiroConfig)
    log_level: str = "INFO"

    def get_workspace_mode(self, platform: str) -> str:
        """Get workspace_mode for a platform (platform-specific or global default)."""
        if platform == "feishu" and self.feishu.workspace_mode:
            return self.feishu.workspace_mode
        elif platform == "discord" and self.discord.workspace_mode:
            return self.discord.workspace_mode
        return self.kiro.workspace_mode

    def get_kiro_cwd(self, platform: str) -> str | None:
        """Get base working directory for kiro-cli startup.
        
        Returns None in per_chat mode (use global ~/.kiro/ config).
        Returns platform cwd in fixed mode (use project-level .kiro/ config).
        """
        mode = self.get_workspace_mode(platform)
        if mode == "per_chat":
            # Don't pass cwd, let kiro-cli use global config
            return None
        
        # fixed mode: use platform-specific or default cwd
        if platform == "feishu" and self.feishu.kiro_cwd:
            return self.feishu.kiro_cwd
        elif platform == "discord" and self.discord.kiro_cwd:
            return self.discord.kiro_cwd
        return self.kiro.default_cwd or os.getcwd()

    def get_session_cwd(self, platform: str, chat_id: str) -> str:
        """Get working directory for a specific chat session.
        
        In 'fixed' mode: all chats share the platform's base directory.
        In 'per_chat' mode: each chat gets its own subdirectory under default_cwd.
        """
        mode = self.get_workspace_mode(platform)
        
        if mode == "per_chat":
            # Use default_cwd as base for per_chat subdirectories
            if platform == "feishu" and self.feishu.kiro_cwd:
                base = self.feishu.kiro_cwd
            elif platform == "discord" and self.discord.kiro_cwd:
                base = self.discord.kiro_cwd
            else:
                base = self.kiro.default_cwd or os.getcwd()
            
            # Sanitize chat_id for use in path
            safe_chat_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in chat_id)
            return os.path.join(base, safe_chat_id)
        
        # fixed mode: all sessions share the same directory
        if platform == "feishu" and self.feishu.kiro_cwd:
            return self.feishu.kiro_cwd
        elif platform == "discord" and self.discord.kiro_cwd:
            return self.discord.kiro_cwd
        return self.kiro.default_cwd or os.getcwd()


def load_config() -> Config:
    """Load configuration from environment variables."""
    load_dotenv()

    feishu = FeishuConfig(
        enabled=os.getenv("FEISHU_ENABLED", "true").lower() in ("true", "1", "yes"),
        app_id=os.getenv("FEISHU_APP_ID", ""),
        app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        bot_name=os.getenv("FEISHU_BOT_NAME", ""),
        kiro_cwd=os.getenv("FEISHU_KIRO_CWD", ""),
        workspace_mode=_parse_workspace_mode(os.getenv("FEISHU_WORKSPACE_MODE"), ""),
    )

    discord = DiscordConfig(
        enabled=os.getenv("DISCORD_ENABLED", "false").lower() in ("true", "1", "yes"),
        bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
        kiro_cwd=os.getenv("DISCORD_KIRO_CWD", ""),
        workspace_mode=_parse_workspace_mode(os.getenv("DISCORD_WORKSPACE_MODE"), ""),
    )

    kiro = KiroConfig(
        path=os.getenv("KIRO_PATH", "kiro"),
        default_cwd=os.getenv("KIRO_CWD", os.getcwd()),
        idle_timeout=int(os.getenv("KIRO_IDLE_TIMEOUT", "300")),
        workspace_mode=_parse_workspace_mode(os.getenv("KIRO_WORKSPACE_MODE"), "per_chat"),
    )

    return Config(
        feishu=feishu,
        discord=discord,
        kiro=kiro,
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )
