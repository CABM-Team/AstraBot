"""全局配置：从 .env 文件加载机器人人设、API 密钥、回复概率等参数"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass
class Config:
    """机器人运行配置，所有字段均从环境变量读取"""
    name: str = ""
    name_cn: str = ""
    person_setting: str = ""
    enabled_groups: list[int] = field(default_factory=list)
    output_style: str = ""
    reply_style: str = ""
    extra_style: str = ""
    image_analyzer: str = ""

    keyword: list[str] = field(default_factory=list)

    # 四种回复概率，分别对应：普通消息 / @触发 / 回复中继续搭话 / 回复中被@插入
    reply_rate: float = 0.1
    reply_rate_at: float = 1.0
    reply_rate_in_reply: float = 0.0
    reply_rate_at_in_reply: float = 1.0

    SILICONFLOW_API_KEY: str = ""
    MINIMAX_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""

    API_PROVIDER: str = "MINIMAX"
    API_MODEL: str = ""
    BACK_API_PROVIDER: str = "DEEPSEEK"       # 主 API 失败时的备用提供商
    BACK_API_MODEL: str = ""
    VISUAL_API_PROVIDER: str = "SILICONFLOW"  # 图片分析专用提供商
    VISUAL_API_MODEL: str = ""

    MINIMAX_API_HOST: str = "https://api.minimaxi.com"

    @classmethod
    def load(cls) -> Config:
        load_dotenv()

        raw = {k: v for k, v in os.environ.items() if k != ""}
        required_str = ["name", "name_cn", "person_setting", "output_style", "reply_style", "extra_style", "image_analyzer"]
        for key in required_str:
            if not raw.get(key):
                raise ValueError(f"Missing required config: {key}")

        required_api = ["SILICONFLOW_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY"]
        for key in required_api:
            if not raw.get(key):
                raise ValueError(f"Missing required config: {key}")

        required_model_keys = ["API_MODEL", "BACK_API_MODEL", "VISUAL_API_MODEL"]
        for key in required_model_keys:
            if not raw.get(key):
                raise ValueError(f"Missing required config: {key}")

        enabled_groups_raw = raw.get("enabled_groups", "[]")
        if isinstance(enabled_groups_raw, str):
            import ast
            enabled_groups = [int(x) for x in ast.literal_eval(enabled_groups_raw)]
        else:
            enabled_groups = [int(x) for x in enabled_groups_raw]

        float_keys = ["reply_rate", "reply_rate_at", "reply_rate_in_reply", "reply_rate_at_in_reply"]
        floats = {}
        for key in float_keys:
            val = raw.get(key, "0.1")
            try:
                floats[key] = float(val)
            except (ValueError, TypeError):
                floats[key] = 0.1

        keyword_raw = raw.get("keyword", "[]")
        if isinstance(keyword_raw, str):
            import ast
            keyword = ast.literal_eval(keyword_raw)
        else:
            keyword = list(keyword_raw)

        return cls(
            name=raw["name"],
            name_cn=raw["name_cn"],
            person_setting=raw["person_setting"],
            enabled_groups=enabled_groups,
            keyword=keyword,
            output_style=raw["output_style"],
            reply_style=raw["reply_style"],
            extra_style=raw["extra_style"],
            image_analyzer=raw["image_analyzer"],
            reply_rate=floats["reply_rate"],
            reply_rate_at=floats["reply_rate_at"],
            reply_rate_in_reply=floats["reply_rate_in_reply"],
            reply_rate_at_in_reply=floats["reply_rate_at_in_reply"],
            SILICONFLOW_API_KEY=raw.get("SILICONFLOW_API_KEY", ""),
            MINIMAX_API_KEY=raw.get("MINIMAX_API_KEY", ""),
            DEEPSEEK_API_KEY=raw.get("DEEPSEEK_API_KEY", ""),
            API_PROVIDER=raw.get("API_PROVIDER", "MINIMAX"),
            API_MODEL=raw.get("API_MODEL", ""),
            BACK_API_PROVIDER=raw.get("BACK_API_PROVIDER", "DEEPSEEK"),
            BACK_API_MODEL=raw.get("BACK_API_MODEL", ""),
            VISUAL_API_PROVIDER=raw.get("VISUAL_API_PROVIDER", "SILICONFLOW"),
            VISUAL_API_MODEL=raw.get("VISUAL_API_MODEL", ""),
            MINIMAX_API_HOST=raw.get("MINIMAX_API_HOST", "https://api.minimaxi.com"),
        )


_config_instance: Config | None = None


def get_config() -> Config:
    global _config_instance
    if _config_instance is None:
        _config_instance = Config.load()
    return _config_instance
