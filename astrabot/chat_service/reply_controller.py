"""回复控制器：管理各群的回复状态、概率判断、排队消息和撤回标记"""

from __future__ import annotations

import asyncio
import random

from astrabot.config import Config


class ReplyController:
    _locks: dict[int, asyncio.Lock] = {}
    _reply_status: dict[int, bool] = {}
    _pending_at: dict[int, list[dict]] = {}  # 回复期间被 @ 的排队消息
    _recalled_messages: set[tuple[int, int]] = set()  # 已撤回的 (group_id, message_id)
    @classmethod
    def get_lock(cls, group_id: int) -> asyncio.Lock:
        if group_id not in cls._locks:
            cls._locks[group_id] = asyncio.Lock()
        return cls._locks[group_id]

    @classmethod
    def is_replying(cls, group_id: int) -> bool:
        return cls._reply_status.get(group_id, False)

    @classmethod
    def set_replying(cls, group_id: int, status: bool):
        cls._reply_status[group_id] = status

    @classmethod
    def add_pending_at(cls, group_id: int, info: dict):
        if group_id not in cls._pending_at:
            cls._pending_at[group_id] = []
        cls._pending_at[group_id].append(info)

    @classmethod
    def add_recalled(cls, group_id: int, message_id: int):
        cls._recalled_messages.add((group_id, message_id))

    @classmethod
    def is_recalled(cls, group_id: int, message_id: int) -> bool:
        return (group_id, message_id) in cls._recalled_messages

    @classmethod
    def pop_pending_at(cls, group_id: int) -> list[dict]:
        return cls._pending_at.pop(group_id, [])


def should_reply(
    is_at: bool,
    is_replying: bool,
    config: Config,
    is_keyword: bool = False,
) -> bool:
    """根据触发方式和回复状态，按不同概率决定是否回复"""
    triggered = is_at or is_keyword
    # 四种概率：被@时回复中 / 被@时空闲 / 普通回复中 / 普通空闲
    if triggered and is_replying:
        rate = config.reply_rate_at_in_reply
    elif triggered:
        rate = config.reply_rate_at
    elif is_replying:
        rate = config.reply_rate_in_reply
    else:
        rate = config.reply_rate
    return random.random() < rate
