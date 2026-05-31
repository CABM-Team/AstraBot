"""Prompt 构建器：拼接人设、记忆、历史、触发标记等各部分，生成完整的 AI 输入"""

from __future__ import annotations

from typing import Any

from astrabot.config import Config


def build_main_prompt(
    config: Config,
    plugin_section: str,
    formatted_history: str,
    trigger_marker: str,
    memory_section: str = "",
    personal_memory_section: str = "",
    current_time: str = "",
    long_term_memory: str = "",
    relevant_facts: str = "",
) -> str:
    parts = [
        f"你的名字是{config.name_cn}，英文名是{config.name}，你的设定是{config.person_setting}。",
        "",
        f"【当前真实时间】{current_time}，这是现在的真实时间，不是假设或设定。所有联网搜索结果都基于此时间。",
        "",
        "你需要按照如下的格式输出：",
        config.output_style,
        "",
        "你的说话风格如下：",
        config.reply_style,
        "",
        "除此之外，有一些额外的要求需要你注意：",
        config.extra_style,
    ]

    if plugin_section:
        parts.append("")
        parts.append(plugin_section)


    if long_term_memory:
        parts.append("")
        parts.append("【重要记忆】以下是你必须记住的内容：")
        parts.append(long_term_memory)

    if relevant_facts:
        parts.append("")
        parts.append("【相关记忆】以下是与当前对话相关的记忆：")
        parts.append(relevant_facts)


    # 旧版记忆仅在增强记忆系统没有长期记忆时展示，避免重复
    if memory_section and not long_term_memory:
        parts.append("")
        parts.append("你之前记住的东西：")
        parts.append(memory_section)

    if personal_memory_section:
        parts.append("")
        parts.append("你对群友的印象：")
        parts.append(personal_memory_section)

    parts.append("")
    parts.append("这是最近的聊天记录：")
    parts.append(formatted_history)
    parts.append("")
    parts.append(trigger_marker)

    return "\n".join(parts)


def format_history(
    history: list[dict],
    history_image_descs: dict[tuple[int, int], str] | None = None,
) -> str:
    if not history:
        return "（暂无聊天记录）"

    history_image_descs = history_image_descs or {}
    lines = []
    for msg_idx, msg in enumerate(history):
        user_id = msg.get("user_id", "?")
        user_name = msg.get("user_name", "未知")
        segments = msg.get("segments")

        if segments:
            parts = []
            reply_info = ""
            for seg_idx, seg in enumerate(segments):
                if seg["type"] == "reply":
                    ruid = seg.get("user_id", "")
                    rname = seg.get("user_name", "")
                    rmsg = seg.get("message", "")
                    if ruid:
                        reply_info = f"回复{rname}({ruid})：{rmsg}"
                    elif rmsg:
                        reply_info = rmsg
                elif seg["type"] == "text":
                    parts.append(seg.get("text", ""))
                elif seg["type"] == "at":
                    qq = seg.get("qq", "")
                    if qq == "all":
                        parts.append("@所有人")
                    else:
                        parts.append(f"@{qq}")
                elif seg["type"] == "image":
                    desc = history_image_descs.get((msg_idx, seg_idx), "")
                    if desc:
                        parts.append(f"[图片][图片描述：{desc}]")
                    else:
                        parts.append("[图片]")
            msg_text = "".join(parts)
            if reply_info:
                line = f"{user_name}({user_id})（{reply_info}）：{msg_text}"
            else:
                line = f"{user_name}({user_id})：{msg_text}"
        else:
            text = msg.get("message", "")
            line = f"{user_name}({user_id})：{text}"

        lines.append(line)

    return "\n".join(lines)


def build_message_text(
    segments: list[dict],
    image_descriptions: list[str],
) -> str:
    parts = []
    reply_prefix = ""
    desc_idx = 0
    for seg in segments:
        if seg["type"] == "reply":
            ruid = seg.get("user_id", "")
            rname = seg.get("user_name", "")
            rmsg = seg.get("message", "")
            if ruid:
                reply_prefix = f"（回复{rname}({ruid})：{rmsg}）"
            elif rmsg:
                reply_prefix = f"（回复：{rmsg}）"
        elif seg["type"] == "text":
            parts.append(seg.get("text", ""))
        elif seg["type"] == "at":
            qq = seg.get("qq", "")
            name = seg.get("name", "")
            if qq == "all":
                parts.append("@所有人")
            elif name:
                parts.append(f"@{name}({qq})")
            else:
                parts.append(f"@{qq}")
        elif seg["type"] == "image":
            desc = image_descriptions[desc_idx] if desc_idx < len(image_descriptions) else ""
            desc_idx += 1
            if desc:
                parts.append(f"[图片][图片描述：{desc}]")
            else:
                parts.append("[图片]")
    text = "".join(parts)
    if reply_prefix:
        return reply_prefix + text
    return text


TRIGGER_MAIN = "你看到这里，准备回复"
TRIGGER_AT = "你被at了，准备回复"
TRIGGER_IN_PROGRESS = "你正在准备上一条消息"
TRIGGER_INTERRUPT = "你在回复途中被at了，准备回复。回复将会在上一个消息发送后，发送。"

def build_trigger_line(
    user_name: str,
    user_id: str,
    message_text: str,
    marker: str,
) -> str:
    return f"{user_name}({user_id})：{message_text}  <--{marker}"


def build_image_analysis_prompt(image_analyzer_prompt: str, context: str = "") -> str:
    parts = [image_analyzer_prompt]
    if context:
        parts.append("")
        parts.append("最近的聊天记录供参考：")
        parts.append(context)
    parts.append("")
    parts.append("请为以下图片生成描述，使用 JSON 格式返回：")
    parts.append("{")
    parts.append('    "image_1": "描述",')
    parts.append('    "image_2": "描述",')
    parts.append("    ...")
    parts.append("}")
    return "\n".join(parts)


def build_search_decision_prompt(context: str, search_results_text: str) -> str:
    return (
        f"用户的问题是：{context}\n\n"
        f"这是搜索到的结果：\n{search_results_text}\n\n"
        "你的任务：\n"
        "1. 分析哪些结果有价值\n"
        "2. 决定 fetch 哪些 URL\n"
        "3. 判断是否需要换关键词重新搜索\n\n"
        "请用 JSON 格式返回：\n"
        "{\n"
        '    "fetch_urls": ["url1", "url2"],\n'
        '    "reasoning": "选择理由",\n'
        '    "needs_more_search": false,\n'
        '    "next_query": "新搜索词"\n'
        "}"
    )


def build_search_summary_prompt(fetched_content: str) -> str:
    return (
        f"这是提取的网页内容：\n{fetched_content}\n\n"
        "请综合搜索结果，给出总结。使用 JSON 格式返回：\n"
        "{\n"
        '    "summary": "搜索总结",\n'
        '    "sources": ["来源1"]\n'
        "}"
    )


def build_search_inject_prompt(
    search_summary: str,
    new_messages_since_search: str = "",
    search_failed: bool = False,
    current_time: str = "",
) -> str:
    if not current_time:
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if search_failed:
        return "联网搜索失败了，无法获取搜索结果。请直接根据已有信息回复用户。"
    parts = [
        f"你刚才进行了联网搜索（当前真实时间 {current_time}），这是搜索结果：",
        search_summary,
    ]
    if new_messages_since_search:
        parts.append("")
        parts.append("联网搜索期间群友的新发言：")
        parts.append(new_messages_since_search)
    parts.append("")
    parts.append("请结合这些信息，重新构思回复。")
    return "\n".join(parts)
