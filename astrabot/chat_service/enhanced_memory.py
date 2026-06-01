"""
增强的记忆管理系统

三层架构：
1. 短期记忆：最近的对话上下文（已有的 history）
2. 中期记忆：自动提取的事实，按相关性检索
3. 长期记忆：用户明确要求记住的内容，始终加载
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from astrabot.chat_service.ai_client import chat
from astrabot.logmanager.logger import logger

DATA_DIR = Path(__file__).resolve().parent / "data"
MEMORY_DB = DATA_DIR / "memories.db"
_local = threading.local()


def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _local.conn = sqlite3.connect(str(MEMORY_DB))
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn: sqlite3.Connection):
    """初始化数据库表"""

    conn.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id TEXT,
            fact TEXT NOT NULL,
            keywords TEXT,
            created_at INTEGER NOT NULL,
            access_count INTEGER DEFAULT 0,
            last_accessed INTEGER,
            importance INTEGER DEFAULT 1,
            UNIQUE(group_id, fact)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_group ON facts(group_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_keywords ON facts(keywords)")


    conn.execute("""
        CREATE TABLE IF NOT EXISTS long_term_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            created_by TEXT,
            UNIQUE(group_id, content)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ltm_group ON long_term_memories(group_id)")


    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_impressions (
            group_id INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            impression TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (group_id, user_id)
        )
    """)

    conn.commit()




async def extract_facts_from_conversation(
    group_id: int,
    user_id: str,
    user_name: str,
    user_msg: str,
    ai_reply: str,
) -> list[str]:
    """从对话中提取事实，返回提取的事实列表"""
    try:
        prompt = f"""从以下对话中提取值得记住的事实信息。

对话：
{user_name}: {user_msg[:500]}
AI: {ai_reply[:500]}

提取规则：
1. 只提取稳定的、有价值的信息：
   - 个人偏好、习惯、性格
   - 重要事件、约定
   - 个人背景、技能
   - 群内规则、共识
2. 不提取：
   - 随口说的话、玩笑
   - 情绪化表达
   - 日常寒暄
3. 每条事实用一句话表述，包含主语
4. 如果没有值得记录的，返回空数组

输出格式：纯 JSON 数组
["事实1", "事实2"]"""

        messages = [{"role": "user", "content": prompt}]
        result = await chat(
            provider="MINIMAX",
            model="MiniMax-M2.7",
            messages=messages,
            timeout=15,
        )

        content = result.get("content", "").strip()
        if not content:
            return []


        if content.startswith("```json"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        facts = json.loads(content)
        if not isinstance(facts, list):
            return []


        stored = []
        for fact in facts:
            if not isinstance(fact, str) or not fact.strip():
                continue
            if add_fact(group_id, fact, user_id=user_id):
                stored.append(fact)

        if stored:
            logger.info(f"Extracted {len(stored)} facts from {user_name}({user_id})")

        return stored

    except json.JSONDecodeError as e:
        logger.debug(f"Fact extraction JSON parse failed: {e}")
        return []
    except Exception as e:
        logger.debug(f"Fact extraction failed: {e}")
        return []


def add_fact(
    group_id: int,
    fact: str,
    user_id: str | None = None,
    importance: int = 1,
) -> bool:
    """添加一条事实记忆"""
    fact = fact.strip()
    if not fact:
        return False


    keywords = _extract_keywords(fact)

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO facts (group_id, user_id, fact, keywords, created_at, importance)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (group_id, user_id, fact, keywords, int(time.time()), importance),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        # 事实已存在（UNIQUE 约束），改为增加访问计数
        conn.execute(
            """UPDATE facts SET access_count = access_count + 1, last_accessed = ?
               WHERE group_id = ? AND fact = ?""",
            (int(time.time()), group_id, fact),
        )
        conn.commit()
        return False


def _extract_keywords(text: str) -> str:
    """提取关键词（简单实现，可以用 jieba 等分词库改进）"""

    text = re.sub('[，。！？、；：\u201c\u201d\u2018\u2019（）《》【】\\s]+', ' ', text)

    words = text.split()

    keywords = [w for w in words if len(w) >= 2]
    return ' '.join(keywords[:10])


def search_relevant_facts(
    group_id: int,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """根据查询搜索相关的事实记忆"""
    keywords = _extract_keywords(query)
    if not keywords:
        return []

    conn = _get_db()
    conn.row_factory = sqlite3.Row

    # 对每个关键词做 LIKE 模糊匹配，OR 连接
    keyword_list = keywords.split()
    conditions = " OR ".join(["keywords LIKE ?" for _ in keyword_list])
    params = [f"%{kw}%" for kw in keyword_list] + [group_id, limit]

    cur = conn.execute(
        f"""SELECT id, fact, user_id, created_at, access_count, importance
            FROM facts
            WHERE ({conditions}) AND group_id = ?
            ORDER BY importance DESC, access_count DESC, created_at DESC
            LIMIT ?""",
        params,
    )

    results = []
    for row in cur.fetchall():
        results.append(dict(row))
        # 每次检索命中就增加访问计数，实现"越常用越靠前"的排序
        conn.execute(
            "UPDATE facts SET access_count = access_count + 1, last_accessed = ? WHERE id = ?",
            (int(time.time()), row["id"]),
        )

    conn.commit()
    return results


def get_recent_facts(group_id: int, limit: int = 10) -> list[dict]:
    """获取最近的事实记忆"""
    conn = _get_db()
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        """SELECT id, fact, user_id, created_at, access_count, importance
           FROM facts
           WHERE group_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (group_id, limit),
    )

    return [dict(row) for row in cur.fetchall()]


def delete_fact(group_id: int, fact_id: int) -> bool:
    """删除一条事实记忆"""
    conn = _get_db()
    cur = conn.execute(
        "DELETE FROM facts WHERE group_id = ? AND id = ?",
        (group_id, fact_id),
    )
    conn.commit()
    return cur.rowcount > 0




def add_long_term_memory(
    group_id: int,
    content: str,
    created_by: str | None = None,
) -> bool:
    """添加长期记忆"""
    content = content.strip()
    if not content:
        return False

    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO long_term_memories (group_id, content, created_at, created_by)
               VALUES (?, ?, ?, ?)""",
            (group_id, content, int(time.time()), created_by),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def get_long_term_memories(group_id: int) -> list[dict]:
    """获取所有长期记忆"""
    conn = _get_db()
    conn.row_factory = sqlite3.Row

    cur = conn.execute(
        """SELECT id, content, created_at, created_by
           FROM long_term_memories
           WHERE group_id = ?
           ORDER BY created_at ASC""",
        (group_id,),
    )

    return [dict(row) for row in cur.fetchall()]


def delete_long_term_memory(group_id: int, memory_id: int) -> bool:
    """删除长期记忆"""
    conn = _get_db()
    cur = conn.execute(
        "DELETE FROM long_term_memories WHERE group_id = ? AND id = ?",
        (group_id, memory_id),
    )
    conn.commit()
    return cur.rowcount > 0




def set_user_impression(
    group_id: int,
    user_id: str,
    impression: str,
):
    """设置对用户的印象"""
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO user_impressions (group_id, user_id, impression, updated_at)
           VALUES (?, ?, ?, ?)""",
        (group_id, user_id, impression, int(time.time())),
    )
    conn.commit()


def get_user_impression(group_id: int, user_id: str) -> str | None:
    """获取对用户的印象"""
    conn = _get_db()
    cur = conn.execute(
        "SELECT impression FROM user_impressions WHERE group_id = ? AND user_id = ?",
        (group_id, user_id),
    )
    row = cur.fetchone()
    return row[0] if row else None


def get_all_user_impressions(group_id: int) -> dict[str, str]:
    """获取所有用户印象"""
    conn = _get_db()
    cur = conn.execute(
        "SELECT user_id, impression FROM user_impressions WHERE group_id = ?",
        (group_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}




def build_memory_context(
    group_id: int,
    current_message: str,
    relevant_user_ids: set[str],
) -> dict[str, str]:
    """构建记忆上下文，返回各部分记忆文本"""


    ltm = get_long_term_memories(group_id)
    ltm_text = "\n".join(
        f"{i+1}. [{datetime.fromtimestamp(m['created_at']).strftime('%m-%d %H:%M')}] {m['content']}"
        for i, m in enumerate(ltm)
    ) if ltm else ""

    # 中期记忆：取最近的事实，不依赖关键词搜索（中文分词效果差）
    recent_facts = get_recent_facts(group_id, limit=10)
    facts_text = "\n".join(
        f"- [{datetime.fromtimestamp(f['created_at']).strftime('%m-%d %H:%M')}] {f['fact']}"
        for f in recent_facts
    ) if recent_facts else ""


    impressions = []
    for user_id in relevant_user_ids:
        impression = get_user_impression(group_id, user_id)
        if impression:
            impressions.append(f"- {user_id}: {impression}")
    impressions_text = "\n".join(impressions) if impressions else ""

    return {
        "long_term": ltm_text,
        "facts": facts_text,
        "impressions": impressions_text,
    }




def migrate_from_old_memory_system():
    """从旧的 memories.json 迁移数据"""
    from astrabot.chat_service.memory_manager import _load_json, MEMORIES_PATH

    data = _load_json(MEMORIES_PATH)
    migrated_count = 0

    for key, value in data.items():
        if not key.startswith("group_"):
            continue

        try:
            group_id = int(key.replace("group_", ""))
        except ValueError:
            continue

        if isinstance(value, dict):

            memories = value.get("memories", [])
            for mem in memories:
                if add_long_term_memory(group_id, mem):
                    migrated_count += 1


            users = value.get("users", {})
            for user_id, impression in users.items():
                set_user_impression(group_id, user_id, impression)

    logger.info(f"Migrated {migrated_count} memories from old system")
