"""联网搜索：通过 MCP + DeepSeek tool calling 实现多轮搜索、网页抓取和结果总结"""

from __future__ import annotations

import asyncio
import json
import os
from html.parser import HTMLParser

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI

from astrabot.config import get_config
from astrabot.logmanager.logger import logger

MAX_TOOL_CALLS = 10  # 搜索代理最大工具调用次数

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for real-time information. Use 3-5 keywords for best results. Include current date for time-sensitive topics.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query, 3-5 keywords"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and read the content of a webpage to get detailed information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL of the webpage to fetch"}
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "respond",
            "description": "Call this when you have enough information to give a final answer to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Final response to the user"}
                },
                "required": ["message"],
            },
        },
    },
]

def _build_system_prompt() -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y年%m月%d日")
    return f"""你是一个智能搜索助手。你的任务是：
1. 分析用户的问题，理解他们需要什么信息
2. 使用 web_search 工具搜索相关关键词
3. 如果搜索结果不够详细，使用 fetch_url 获取具体网页内容
4. 如果搜索结果不理想，尝试不同的关键词重新搜索
5. 当你收集到足够信息后，使用 respond 工具给出完整的回答

搜索时注意：
- 对于有时效性的问题，在搜索关键词中包含当前日期
- 优先查看权威来源的内容
- 综合多个来源的信息给出答案

当前日期：{now}"""


def _extract_text(html: str) -> str:
    class TextExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.result = []
            self.skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style"):
                self.skip = True

        def handle_endtag(self, tag):
            if tag in ("script", "style"):
                self.skip = False

        def handle_data(self, data):
            if not self.skip:
                stripped = data.strip()
                if stripped:
                    self.result.append(stripped)

    extractor = TextExtractor()
    extractor.feed(html)
    return "\n".join(extractor.result)


def _get_httpx_client(timeout: int = 15) -> httpx.AsyncClient:
    """创建配置好代理的 httpx 客户端"""

    proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
    if proxy and proxy.startswith("socks://"):
        proxy = proxy.replace("socks://", "socks5://", 1)

    if proxy:
        return httpx.AsyncClient(proxy=proxy, timeout=timeout)
    return httpx.AsyncClient(timeout=timeout)


async def fetch_url(url: str) -> str:
    client = _get_httpx_client(timeout=15)
    try:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.encoding = "utf-8"
        content = _extract_text(resp.text)
        return content[:8000]
    except Exception as e:
        logger.error(f"fetch_url failed {url}: {e}")
        return f"Failed to fetch URL: {e}"
    finally:
        await client.aclose()


async def run_search(initial_query: str, context: str) -> dict:
    """启动搜索代理：通过 MCP 调用 web_search，用 DeepSeek 进行多轮 tool calling 搜索"""
    config = get_config()
    summary_result: dict = {"summary": "", "sources": []}

    http_client = _get_httpx_client(timeout=20)
    client = AsyncOpenAI(
        api_key=config.API_KEY,
        base_url=config.API_BASE_URL,
        http_client=http_client,
    )

    server_params = StdioServerParameters(
        command="uvx",
        args=["minimax-coding-plan-mcp", "-y"],
        env={
            "MINIMAX_API_KEY": config.MINIMAX_API_KEY,
            "MINIMAX_API_HOST": config.MINIMAX_API_HOST,
        },
    )

    try:
        logger.debug("Starting MCP stdio client...")
        async with stdio_client(server_params) as (read, write):
            logger.debug("MCP stdio client connected")
            async with ClientSession(read, write) as session:
                logger.debug("MCP session created, initializing...")
                await session.initialize()
                logger.debug("MCP session initialized")

                messages = [
                    {"role": "system", "content": _build_system_prompt()},
                    {"role": "user", "content": f"用户的问题是：{initial_query}\n\n对话背景：{context}"},
                ]

                tool_calls_count = 0

                while tool_calls_count < MAX_TOOL_CALLS:
                    response = await client.chat.completions.create(
                        model=config.API_MODEL,
                        messages=messages,
                        tools=TOOLS,
                        timeout=20,
                    )

                    msg = response.choices[0].message

                    if not msg.tool_calls:
                        messages.append({"role": "assistant", "content": msg.content or ""})
                        continue

                    assistant_msg = {
                        "role": "assistant",
                        "content": msg.content or "",
                        "tool_calls": [
                            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in msg.tool_calls
                        ],
                    }
                    messages.append(assistant_msg)

                    for tc in msg.tool_calls:
                        tool_calls_count += 1
                        name = tc.function.name
                        args = json.loads(tc.function.arguments)

                        if name == "respond":
                            msg_text = args.get("message", "")
                            logger.info(f"Search agent: respond({len(msg_text)} chars)")
                            logger.debug(f"Respond content: {msg_text[:500]}")
                            summary_result["summary"] = msg_text
                            return summary_result

                        if name == "web_search":
                            query = args["query"]
                            logger.info(f"Search agent: web_search({query})")
                            try:
                                result = await session.call_tool("web_search", arguments={"query": query})
                                raw_text = result.content[0].text if hasattr(result.content[0], "text") else str(result.content[0])
                                result_text = raw_text
                            except Exception as e:
                                logger.error(f"web_search failed: {e}")
                                result_text = f"web_search failed: {e}"
                        elif name == "fetch_url":
                            url = args["url"]
                            logger.info(f"Search agent: fetch_url({url})")
                            result_text = await fetch_url(url)
                        else:
                            result_text = f"Unknown tool: {name}"

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result_text,
                        })

                logger.warning(f"Search agent reached max tool calls ({MAX_TOOL_CALLS}), forcing summary")
                force_prompt = "你已进行多轮搜索，请根据已获取的信息，用 respond 工具给出最终总结回答。"
                messages.append({"role": "user", "content": force_prompt})
                response = await client.chat.completions.create(
                    model=config.API_MODEL,
                    messages=messages,
                    tools=TOOLS,
                    timeout=20,
                )
                msg = response.choices[0].message
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        if tc.function.name == "respond":
                            forced = json.loads(tc.function.arguments).get("message", "")
                            logger.info(f"Search agent force respond ({len(forced)} chars)")
                            logger.debug(f"Forced respond content: {forced[:500]}")
                            summary_result["summary"] = forced
                elif msg.content:
                    logger.info(f"Search agent force respond (text, {len(msg.content)} chars)")
                    summary_result["summary"] = msg.content

    except* Exception as eg:
        # Python 3.11+ ExceptionGroup：MCP stdio 关闭时可能抛出多异常
        import traceback
        logger.error(f"Search agent full traceback:\n{''.join(traceback.format_exception(type(eg), eg, eg.__traceback__))}")
    finally:
        await http_client.aclose()

    return summary_result
