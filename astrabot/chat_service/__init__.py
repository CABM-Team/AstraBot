"""AstraBot 聊天服务入口：注册群消息和撤回事件处理器，加载插件"""

from __future__ import annotations

from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, NoticeEvent

from astrabot.chat_service.history_manager import delete_by_message_id
from astrabot.chat_service.message_handler import handle_group_message
from astrabot.chat_service.plugin_loader import PluginLoader
from astrabot.chat_service.reply_controller import ReplyController
from astrabot.logmanager.logger import logger

reply = on_message(priority=10)


@reply.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    await handle_group_message(bot, event)


recall = on_notice()


@recall.handle()
async def handle_recall(bot: Bot, event: NoticeEvent):
    """处理群消息撤回：标记已撤回并从历史记录中删除对应消息"""
    data = event.model_dump()
    if data.get("notice_type") != "group_recall":
        return
    group_id = data.get("group_id")
    message_id = data.get("message_id")
    if not group_id or not message_id:
        return

    ReplyController.add_recalled(group_id, message_id)
    deleted = await delete_by_message_id(group_id, message_id)
    if deleted:
        logger.info(f"Removed recalled message {message_id} from history in group {group_id}")
    else:
        logger.debug(f"Recalled message {message_id} not found in history (group {group_id})")


PluginLoader.load_all()
