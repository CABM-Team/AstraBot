# AstraBot

基于 [NoneBot2](https://nonebot.dev/) + [OneBot V11](https://github.com/botuniverse/onebot-11) 的 QQ 群聊机器人，支持多 AI 厂商、三层记忆系统、插件机制和联网搜索。

## 功能特性

- **多 AI 厂商支持** — MiniMax / DeepSeek / SiliconFlow，可配置主备切换
- **三层记忆系统** — 短期（近期对话上下文）、中期（自动提取的事实按相关性检索）、长期（用户指定记住的内容，始终加载）
- **插件机制** — 动态加载 `plugins/` 目录下的插件，支持链式执行、提示词注入、跳过 AI 生成
- **联网搜索** — 多轮搜索 + 网页抓取 + 结果总结，通过 MCP + DeepSeek 工具调用实现
- **图片分析** — 自动识别群聊图片并注入到 AI 上下文
- **回复概率控制** — 四种概率分别控制普通消息 / @触发 / 回复中搭话 / 回复中被 @ 插入
- **事实自动提取** — 从群聊对话中自动提取用户信息，形成中期记忆
- **记忆指令** — 支持 `记住` / `忘记` / `查看记忆` 等自然语言记忆管理

## 项目结构

```
AstraBot/
├── astrabot/
│   ├── config.py                    # 全局配置，从 .env 加载
│   ├── chat_service/
│   │   ├── message_handler.py       # 消息处理核心（解析、上下文组装、AI 调用）
│   │   ├── ai_client.py             # 统一封装 MiniMax / DeepSeek / SiliconFlow API
│   │   ├── enhanced_memory.py       # 三层记忆系统
│   │   ├── fact_extractor.py        # 对话事实自动提取
│   │   ├── history_manager.py       # 群聊历史记录管理（SQLite + JSONL）
│   │   ├── memory_manager.py        # 记忆系统入口（LTM / MTM / STM 协调）
│   │   ├── memory_commands.py       # 自然语言记忆指令解析
│   │   ├── prompt_builder.py        # AI 提示词组装
│   │   ├── reply_controller.py      # 回复概率判定与状态管理
│   │   ├── search_engine.py         # 联网搜索与结果总结
│   │   ├── image_utils.py           # 图片解析工具
│   │   ├── plugin_loader.py         # 插件动态加载器
│   │   └── plugins/
│   │       ├── example_plug/        # 示例插件模板
│   │       └── docker_exec/         # Docker 执行插件
│   └── logmanager/
│       └── logger.py                # 统一日志管理
├── .env.example                     # 环境变量示例
├── .env.prod                        # 生产环境部署变量
├── pyproject.toml                   # 项目依赖与 NoneBot 配置
└── README_Plugin.md                 # 插件开发文档
```

## 快速开始

### 环境要求

- Python >= 3.10
- 三个 AI API Key（均为必填）：
  - **MiniMax** — Token Plan，用于联网搜索
  - **DeepSeek** — 主聊天模型；联网搜索的Agent
  - **SiliconFlow** — 图像识别

### 安装

```bash
# 先安装pipx
sudo pacman -S python-pipx # 不同发行版和系统不一样。例如Windows需要pip：python -m pip install --user pipx
pipx install nb-cli

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate # Windows用./.venv/scripts/activate；fish用户用source .venv/bin/activate.fish

# 安装依赖
pip install -e .
```

### 配置

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入 API Key 和配置
# 必填项：SILICONFLOW_API_KEY, MINIMAX_API_KEY, DEEPSEEK_API_KEY
#          name, name_cn, person_setting, output_style, reply_style, extra_style, image_analyzer
#          API_MODEL, BACK_API_MODEL, VISUAL_API_MODEL
```

关键配置项说明：

| 变量 | 说明 |
|------|------|
| `name` / `name_cn` | 机器人名称 / 中文名 |
| `person_setting` | 角色人设（性别、年龄等） |
| `output_style` | 输出格式约束（JSON 格式，控制回复结构） |
| `reply_style` | 说话风格设定 |
| `enabled_groups` | 启用机器人的群号列表，如 `['12345678']` |
| `reply_rate` | 普通消息回复概率（0.0 ~ 1.0） |
| `reply_rate_at` | 被 @ 时的回复概率 |
| `API_PROVIDER` | 主 API 厂商（MINIMAX / DEEPSEEK） |
| `BACK_API_PROVIDER` | 主 API 失败时的备用厂商 |
| `VISUAL_API_PROVIDER` | 图片分析专用厂商（SILICONFLOW） |

### 启动

```bash
nb run
```

更多启动方式请参考 [NoneBot 文档](https://nonebot.dev/docs/)。

## 插件开发

插件放在 `astrabot/chat_service/plugins/` 目录下，每个插件一个子目录，包含 `__init__.py`（实现 `run()` 函数）和可选的 `settings.toml`。

```python
def run(bot, event, history, image_desc, config, plugin_config):
    """
    bot           - NoneBot Bot 实例
    event         - GroupMessageEvent
    history       - list[dict]，最近聊天记录
    image_desc    - str，当前消息的图片分析结果
    config        - Config 对象
    plugin_config - dict，settings.toml 内容

    返回值:
      None          - 不干预
      {"reply": "你好", "skip_main": True}   - 直接回复，跳过 AI
      {"append_prompt": "..."}               - 在 AI 提示词末尾追加内容
      {"override_prompt": "..."}             - 完全替换 AI 提示词
      {"block": True}                        - 阻止后续插件执行
    """
    return None
```

详见 [README_Plugin.md](./README_Plugin.md)。

## 许可证

GNU GPL v3.0