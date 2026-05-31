"""基础记忆管理：基于 JSON 文件存储群记忆、个人印象和忽略列表"""

from __future__ import annotations

import json
from pathlib import Path

from astrabot.logmanager.logger import logger

DATA_DIR = Path(__file__).resolve().parent / "data"
MEMORIES_PATH = DATA_DIR / "memories.json"
IGNORE_PATH = DATA_DIR / "ignores.json"


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load {path}: {e}")
    return {}


def _save_json(path: Path, data: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _gk(group_id: int) -> str:
    """将群 ID 转换为 JSON 中的 key 前缀"""
    return f"group_{group_id}"

_migrated = False


def _migrate():
    """将旧版扁平结构的数据迁移为 {memories: [], users: {}} 嵌套结构"""
    global _migrated
    if _migrated:
        return


    data = _load_json(MEMORIES_PATH)
    needs_save = False
    new_data: dict[str, dict] = {}

    for key, val in data.items():
        if key.endswith("_personal"):
            group_key = key.replace("_personal", "")
            if isinstance(val, dict):
                new_data.setdefault(group_key, {"memories": [], "users": {}})
                new_data[group_key]["users"].update(val)
                needs_save = True
        elif key.endswith("_users"):
            continue
        else:
            if isinstance(val, list):
                cleaned = [s for s in val if s and s.strip()]
                entry = new_data.setdefault(key, {"memories": [], "users": {}})
                entry["memories"] = cleaned
                needs_save = True
            elif isinstance(val, dict) and "memories" not in val:
                entry = new_data.setdefault(key, {"memories": [], "users": {}})
                entry["users"].update(val)
                needs_save = True
            else:
                new_data.setdefault(key, val)

    if needs_save:
        deduped = {}
        for gk, gv in new_data.items():
            seen = []
            for m in gv.get("memories", []):
                if m not in seen:
                    seen.append(m)
            deduped[gk] = {"memories": seen, "users": gv.get("users", {})}
        _save_json(MEMORIES_PATH, deduped)


    ignore_data = _load_json(IGNORE_PATH)
    ignore_needs_save = False
    new_ignore: dict[str, dict] = {}

    for key, val in ignore_data.items():
        if key.endswith("_ignore_reply"):
            gk = key.replace("_ignore_reply", "")
            new_ignore.setdefault(gk, {})
            new_ignore[gk].setdefault("ignore_reply", {}).update(val)
            ignore_needs_save = True
        elif key.endswith("_ignore_even_at"):
            gk = key.replace("_ignore_even_at", "")
            new_ignore.setdefault(gk, {})
            new_ignore[gk].setdefault("ignore_even_at", {}).update(val)
            ignore_needs_save = True
        else:
            new_ignore.setdefault(key, val)

    if ignore_needs_save:
        _save_json(IGNORE_PATH, new_ignore)

    _migrated = True





def get_memories(group_id: int) -> list[str]:
    _migrate()
    data = _load_json(MEMORIES_PATH)
    group = data.get(_gk(group_id), {})
    return group.get("memories", []) if isinstance(group, dict) else []


def add_memory(group_id: int, text: str):
    text = text.strip()
    if not text:
        return
    _migrate()
    data = _load_json(MEMORIES_PATH)
    key = _gk(group_id)
    if key not in data or not isinstance(data[key], dict):
        data[key] = {"memories": [], "users": {}}
    if text not in data[key]["memories"]:
        data[key]["memories"].append(text)
        _save_json(MEMORIES_PATH, data)


def delete_memory(group_id: int, index: int):
    _migrate()
    data = _load_json(MEMORIES_PATH)
    key = _gk(group_id)
    lst = data.get(key, {}).get("memories", []) if isinstance(data.get(key), dict) else []
    zero_idx = index - 1
    if 0 <= zero_idx < len(lst):
        lst.pop(zero_idx)
        data[key]["memories"] = lst
        _save_json(MEMORIES_PATH, data)


def process_memorize(group_id: int, memorize: list):
    """处理 AI 返回的记忆操作：字符串为新增，整数为按序号删除"""
    from astrabot.chat_service.enhanced_memory import add_long_term_memory, delete_long_term_memory, get_long_term_memories
    for item in memorize:
        if isinstance(item, str):
            # 新增记忆，同时写入旧 JSON 存储和新 SQLite 长期记忆
            add_memory(group_id, item)

            add_long_term_memory(group_id, item)
        elif isinstance(item, int):
            # 按 1-based 序号删除记忆，同时清理两个存储中的对应条目

            old_memories = get_memories(group_id)
            zero_idx = item - 1
            old_content = old_memories[zero_idx] if 0 <= zero_idx < len(old_memories) else None
            delete_memory(group_id, item)

            if old_content:
                ltm_list = get_long_term_memories(group_id)
                for ltm in ltm_list:
                    if ltm["content"] == old_content:
                        delete_long_term_memory(group_id, ltm["id"])
                        break





def get_personal_memorize(group_id: int) -> dict[str, str]:
    _migrate()
    data = _load_json(MEMORIES_PATH)
    group = data.get(_gk(group_id), {})
    return group.get("users", {}) if isinstance(group, dict) else {}


def set_personal_memorize(group_id: int, user_id: str, text: str):
    _migrate()
    data = _load_json(MEMORIES_PATH)
    key = _gk(group_id)
    if key not in data or not isinstance(data[key], dict):
        data[key] = {"memories": [], "users": {}}
    data[key]["users"][user_id] = text
    _save_json(MEMORIES_PATH, data)


def process_personal_memorize(group_id: int, items: list[dict]):
    from astrabot.chat_service.enhanced_memory import set_user_impression
    for item in items:
        uid = item.get("user_id", "")
        memo = item.get("memorize", "")
        if uid and memo:
            set_personal_memorize(group_id, uid, memo)

            set_user_impression(group_id, uid, memo)





def _get_ignore_data(group_id: int) -> dict:
    _migrate()
    data = _load_json(IGNORE_PATH)
    return data.get(_gk(group_id), {})


def _save_ignore_data(group_id: int, group_data: dict):
    data = _load_json(IGNORE_PATH)
    data[_gk(group_id)] = group_data
    _save_json(IGNORE_PATH, data)


def get_ignore_reply(group_id: int) -> dict[str, bool]:
    return _get_ignore_data(group_id).get("ignore_reply", {})


def get_ignore_even_at(group_id: int) -> dict[str, bool]:
    return _get_ignore_data(group_id).get("ignore_even_at", {})


def set_ignore_reply(group_id: int, ignore_map: dict[str, bool]):
    group_data = _get_ignore_data(group_id)
    group_data.setdefault("ignore_reply", {}).update(ignore_map)
    _save_ignore_data(group_id, group_data)


def set_ignore_even_at(group_id: int, ignore_map: dict[str, bool]):
    group_data = _get_ignore_data(group_id)
    group_data.setdefault("ignore_even_at", {}).update(ignore_map)
    _save_ignore_data(group_id, group_data)


def process_ignore_fields(group_id: int, ignoreing_reply: dict | None, ignore_even_at: dict | None):
    if ignoreing_reply:
        set_ignore_reply(group_id, ignoreing_reply)
    if ignore_even_at:
        set_ignore_even_at(group_id, ignore_even_at)


def is_user_ignored(group_id: int, user_id: str, is_at: bool) -> bool:
    # ignore_even_at 最高优先级：@ 都不理
    ignore_even_at = get_ignore_even_at(group_id)
    if user_id in ignore_even_at and ignore_even_at[user_id]:
        return True
    # ignore_reply 仅对非 @ 消息生效
    ignore_reply = get_ignore_reply(group_id)
    if not is_at and user_id in ignore_reply and ignore_reply[user_id]:
        return True
    return False
