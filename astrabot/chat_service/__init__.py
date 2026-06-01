"""AstraBot 聊天服务入口：注册群消息和撤回事件处理器，加载插件"""

from __future__ import annotations

from nonebot import on_message, on_notice
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, NoticeEvent

from astrabot.chat_service.desktop_notify import send_desktop_notification
from astrabot.chat_service.history_manager import delete_by_message_id
from astrabot.chat_service.message_handler import handle_group_message
from astrabot.chat_service.plugin_loader import PluginLoader
from astrabot.chat_service.reply_controller import ReplyController
from astrabot.logmanager.logger import logger

reply = on_message(priority=10)


@reply.handle()
async def handle(bot: Bot, event: GroupMessageEvent):
    await handle_group_message(bot, event)


notice_handler = on_notice()


@notice_handler.handle()
async def handle_notice(bot: Bot, event: NoticeEvent):
    data = event.model_dump()
    notice_type = data.get("notice_type")

    if notice_type == "group_recall":
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

    elif notice_type == "bot_offline":
        msg = data.get("message", "")
        tag = data.get("tag", "")
        logger.warning(f"Bot offline detected: tag={tag}, message={msg}")
        send_desktop_notification("AstraBot 离线通知", "QQ凭证过期，请重新登录")


PluginLoader.load_all()
