"""
记忆管理命令处理器

支持的命令：
- 记住：XXX - 添加长期记忆
- /memory list - 查看所有长期记忆
- /memory facts - 查看最近的事实记忆
- /memory delete <id> - 删除指定记忆
- /memory clear - 清空所有事实记忆（保留长期记忆）
"""
from __future__ import annotations

import re

from astrabot.chat_service.enhanced_memory import (
    add_long_term_memory,
    delete_long_term_memory,
    get_long_term_memories,
    get_recent_facts,
    delete_fact,
)
from astrabot.logmanager.logger import logger


def handle_memory_command(
    group_id: int,
    user_id: str,
    message: str,
) -> tuple[bool, str]:
    """
    处理记忆相关命令

    返回：(是否处理, 回复消息)
    """
    message = message.strip()


    if message.startswith("记住：") or message.startswith("记住:"):
        content = message[3:].strip()
        if not content:
            return True, "要记住什么呢？"

        if add_long_term_memory(group_id, content, created_by=user_id):
            logger.info(f"User {user_id} added long-term memory: {content}")
            return True, f"好的，我会记住：{content}"
        else:
            return True, "这个我已经记住了"


    if message == "/memory list" or message == "/记忆列表":
        memories = get_long_term_memories(group_id)
        if not memories:
            return True, "还没有长期记忆"

        lines = ["【长期记忆列表】"]
        for m in memories:
            lines.append(f"{m['id']}. {m['content']}")

        return True, "\n".join(lines)


    if message == "/memory facts" or message == "/事实记忆":
        facts = get_recent_facts(group_id, limit=20)
        if not facts:
            return True, "还没有提取到事实记忆"

        lines = ["【最近的事实记忆】"]
        for f in facts:
            access_info = f"(访问{f['access_count']}次)" if f['access_count'] > 0 else ""
            lines.append(f"{f['id']}. {f['fact']} {access_info}")

        return True, "\n".join(lines)


    match = re.match(r'/memory\s+delete\s+(\d+)', message)
    if not match:
        match = re.match(r'/删除记忆\s+(\d+)', message)

    if match:
        memory_id = int(match.group(1))

        # 先尝试删除长期记忆，失败再尝试删除事实记忆
        if delete_long_term_memory(group_id, memory_id):
            logger.info(f"User {user_id} deleted long-term memory {memory_id}")
            return True, f"已删除长期记忆 #{memory_id}"


        if delete_fact(group_id, memory_id):
            logger.info(f"User {user_id} deleted fact {memory_id}")
            return True, f"已删除事实记忆 #{memory_id}"

        return True, f"找不到记忆 #{memory_id}"


    if message == "/memory clear" or message == "/清空事实记忆":

        return True, "此命令会清空所有自动提取的事实记忆（保留长期记忆），请使用 /memory clear confirm 确认"

    if message == "/memory clear confirm":
        from astrabot.chat_service.enhanced_memory import _get_db
        conn = _get_db()
        cur = conn.execute("DELETE FROM facts WHERE group_id = ?", (group_id,))
        conn.commit()
        count = cur.rowcount
        logger.warning(f"User {user_id} cleared {count} facts in group {group_id}")
        return True, f"已清空 {count} 条事实记忆"


    if message == "/memory help" or message == "/记忆帮助":
        help_text = """【记忆管理命令】
记住：XXX - 添加长期记忆
/memory list - 查看所有长期记忆
/memory facts - 查看最近的事实记忆
/memory delete <id> - 删除指定记忆
/memory clear - 清空所有事实记忆
/memory help - 显示此帮助

说明：
• 长期记忆：你明确要求记住的内容，会始终加载
• 事实记忆：AI 自动从对话中提取的事实，按相关性加载"""
        return True, help_text

    return False, ""


def check_implicit_memory_request(message: str) -> str | None:
    """
    检查隐式的记忆请求

    例如："记住我喜欢吃辣" -> "我喜欢吃辣"

    返回：提取的记忆内容，如果不是记忆请求则返回 None
    """
    patterns = [
        r'^记住[，：:]\s*(.+)$',
        r'^记住\s+(.+)$',
        r'^帮我记住[，：:]\s*(.+)$',
        r'^帮我记住\s+(.+)$',
        r'^请记住[，：:]\s*(.+)$',
        r'^请记住\s+(.+)$',
    ]

    for pattern in patterns:
        match = re.match(pattern, message.strip())
        if match:
            return match.group(1).strip()

    return None
