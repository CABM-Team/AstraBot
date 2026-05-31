def run(bot, event, history, image_desc, config, plugin_config):
    """
    示例插件：在每次触发时追加一条提示。

    plugin_config 来自 settings.toml:
      {"must": false, "function_format": "string", "function_desc": "...", "re_exec": false}

    返回 None 表示不干预；
    返回 dict 可控制后续流程（见 plan.md 第三节）。
    """
    return None
