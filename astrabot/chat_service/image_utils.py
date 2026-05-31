"""图片工具：下载图片并转换为 base64 编码"""

from __future__ import annotations

import base64
import os

import httpx

from astrabot.logmanager.logger import logger


def _get_httpx_client(timeout: int = 15) -> httpx.AsyncClient:
    """创建配置好代理的 httpx 客户端"""

    proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
    if proxy and proxy.startswith("socks://"):
        proxy = proxy.replace("socks://", "socks5://", 1)

    if proxy:
        return httpx.AsyncClient(proxy=proxy, timeout=timeout)
    return httpx.AsyncClient(timeout=timeout)


async def download_image_base64(url: str, timeout: int = 15) -> str | None:
    client = _get_httpx_client(timeout=timeout)
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        data = resp.content
        b64 = base64.b64encode(data).decode("utf-8")
        return b64
    except Exception as e:
        logger.error(f"Failed to download image {url}: {e}")
        return None
    finally:
        await client.aclose()
