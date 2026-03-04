"""
Shared helpers for Meta-platform adapters (WhatsApp, Instagram, Messenger).

All three use the Meta Graph API with the same webhook signature verification,
media download flow, and POST conventions.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

import aiohttp

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v21.0"


def verify_webhook_signature(
    payload: bytes,
    signature: str,
    app_secret: str,
) -> bool:
    """
    Verify the X-Hub-Signature-256 header sent by Meta on every webhook POST.

    Parameters
    ----------
    payload : bytes
        The raw request body.
    signature : str
        The value of the X-Hub-Signature-256 header (e.g. "sha256=abc123...").
    app_secret : str
        The Meta App Secret used as the HMAC key.

    Returns
    -------
    bool
        True if the signature is valid.
    """
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(
        app_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    received = signature.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


async def send_graph_api(
    endpoint: str,
    token: str,
    data: dict,
    session: aiohttp.ClientSession,
) -> dict:
    """
    POST JSON to the Meta Graph API.

    Parameters
    ----------
    endpoint : str
        Path after the version prefix, e.g. "{phone_number_id}/messages".
    token : str
        The page / system-user access token.
    data : dict
        JSON body to send.
    session : aiohttp.ClientSession
        Shared HTTP session.

    Returns
    -------
    dict
        Parsed JSON response.
    """
    url = f"{GRAPH_API_BASE}/{endpoint}"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.post(url, json=data, headers=headers) as resp:
        body = await resp.json()
        if resp.status >= 400:
            logger.error("Graph API error %s %s: %s", resp.status, endpoint, body)
        return body


async def download_media(
    media_id: str,
    token: str,
    session: aiohttp.ClientSession,
) -> bytes:
    """
    Download media from Meta (two-step: get URL, then download bytes).

    Step 1: GET /{media_id} to obtain the download URL.
    Step 2: GET the URL with the Bearer token to download the binary data.

    Parameters
    ----------
    media_id : str
        The media object ID from the incoming message.
    token : str
        Access token.
    session : aiohttp.ClientSession
        Shared HTTP session.

    Returns
    -------
    bytes
        Raw media bytes.
    """
    # Step 1: resolve media URL
    url = f"{GRAPH_API_BASE}/{media_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with session.get(url, headers=headers) as resp:
        meta = await resp.json()
        media_url = meta["url"]

    # Step 2: download binary
    async with session.get(media_url, headers=headers) as resp:
        return await resp.read()
