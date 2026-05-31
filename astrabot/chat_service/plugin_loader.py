"""插件加载器：从 plugins 目录动态加载插件，支持插件链式执行和 prompt 注入"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

from astrabot.logmanager.logger import logger

PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"

DEFAULT_SETTINGS = {
    "must": False,
    "function_format": "string",
    "function_desc": "",
    "re_exec": False,
}


class PluginInfo:
    """插件信息：名称、模块对象、settings.toml 中的配置"""
    def __init__(self, name: str, module: ModuleType, settings: dict):
        self.name = name
        self.module = module
        self.settings = {**DEFAULT_SETTINGS, **settings}


class PluginLoader:
    plugins: list[PluginInfo] = []
    _loaded = False

    @classmethod
    def load_all(cls):
        """扫描 plugins 目录，加载每个含 run() 函数的子目录为插件"""
        if cls._loaded:
            return
        cls.plugins.clear()

        if not PLUGINS_DIR.exists():
            logger.warning(f"Plugins directory not found: {PLUGINS_DIR}")
            cls._loaded = True
            return

        entries = sorted(
            [d for d in PLUGINS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_") and d.name != "example_plug"],
            key=lambda d: d.name,
        )

        for entry in entries:
            try:
                module = importlib.import_module(
                    f"astrabot.chat_service.plugins.{entry.name}.__init__"
                )
                if not hasattr(module, "run") or not callable(module.run):
                    raise ImportError(f"Plugin {entry.name} missing run() function")

                settings_path = entry / "settings.toml"
                settings = {}
                if settings_path.exists():
                    with open(settings_path, "rb") as f:
                        settings = tomllib.load(f)

                plugin = PluginInfo(name=entry.name, module=module, settings=settings)
                cls.plugins.append(plugin)
                logger.info(f"Loaded plugin: {entry.name}")
            except Exception as e:
                logger.error(f"Failed to load plugin {entry.name}: {e}")

        cls._loaded = True

    @classmethod
    def get_plugin_section(cls) -> str:
        """生成注入到 prompt 中的工具说明文本，告知 AI 可用的插件字段"""
        if not cls.plugins:
            return ""

        required = []
        optional = []
        for p in cls.plugins:
            fmt = p.settings.get("function_format", "string")
            desc = p.settings.get("function_desc", "")
            if not desc:
                continue
            entry = f"{p.name}({fmt})：{desc}"
            if p.settings.get("must", False):
                required.append(entry)
            else:
                optional.append(entry)

        lines = ["【可用工具】（需要时直接在回复 JSON 中添加对应字段即可调用）："]
        if required:
            lines.append("  必填字段（必须在 JSON 中包含）：")
            for r in required:
                lines.append(f"    {r}")
        if optional:
            lines.append("  可选工具（需要时使用）：")
            for o in optional:
                lines.append(f"    {o}")
        lines.append("  示例：{\"reply\": \"...\", \"docker_exec\": \"ls -la\"}")
        return "\n".join(lines)

    @classmethod
    def execute_chain(cls, bot, event, history: list[dict], image_desc: str, config: Any) -> tuple[dict | None, str]:
        """依次执行所有插件的 run()，合并结果；支持 block 终止和 re_exec 追加"""
        merged: dict | None = None
        re_exec_parts: list[str] = []

        for p in cls.plugins:
            try:
                result = p.module.run(
                    bot=bot,
                    event=event,
                    history=history,
                    image_desc=image_desc,
                    config=config,
                    plugin_config=p.settings,
                )
                if result is None:
                    continue
                if merged is None:
                    merged = {}
                merged.update(result)
                # block=True 终止后续插件执行，但已合并的结果仍会生效
                if result.get("block"):
                    break
                # re_exec 插件：将 append_prompt 收集起来，后续注入 prompt 让 AI 重新生成
                if p.settings.get("re_exec", False):
                    if result.get("append_prompt"):
                        re_exec_parts.append(result["append_prompt"])
            except Exception as e:
                logger.error(f"Plugin {p.name} run() error: {e}")

        re_exec_append = "\n\n".join(re_exec_parts) if re_exec_parts else ""
        return merged, re_exec_append

    @classmethod
    def has_re_exec_plugins(cls) -> bool:
        return any(p.settings.get("re_exec", False) for p in cls.plugins)
