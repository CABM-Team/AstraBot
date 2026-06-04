def before_run(bot, event, history, image_desc, config, plugin_config):
    return None


def run(bot, event, history, image_desc, config, plugin_config):
    """
    示例插件：在每次触发时追加一条提示。

    before_run() 适合基于当前消息和历史做知识库召回、关键词检索等，
    返回字符串或 {"prompt": "..."} 即可注入完整提示词。

    plugin_config 来自 settings.toml:
      {"must": false, "function_format": "string", "function_desc": "...", "re_exec": false}

    返回 None 表示不干预；
    返回 dict 可控制后续流程（见 README_Plugin.md）。
    """
    return None
