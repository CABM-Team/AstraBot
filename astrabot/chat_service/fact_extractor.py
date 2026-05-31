"""事实提取器：从对话中用 AI 提取用户偏好、习惯等稳定事实并存入记忆"""

from __future__ import annotations

import hashlib
import json

from astrabot.chat_service.ai_client import chat
from astrabot.chat_service.memory_manager import add_memory, set_personal_memorize
from astrabot.logmanager.logger import logger

EXTRACT_PROMPT = """从以下对话中提取关于 {user_name} 的稳定事实。

要求：
- 只提取：明显的个人偏好、性格特征、重要事件、长期约定、个人背景
- 不提取：随口说的、情绪化表达、玩笑、日常寒暄
- 每条事实用一句话表述
- 如果没有值得记录的内容，返回空数组

输出格式：纯 JSON 数组，不要用 ```json 包裹。
["事实1", "事实2"]"""


async def extract_and_store(group_id: int, user_id: str, user_name: str, user_msg: str, ai_reply: str):
    """从单轮对话中提取事实并存储到群记忆"""
    try:
        prompt = EXTRACT_PROMPT.format(user_name=user_name)
        messages = [
            {"role": "user", "content": f"用户说：{user_msg[:500]}\n\nAI回复：{ai_reply[:500]}\n\n{prompt}"},
        ]
        result = await chat(
            provider="MINIMAX",
            model="MiniMax-M2.7",
            messages=messages,
            timeout=15,
        )
        content = result.get("content", "")
        if not content:
            return

        from astrabot.chat_service.ai_client import _clean_json
        content = _clean_json(content)
        facts = json.loads(content)
        if not isinstance(facts, list):
            return

        for fact in facts:
            if not isinstance(fact, str) or not fact.strip():
                continue
            add_memory(group_id, fact)

        logger.debug(f"Extracted {len(facts)} facts from {user_name}({user_id})")

    except json.JSONDecodeError:
        logger.debug(f"Fact extraction JSON parse failed for {user_name}")
    except Exception as e:
        logger.debug(f"Fact extraction failed for {user_name}: {e}")
