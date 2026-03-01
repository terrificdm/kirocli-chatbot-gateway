"""Entry point for kirocli-bot-gateway."""

import logging
import sys

from config import load_config
from gateway import Gateway
from adapters import FeishuAdapter, DiscordAdapter


def main():
    config = load_config()
    
    # Setup logging
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )
    log = logging.getLogger(__name__)

    # Build adapter list based on config
    adapters = []

    if config.feishu.enabled:
        if not config.feishu.app_id or not config.feishu.app_secret:
            log.error("Feishu enabled but FEISHU_APP_ID or FEISHU_APP_SECRET not set")
            sys.exit(1)
        log.info("Feishu adapter enabled")
        adapters.append(FeishuAdapter(
            app_id=config.feishu.app_id,
            app_secret=config.feishu.app_secret,
            bot_name=config.feishu.bot_name,
        ))

    if config.discord.enabled:
        if not config.discord.bot_token:
            log.error("Discord enabled but DISCORD_BOT_TOKEN not set")
            sys.exit(1)
        log.info("Discord adapter enabled")
        adapters.append(DiscordAdapter(
            bot_token=config.discord.bot_token,
            policy=config.discord.policy,
        ))

    if not adapters:
        log.error("No adapters enabled. Set FEISHU_ENABLED=true or DISCORD_ENABLED=true")
        sys.exit(1)

    # Create and start gateway
    gateway = Gateway(config, adapters)
    gateway.start()


if __name__ == "__main__":
    main()
