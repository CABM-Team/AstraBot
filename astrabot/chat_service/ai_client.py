"""AI 客户端：统一封装 chat 与图片分析调用"""

from __future__ import annotations

import json
import os

import anthropic
import httpx
from openai import AsyncOpenAI

from astrabot.config import get_config
from astrabot.logmanager.logger import logger

API_BASE_URLS = {
    "SILICONFLOW": "https://api.siliconflow.cn/v1",
    "DEEPSEEK": "https://api.deepseek.com/v1",
    "MINIMAX": "https://api.minimaxi.com/anthropic",
}


def _get_httpx_client() -> httpx.AsyncClient:
    """创建配置好代理的 httpx 客户端"""

    proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
    if proxy and proxy.startswith("socks://"):
        proxy = proxy.replace("socks://", "socks5://", 1)

    if proxy:
        return httpx.AsyncClient(proxy=proxy, timeout=30.0)
    return httpx.AsyncClient(timeout=30.0)


def _create_openai_client(base_url: str, api_key: str, http_client: httpx.AsyncClient) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)


def _create_anthropic_client(base_url: str, api_key: str, http_client: httpx.AsyncClient) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(base_url=base_url, api_key=api_key, http_client=http_client)


async def chat(
    protocol: str,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    response_format: str | None = None,
    reasoning: bool = False,
    timeout: int = 30,
) -> dict:
    protocol_upper = protocol.upper()

    if protocol_upper == "OPENAI":
        http_client = _get_httpx_client()
        client = _create_openai_client(base_url=base_url, api_key=api_key, http_client=http_client)
        try:
            kwargs = {
                "model": model,
                "messages": messages,
                "timeout": timeout,
            }
            if response_format == "json_object":
                kwargs["response_format"] = {"type": "json_object"}
            if reasoning:
                kwargs["reasoning_effort"] = "high"
            response = await client.chat.completions.create(**kwargs)
            msg = response.choices[0].message
            content = msg.content or ""
            reasoning_content = getattr(msg, "reasoning_content", None)
            return {"content": content, "reasoning": reasoning_content}
        finally:
            await http_client.aclose()

    if protocol_upper == "ANTHROPIC":
        http_client = _get_httpx_client()
        client = _create_anthropic_client(base_url=base_url, api_key=api_key, http_client=http_client)
        try:
            anthropic_messages = [m for m in messages if m["role"] != "system"]
            system_content = next((m["content"] for m in messages if m["role"] == "system"), None)
            kwargs = {
                "model": model,
                "max_tokens": 4096,
                "messages": anthropic_messages,
            }
            if system_content:
                kwargs["system"] = system_content
            message = await client.messages.create(**kwargs)
            content = ""
            for block in message.content:
                if block.type == "text":
                    content += block.text
            return {"content": content}
        finally:
            await http_client.aclose()

    raise ValueError(f"Unknown protocol: {protocol}")


async def analyze_images(
    images_base64: list[str],
    prompt: str,
) -> dict:
    config = get_config()
    provider = config.VISUAL_API_PROVIDER
    model = config.VISUAL_API_MODEL

    base_url = API_BASE_URLS.get(provider.upper(), API_BASE_URLS["SILICONFLOW"])
    api_key = config.VISUAL_API_KEY

    http_client = _get_httpx_client()
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

    content_parts = [{"type": "text", "text": prompt}]
    for b64 in images_base64:
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })

    messages = [{"role": "user", "content": content_parts}]

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            timeout=60,
        )
        content = response.choices[0].message.content or "{}"
        return json.loads(content)
    except Exception as e:
        logger.error(f"Image analysis failed: {e}")
        return {}
    finally:
        await http_client.aclose()


def _clean_json(raw: str) -> str:
    """去除 AI 输出中可能包裹的 markdown 代码块标记"""
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    elif raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    return raw.strip()


RETRY_LIMIT = 3


async def generate_reply(
    prompt: str,
) -> tuple[str, dict]:
    """调用主/备 AI 生成 JSON 格式回复，自动重试与降级"""
    config = get_config()

    messages = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.append({"role": "user", "content": prompt})

    for protocol, base_url, api_key, model in [
        (config.API_PROTOCOL, config.API_BASE_URL, config.API_KEY, config.API_MODEL),
        (config.BACK_API_PROTOCOL, config.BACK_API_BASE_URL, config.BACK_API_KEY, config.BACK_API_MODEL),
    ]:
        for retry in range(RETRY_LIMIT):
            try:
                logger.debug(f"AI call attempt: protocol={protocol}, base_url={base_url}, model={model} (retry {retry + 1}/{RETRY_LIMIT})")
                result = await chat(
                    protocol=protocol,
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    messages=messages,
                    response_format="json_object",
                    reasoning=(protocol.upper() == "OPENAI"),
                    timeout=30,
                )
                content = result.get("content", "")
                if not content:
                    logger.warning(f"Empty response from {protocol}, retrying...")
                    continue

                reasoning = result.get("reasoning")
                if reasoning:
                    logger.debug(f"Reasoning from {protocol}:\n{reasoning}")

                logger.debug(f"Raw AI response from {protocol}:\n{content}")

                content = _clean_json(content)
                parsed = json.loads(content)
                reply_text = parsed.get("reply", "")
                if reply_text:
                    return reply_text, parsed
                if "reply" in parsed:
                    return "", parsed
                logger.warning(f"Empty reply field from {protocol}, retrying...")
                continue
            except json.JSONDecodeError as e:
                logger.warning(f"JSON decode error from {protocol}: {e}")
                logger.warning(f"Raw content that failed to parse:\n{content}")
                continue
            except Exception as e:
                logger.warning(f"AI call failed ({protocol}): {e}, retrying...")
                continue

    return "", {}
