# AstraBot 插件系统

## 目录结构

```
plugins/
├── your_plugin/
│   ├── __init__.py       # 必须，实现 run() 函数
│   └── settings.toml     # 可选，插件配置
└── example_plug/         # 参考模板，不会被加载
```

## 插件如何工作

消息处理流程中，插件链在 **AI 生成回复之前** 按目录名排序依次执行。每个插件可以通过两个阶段参与：

- `before_run()`：读取当前消息/历史，返回提示词片段，嵌入完整提示词
- `run()`：直接返回回复文本、修改提示词，或返回 None 表示不干预

### 插件链执行顺序

```
收到群消息 → 记录历史 → 概率判定 → 获取锁
  → 插件链执行（按目录名排序）
      → 插件A before_run()
      → 插件A run()
      → 插件B before_run()/run()（如果 A 没 block）
      → ...
  → 图片识别 → 拼提示词（含 before_run 注入）
  → AI 生成回复 → （搜索循环）→ 发送
```

## `before_run()`

```python
def before_run(bot, event, history, image_desc, config, plugin_config):
    """
    可选钩子，在默认主提示词构建之前执行。

    适合做知识库检索、聊天记录关键词提取、上下文扩写等预处理。

    参数与 run() 完全一致。

    返回值：
      str | dict | None

      None = 不注入任何内容
      str  = 直接作为提示词片段注入到完整提示词末尾
      dict 可选字段：

      prompt (str)
          作为提示词片段注入到完整提示词末尾。

      append_prompt (str)
          与 prompt 等价，便于和 run() 的返回风格保持一致。
    """
    return None
```

- `before_run()` 会在每个插件自己的 `run()` 之前执行
- 所有插件返回的提示词片段会按插件顺序合并，并嵌入主提示词
- `before_run()` 只负责提示词预处理，不控制 `skip_main` / `block` / `reply`

## `run()` 函数

```python
def run(bot, event, history, image_desc, config, plugin_config):
    """
    每次有消息触发回复时，插件链会调用每个插件的 run()。

    参数：
      bot           - nonebot Bot 实例，可用于调用 API（如 bot.send_group_msg）
      event         - GroupMessageEvent，当前触发消息的事件
      history       - list[dict]，最近 7 条聊天记录（不含当前消息）
                      每条: {"time": int, "user_id": str, "user_name": str,
                             "message": str, "images": list[str]|None}
      image_desc    - str，识图结果文本（当前消息的图片分析结果），无图为 ""
      config        - Config 对象，所有 .env 配置项
      plugin_config - dict，本插件的 settings.toml 内容

    返回值：
      dict | None

      None      = 插件未命中，不干预后续流程
      dict 可选字段：

      reply (str)
          直接回复此文本，不调用 AI（需配合 skip_main=true）

      override_prompt (str)
          完全替换发送给 AI 的提示词。
          设置了此字段后，系统不再组装人设/历史/记忆等，直接使用此内容。
          如果同插件链中存在 before_run() 返回内容，这些内容仍会追加到 override_prompt 末尾。

      append_prompt (str)
          在默认提示词末尾追加内容。
          与 override_prompt 不同，append_prompt 保留系统自动组装的人设、
          聊天历史、记忆、插件说明等，仅在其后追加你的内容。
          适用于需要让 AI 参考额外信息的场景。

      block (bool)
          true 时停止执行后续插件（默认 false）

      skip_main (bool)
          true 时跳过 AI 生成，直接用 reply 发送（默认 false）。
          如果同时有 re_exec=true 的插件返回了 append_prompt，
          则即使 skip_main=true 也不会跳过 AI 调用。
    """
    return None
```

### 返回值字段说明

| 字段 | 与 run() 内返回 | 效果 |
|------|----------------|------|
| `reply` | `{"reply": "你好"}` | 直接发消息（skip_main=true 时） |
| `append_prompt` | `{"append_prompt": "当前温度是25℃"}` | AI 提示词末尾追加 |
| `override_prompt` | `{"override_prompt": "你是一个..."}` | 替换整个 AI 提示词 |
| `block` | `{"append_prompt": "...", "block": true}` | 后续插件不再执行 |
| `skip_main` | `{"reply": "你好", "skip_main": true}` | 跳过 AI，直接回复 |

## `settings.toml`

```toml
must = false                     # true=该字段在 JSON 中必填，false=可选
function_format = "string"       # JSON 值的类型，如 string / array / object
function_desc = "功能说明"        # 插件的功能描述，会拼接到 AI 提示词
re_exec = false                  # 是否触发 AI 重新调用
```

### `must`
- `true`：提示词中该字段标记为"必填字段，必须在 JSON 中包含"
- `false`：提示词中该字段标记为"可选字段，根据需要选择使用"

### `function_format` 和 `function_desc`
这两个字段被 `PluginLoader.get_plugin_section()` 拼接到 AI 的主提示词中，格式为：

```
你还可以使用以下插件功能：
{插件名}({function_format})：{function_desc}
```

例如插件名为 `weather` 时：

```
weather(object)：查询天气，返回 {"temperature": "25℃", "condition": "晴"}
```

AI 看到后就知道 JSON 中 `weather` 字段的值应该是一个 object。

### `re_exec`

`re_exec=true` 的作用类似于**联网搜索**的重新调用机制：

1. 插件 `run()` 返回 `append_prompt`
2. 系统记录插件执行开始时间
3. 系统把 append_prompt 注入到 prompt 末尾
4. 同时注入插件执行期间群友的新发言
5. 重新调用 AI 生成回复

工作方式：

```
插件链执行
  └─ re_exec=true 的插件返回 append_prompt
      └─ 记录这段时间的新消息
      └─ inject = append_prompt + 新消息
      └─ main_prompt + inject → 调用 AI
                               └─ 如果返回 search → 搜索循环
                               └─ 最终回复
```

这和搜索注入几乎一样，只是注入的内容来自插件而非搜索：

| 场景 | 注入内容 | 新消息来源 |
|------|---------|-----------|
| 联网搜索 | 搜索结果摘要 | 搜索期间的消息 |
| re_exec 插件 | 插件的 append_prompt | 插件执行期间的消息 |

如果多个 `re_exec=true` 的插件都返回了 `append_prompt`，它们会被合并后一次性注入。

## 加载策略

- 启动时扫描 `plugins/` 下每个子目录，跳过 `example_plug`
- 按目录名排序后顺序执行
- 任何插件加载失败不会阻止启动（仅记日志）
- 插件 `run()` 抛出异常不会中断流程，仅记日志
- `block=True` 可停止后续插件执行
