from sqlalchemy.future import select
from bot.models import get_db, BotConfig
from bot.config import settings
import logging

logger = logging.getLogger(__name__)

# Module-level cache
_config_cache = {}

async def preload_config() -> None:
    """Preloads all config values from database into memory cache."""
    try:
        async with get_db() as session:
            result = await session.execute(select(BotConfig))
            configs = result.scalars().all()
            _config_cache.clear()
            for cfg in configs:
                _config_cache[cfg.key] = cfg.value
            logger.info(f"Preloaded {len(_config_cache)} configuration(s) into memory cache.")
    except Exception as e:
        logger.error(f"Failed to preload config from database: {e}", exc_info=True)

def get_config(key: str, default: str = None) -> str:
    """Synchronously gets a config value from the memory cache."""
    return _config_cache.get(key, default)

async def set_config(key: str, value: str) -> None:
    """Asynchronously updates a config key-value pair in both DB and cache."""
    str_value = str(value)
    
    # Update memory cache
    _config_cache[key] = str_value
    
    # Update DB
    try:
        async with get_db() as session:
            result = await session.execute(select(BotConfig).filter(BotConfig.key == key))
            cfg = result.scalars().first()
            if cfg:
                cfg.value = str_value
            else:
                cfg = BotConfig(key=key, value=str_value)
                session.add(cfg)
            logger.info(f"Saved config key '{key}' = '{str_value}' to database.")
    except Exception as e:
        logger.error(f"Failed to save config key '{key}' to database: {e}", exc_info=True)

def get_preview_video_id() -> str:
    """Gets preview video file_id/url with fallback."""
    return get_config("PREVIEW_VIDEO_FILE_ID", settings.FULL_VIDEO_FILE_ID)

def get_full_video_id() -> str:
    """Gets full video file_id with fallback."""
    return get_config("FULL_VIDEO_FILE_ID", settings.FULL_VIDEO_FILE_ID)

def get_video_price() -> int:
    """Gets full video price in INR with fallback."""
    try:
        return int(get_config("FULL_VIDEO_PRICE_INR", str(settings.FULL_VIDEO_PRICE_INR)))
    except (ValueError, TypeError):
        return settings.FULL_VIDEO_PRICE_INR
