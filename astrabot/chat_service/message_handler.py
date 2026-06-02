"""群消息处理核心：接收消息 → 判断是否回复 → 构建 prompt → 调用 AI → 发送回复"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import time
from datetime import datetime

from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment

from astrabot.chat_service.ai_client import analyze_images, chat, generate_reply
from astrabot.chat_service.history_manager import (
    append_message,
    get_history_since,
    get_recent_messages,
    select_context,
    trim_history,
)
from astrabot.chat_service.fact_extractor import extract_and_store
from astrabot.chat_service.image_utils import download_image_base64
from astrabot.chat_service.memory_manager import (
    is_user_ignored,
    get_memories,
    get_personal_memorize,
    process_memorize,
    process_personal_memorize,
    process_ignore_fields,
)
from astrabot.chat_service.enhanced_memory import (
    extract_facts_from_conversation,
    build_memory_context,
)

from astrabot.chat_service.plugin_loader import PluginLoader
from astrabot.chat_service.prompt_builder import (
    build_image_analysis_prompt,
    build_main_prompt,
    build_message_text,
    build_search_inject_prompt,
    build_trigger_line,
    format_history,
    TRIGGER_AT,
    TRIGGER_MAIN,
    TRIGGER_IN_PROGRESS,
    TRIGGER_INTERRUPT,
)
from astrabot.chat_service.reply_controller import ReplyController, should_reply
from astrabot.chat_service.search_engine import run_search
from astrabot.config import get_config
from astrabot.logmanager.logger import logger

TOTAL_TIMEOUT = 360
MAX_SEARCH_ROUNDS = 3


async def handle_group_message(bot: Bot, event: GroupMessageEvent):
    """群消息入口：解析消息段 → 存历史 → 判断是否需要回复 → 进入回复流程"""
    config = get_config()
    group_id = event.group_id

    if group_id not in config.enabled_groups:
        return

    user_id = str(event.user_id)
    plain_text = event.get_plaintext()
    user_name = await _get_user_display_name(bot, group_id, event.user_id)

    record_time = int(time.time())
    images_b64 = []
    segments = []
    if event.reply:
        try:
            # OneBot 协议中 event.reply 是快捷访问，但可能为 None 或缺少数据
            reply_obj = event.reply
            sender = reply_obj.sender
            try:
                plain = reply_obj.message.get_plaintext()
            except Exception:
                plain = str(reply_obj.message)
            reply_seg = {
                "type": "reply",
                "user_id": str(sender.user_id),
                "user_name": sender.card or sender.nickname or "",
                "message": plain[:100],
            }
            segments.append(reply_seg)
            logger.debug(f"Reply segment resolved via event.reply: {reply_seg}")
        except Exception as e:
            logger.warning(f"Failed to extract reply from event.reply: {e}")
    for seg in event.get_message():
        if seg.type == "reply":
            try:
                original = await bot.get_msg(message_id=int(seg.data["id"]))
                sender = original.get("sender", {})
                msg_segments = original.get("message", [])
                if isinstance(msg_segments, list):
                    plain = "".join(
                        s.get("data", {}).get("text", "") for s in msg_segments if s.get("type") == "text"
                    )
                else:
                    plain = str(msg_segments)
                reply_seg = {
                    "type": "reply",
                    "user_id": str(sender.get("user_id", "")),
                    "user_name": sender.get("card") or sender.get("nickname", ""),
                    "message": plain[:100],
                }
                segments.append(reply_seg)
                logger.debug(f"Reply segment resolved via get_msg: {reply_seg}")
            except Exception as e:
                # 三级降级：get_msg → DB 查询 → 占位文本
                logger.warning(f"get_msg failed for reply {seg.data.get('id')}: {e}, falling back to DB")
                reply_id = int(seg.data["id"])
                try:
                    from astrabot.chat_service.history_manager import _get_db
                    conn = _get_db()
                    conn.row_factory = sqlite3.Row
                    cur = conn.execute(
                        "SELECT * FROM messages WHERE group_id = ? AND message_id = ? LIMIT 1",
                        (group_id, reply_id),
                    )
                    row = cur.fetchone()
                    if row:
                        segments.append({
                            "type": "reply",
                            "user_id": row["user_id"],
                            "user_name": row["user_name"],
                            "message": row["message"][:100],
                        })
                    else:
                        segments.append({"type": "reply", "user_id": "", "user_name": "", "message": f"消息{reply_id}"})
                except Exception:
                    segments.append({"type": "reply", "user_id": "", "user_name": "", "message": f"消息{reply_id}"})
        elif seg.type == "text":
            t = seg.data.get("text", "")
            if t:
                segments.append({"type": "text", "text": t})
        elif seg.type == "at":
            qq = seg.data.get("qq", "")
            name = ""
            if qq and qq != "all":
                try:
                    info = await bot.get_group_member_info(group_id=group_id, user_id=int(qq))
                    name = info.get("card") or info.get("nickname", "")
                except Exception:
                    pass
            segments.append({"type": "at", "qq": qq, "name": name})
        elif seg.type == "image":
            url = seg.data.get("url", "")
            b64 = await download_image_base64(url) if url else None
            if b64:
                images_b64.append(b64)
            segments.append({"type": "image", "b64": b64})

    record = {
        "time": record_time,
        "message_id": event.message_id,
        "user_id": user_id,
        "user_name": user_name,
        "message": plain_text,
        "msg_type": "user",
        "images": images_b64 or None,
        "segments": segments,
    }
    await append_message(group_id, record)
    await trim_history(group_id)

    if plain_text.startswith("/"):
        logger.debug(f"Command message ignored: {plain_text[:30]}")
        return

    is_at = event.is_tome()
    is_keyword = any(plain_text.startswith(k) for k in config.keyword) if config.keyword else False

    if is_user_ignored(group_id, user_id, is_at):
        logger.debug(f"Ignored user {user_name}({user_id}) in group {group_id}")
        return


    if await _is_bot_muted(bot, group_id):
        logger.info(f"Bot is muted in group {group_id}, skipping reply")
        return

    lock = ReplyController.get_lock(group_id)
    if lock.locked():
        # 机器人正在回复时，只有 @ 或关键词触发的消息会排队等待
        if is_at or is_keyword:
            ReplyController.add_pending_at(group_id, {
                "user_id": user_id,
                "user_name": user_name,
                "segments": segments,
                "images_b64": images_b64,
                "record_time": record_time,
            })
            logger.info(f"Queued pending @ from {user_name}({user_id}) in group {group_id}")
        return

    if not should_reply(is_at, ReplyController.is_replying(group_id), config, is_keyword):
        logger.debug(f"Ignored message from {user_name}({user_id}) in group {group_id}: probability miss")
        return

    ReplyController.set_replying(group_id, True)
    try:
        await asyncio.wait_for(
            _reply_flow(bot, event, group_id, user_id, user_name, images_b64, segments, record_time, is_at, config),
            timeout=TOTAL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.error(f"Reply flow timeout for group {group_id}")
    except Exception as e:
        logger.error(f"Reply flow error for group {group_id}: {e}")
    finally:
        ReplyController.set_replying(group_id, False)


async def _reply_flow(
    bot: Bot,
    event: GroupMessageEvent,
    group_id: int,
    user_id: str,
    user_name: str,
    images_b64: list[str],
    current_segments: list[dict],
    record_time: int,
    is_at: bool,
    config,
):
    """核心回复流程：构建上下文 → 插件预处理 → 图片分析 → 拼接 prompt → AI 调用（含搜索） → 发送回复"""
    # 在处理中途检查触发消息是否已被撤回
    if ReplyController.is_recalled(group_id, event.message_id):
        logger.info(f"Trigger message {event.message_id} was recalled, aborting reply")
        return

    plain_text = "".join(
        seg.get("text", "") for seg in current_segments if seg.get("type") == "text"
    )

    plugin_since = int(time.time())  # 记录插件执行前的时间戳，后续用于获取执行期间的新消息

    history = await select_context(group_id, max_messages=15, exclude_message_id=event.message_id)

    current_image_descs: list[str] = []
    image_desc = ""
    if images_b64:
        ctx = "".join(
            f"{m.get('user_name','?')}：{m.get('message','')}\n"
            for m in history[-3:]
        )
        analysis_prompt = build_image_analysis_prompt(config.image_analyzer, ctx)
        current_image_result = await analyze_images(images_b64, analysis_prompt)
        desc_parts = [v for _, v in sorted(current_image_result.items())]
        current_image_descs = desc_parts
        image_desc = "; ".join(desc_parts)

    plugin_result, re_exec_append = PluginLoader.execute_chain(
        bot=bot,
        event=event,
        history=history,
        image_desc=image_desc,
        config=config,
    )

    plugin_section = PluginLoader.get_plugin_section()

    if plugin_result and plugin_result.get("skip_main") and not re_exec_append:
        # 插件要求跳过主 AI 调用，直接使用插件返回的回复
        reply_text = plugin_result.get("reply", "")
        if reply_text:
            await _send_reply(bot, event, group_id, reply_text, record_reply=True)
        return

    history_image_map = _collect_images_from_history(history, max_images=3)
    if history_image_map:
        # 分析历史消息中的图片，将 AI 描述注入到格式化的历史中
        all_b64 = [b64 for _, _, b64 in history_image_map]
        ctx = "".join(
            f"{m.get('user_name','?')}：{m.get('message','')}\n"
            for m in history[-3:]
        )
        analysis_prompt = build_image_analysis_prompt(config.image_analyzer, ctx)
        analysis_result = await analyze_images(all_b64, analysis_prompt)
        history_image_descs: dict[tuple[int, int], str] = {}
        for (msg_idx, seg_idx, _), (_, desc) in zip(history_image_map, sorted(analysis_result.items())):
            history_image_descs[(msg_idx, seg_idx)] = desc
    else:
        history_image_descs = {}

    pending_list = ReplyController.pop_pending_at(group_id)
    has_pending = bool(pending_list)

    # 构建触发标记：有排队消息时，当前消息标记为 IN_PROGRESS，排队消息标记为 INTERRUPT
    trigger_lines = []
    current_text = build_message_text(current_segments, current_image_descs)
    if has_pending:
        trigger_lines.append(build_trigger_line(user_name, user_id, current_text, TRIGGER_IN_PROGRESS))
        for p in pending_list:
            p_text = build_message_text(p["segments"], [])
            trigger_lines.append(build_trigger_line(p["user_name"], p["user_id"], p_text, TRIGGER_INTERRUPT))
    else:
        trigger_lines.append(build_trigger_line(
            user_name, user_id, current_text,
            TRIGGER_AT if is_at else TRIGGER_MAIN,
        ))
    trigger_marker = "\n".join(trigger_lines)

    formatted_history = format_history(history, history_image_descs)


    memories = get_memories(group_id)
    logger.debug(f"Loaded {len(memories)} memories for group {group_id}: {memories}")
    memory_section = "\n".join(f"{i}. {m}" for i, m in enumerate(memories, 1)) if memories else ""


    personal_memories = get_personal_memorize(group_id)
    # 收集当前消息涉及的所有用户 ID，用于加载相关个人印象
    relevant_uids = {user_id}
    for seg in event.get_message():
        if seg.type == "at":
            qq = seg.data.get("qq", "")
            if qq and qq.isdigit():
                relevant_uids.add(qq)
    reply_to_uid = await _get_reply_user_id(bot, event)
    if reply_to_uid:
        relevant_uids.add(reply_to_uid)


    memory_ctx = build_memory_context(
        group_id=group_id,
        current_message=plain_text,
        relevant_user_ids=relevant_uids,
    )
    logger.debug(f"Enhanced memory context: LTM={len(memory_ctx['long_term'])} chars, Facts={len(memory_ctx['facts'])} chars")


    personal_lines = []
    for uid in relevant_uids:
        memo = personal_memories.get(uid)
        if memo:
            name = await _get_user_display_name(bot, group_id, int(uid))
            personal_lines.append(f"{name}({uid})：{memo}")
    personal_memory_section = "\n".join(personal_lines) if personal_lines else ""

    override_prompt = plugin_result.get("override_prompt") if plugin_result else None
    append_prompt = plugin_result.get("append_prompt") if plugin_result else None

    # override_prompt 完全替换 build_main_prompt 的输出；append_prompt 追加到末尾
    if override_prompt:
        main_prompt = override_prompt
    else:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        main_prompt = build_main_prompt(
            config=config,
            plugin_section=plugin_section,
            formatted_history=formatted_history,
            trigger_marker=trigger_marker,
            memory_section=memory_section,
            personal_memory_section=personal_memory_section,
            current_time=current_time,
            long_term_memory=memory_ctx["long_term"],
            relevant_facts=memory_ctx["facts"],
        )
        if append_prompt:
            main_prompt += "\n\n" + append_prompt

    if re_exec_append:
        # re_exec 插件要求：将插件输出注入历史后，获取执行期间新消息，让 AI 重新构思
        await append_message(group_id, {
            "time": int(time.time()),
            "message_id": 0,
            "user_id": str(bot.self_id),
            "user_name": "",
            "message": re_exec_append[:500],
            "images": None,
            "segments": [{"type": "text", "text": re_exec_append[:500]}],
        })
        new_msgs = await get_history_since(group_id, plugin_since)
        new_msgs_text = format_history(new_msgs)
        inject_parts = [re_exec_append]
        if new_msgs_text:
            inject_parts.append("插件执行期间群友的新发言：")
            inject_parts.append(new_msgs_text)
            inject_parts.append("请结合这些信息，重新构思回复。")
        main_prompt += "\n\n" + "\n".join(inject_parts)

    if ReplyController.is_recalled(group_id, event.message_id):
        # 在真正调用 AI 之前再次检查，因为 prompt 构建阶段可能耗时较长
        logger.info(f"Trigger message {event.message_id} recalled during prep, aborting")
        return

    logger.debug(f"===== AI PROMPT for group {group_id} =====\n{main_prompt}\n===== END PROMPT =====")

    reply_text, reply_json = await _ai_call_with_search(bot, event, group_id, user_name, user_id, plain_text, main_prompt)

    memorize = reply_json.get("memorize")
    if memorize:
        process_memorize(group_id, memorize)

    personal_memorize = reply_json.get("personal_memorize")
    if personal_memorize:
        process_personal_memorize(group_id, personal_memorize)

    ignoreing_reply = reply_json.get("ignoreing_reply")
    ignore_even_at = reply_json.get("ignore_even_at")
    if ignoreing_reply or ignore_even_at:
        process_ignore_fields(group_id, ignoreing_reply, ignore_even_at)

    for _round in range(10):
        # 检查 AI 回复中是否包含插件调用字段，若有则执行插件并重新生成回复
        plugin_val = None
        exec_fn = None
        for p in PluginLoader.plugins:
            v = reply_json.get(p.name)
            if v is not None and isinstance(v, str) and v.strip():
                plugin_val = v
                exec_fn = getattr(p.module, "execute", None)
                break
        if not exec_fn or not plugin_val:
            break
        if reply_text:
            await _send_reply(bot, event, group_id, reply_text, reply_json=reply_json, record_reply=True)
        output = await exec_fn(plugin_val)
        logger.info(f"Plugin executed: {plugin_val}")
        logger.debug(f"Plugin output:\n{output[:1000]}")
        plugin_inject = f"你执行了命令 `{plugin_val}`，输出如下：\n{output[:2000]}"
        main_prompt += "\n\n" + plugin_inject
        await append_message(group_id, {
            "time": int(time.time()), "message_id": 0,
            "user_id": str(bot.self_id), "user_name": "",
            "message": plugin_inject[:500], "images": None,
            "segments": [{"type": "text", "text": plugin_inject[:500]}],
        })
        reply_text, reply_json = await generate_reply(main_prompt)

    if not reply_text:
        logger.info(f"No reply generated for group {group_id}")
        return

    await _send_reply(bot, event, group_id, reply_text, reply_json=reply_json, record_reply=True)


    if reply_text and user_id and user_id != str(bot.self_id) and plain_text:
        # 异步提取对话事实，不阻塞回复发送
        asyncio.create_task(
            extract_facts_from_conversation(
                group_id, user_id, user_name, plain_text, reply_text
            )
        )


async def _ai_call_with_search(
    bot: Bot,
    event: GroupMessageEvent,
    group_id: int,
    user_name: str,
    user_id: str,
    plain_text: str,
    main_prompt: str,
) -> tuple[str, dict]:
    """调用 AI 生成回复，若 AI 请求搜索则进行多轮联网搜索后重新生成"""
    reply_text, reply_json = await generate_reply(main_prompt)

    search_round = 0
    search_query = reply_json.get("search", "")
    if search_query and reply_text:
        # 搜索期间先发送已有回复，搜索完成后再次生成补充回复
        await _send_reply(bot, event, group_id, reply_text, reply_json=reply_json, record_reply=True)

    while search_query and search_round < MAX_SEARCH_ROUNDS:
        if ReplyController.is_recalled(group_id, event.message_id):
            logger.info(f"Trigger message {event.message_id} recalled during search, aborting")
            return reply_text, reply_json

        search_round += 1
        logger.info(f"Search round {search_round}: {search_query}")

        context = f"群 {group_id} 中 {user_name}({user_id}) 说：{plain_text}"

        search_since = int(time.time())
        search_result = await run_search(search_query, context)
        new_msgs = await get_history_since(group_id, search_since)
        new_msgs_text = format_history(new_msgs)

        search_summary = search_result.get("summary", "")
        search_failed = not search_summary
        if search_failed:
            logger.warning("Search returned empty result")

        search_inject = build_search_inject_prompt(
            search_summary=search_summary,
            new_messages_since_search=new_msgs_text,
            search_failed=search_failed,
            current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        enhanced_prompt = main_prompt + "\n\n" + search_inject
        logger.debug(f"===== SEARCH INJECT PROMPT round {search_round} =====\n{search_inject}\n===== END SEARCH INJECT =====")
        reply_text, reply_json = await generate_reply(enhanced_prompt)
        search_query = reply_json.get("search", "")

    return reply_text, reply_json


def _collect_images_from_history(history: list[dict], max_images: int = 3) -> list[tuple[int, int, str]]:
    """从历史消息中倒序收集最近的图片，返回 (消息索引, 段索引, base64) 列表"""
    result: list[tuple[int, int, str]] = []
    # 从新到旧遍历消息，从新到旧遍历每条消息的段，收集最近的图片
    for msg_idx in range(len(history) - 1, -1, -1):
        segs = history[msg_idx].get("segments") or []
        for seg_idx in range(len(segs) - 1, -1, -1):
            seg = segs[seg_idx]
            if seg.get("type") == "image" and seg.get("b64"):
                if len(result) >= max_images:
                    break
                result.append((msg_idx, seg_idx, seg["b64"]))
        if len(result) >= max_images:
            break
    result.reverse()  # 翻转为从旧到新，保持时间顺序
    return result


_mute_cache: dict[int, tuple[bool, float]] = {}


async def _is_bot_muted(bot: Bot, group_id: int) -> bool:
    """检查机器人在群内是否被禁言，结果缓存 30 秒"""
    now = time.time()
    cached = _mute_cache.get(group_id)
    if cached and now - cached[1] < 30:
        return cached[0]

    try:
        self_id = int(bot.self_id)
        info = await bot.get_group_member_info(group_id=group_id, user_id=self_id)
        shut_up = info.get("shut_up_timestamp", 0)
        muted = shut_up > now
        _mute_cache[group_id] = (muted, now)
        if muted:
            logger.info(f"Bot muted in group {group_id} until {shut_up}")
        return muted
    except Exception as e:
        logger.warning(f"Failed to check mute status for group {group_id}: {e}")
        return False


def _append_with_at(segments: list, text: str):
    """将文本中的 [at:qq号] 标记转换为 MessageSegment.at"""
    parts = re.split(r"\[at:(\d+)\]", text)
    for j, p in enumerate(parts):
        if j % 2 == 0:
            if p:
                segments.append(MessageSegment.text(p))
        else:
            segments.append(MessageSegment.at(int(p)))


async def _send_reply(
    bot: Bot,
    event: GroupMessageEvent,
    group_id: int,
    text: str,
    reply_json: dict | None = None,
    record_reply: bool = False,
):
    """发送回复消息，支持 [/] 分段发送和 [at:qq] 引用，可选记录到历史"""
    is_reply_to = (reply_json or {}).get("is_reply_to", False)

    parts = text.split("[/]")
    for pi, part in enumerate(parts):
        segments: list = []
        _append_with_at(segments, part)
        if is_reply_to and pi == 0:
            segments.insert(0, MessageSegment.reply(event.message_id))

        message = segments[0] if len(segments) == 1 else segments
        await bot.send_group_msg(group_id=group_id, message=message)

    if record_reply:
        config = get_config()
        plain = re.sub(r"\[at:\d+\]", "", text)

        plain = plain.replace("[/]", " ").strip()
        reply_record = {
            "time": int(time.time()),
            "user_id": str(bot.self_id),
            "user_name": config.name_cn,
            "message": plain,
            "msg_type": "bot",
            "images": None,
        }
        await append_message(group_id, reply_record)

    logger.info(f"Replied to group {group_id}: {text[:50]}...")


async def _get_reply_user_id(bot: Bot, event: GroupMessageEvent) -> str | None:
    if event.reply:
        try:
            return str(event.reply.sender.user_id)
        except Exception:
            pass
    for seg in event.get_message():
        if seg.type == "reply":
            try:
                msg_id = int(seg.data["id"])
                original = await bot.get_msg(message_id=msg_id)
                sender = original.get("sender", {})
                return str(sender.get("user_id", ""))
            except Exception:
                return None
    return None


async def _get_user_display_name(bot: Bot, group_id: int, user_id: int) -> str:
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return info.get("card") or info.get("nickname", str(user_id))
    except Exception:
        return str(user_id)
