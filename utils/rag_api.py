import aiohttp
from typing import Optional, List, Tuple, AsyncGenerator

RAG_API_URL = "http://rag.sqrfund.ai/api/chat/message"

async def send_chat_message_to_rag_api(
    message: str,
    api_key: str,
    conversation_id: Optional[str] = None,
    files: Optional[List[Tuple[str, bytes, str]]] = None,  # (filename, file_bytes, mime_type)
    fallback_context: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """
    Sends a chat message to the rag.sqrfund.ai API and yields response chunks as they arrive.
    - message: The user message to send.
    - api_key: The API key for authentication.
    - conversation_id: Optional conversation ID for context continuity.
    - files: Optional list of files to attach (filename, bytes, mime_type).
    - fallback_context: Optional context to send if no context is retrieved.
    Yields: Chunks of the streaming response as they arrive.
    Raises: Exception on error (with details from API if available).
    """
    headers = {
        "X-API-Key": api_key,
    }
    data = {
        "message": message,
    }
    if conversation_id:
        data["conversation_id"] = str(conversation_id)
    if fallback_context:
        data["context"] = fallback_context

    form = aiohttp.FormData()
    for k, v in data.items():
        form.add_field(k, v)
    if files:
        for filename, file_bytes, mime_type in files:
            form.add_field(
                "files[]",
                file_bytes,
                filename=filename,
                content_type=mime_type
            )

    async with aiohttp.ClientSession() as session:
        async with session.post(RAG_API_URL, headers=headers, data=form) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if resp.status == 200 and content_type.startswith("text/plain"):
                async for chunk, _ in resp.content.iter_chunks():
                    if chunk:
                        yield chunk.decode("utf-8", errors="replace")
            else:
                # Try to parse error JSON
                try:
                    error_json = await resp.json()
                    error_msg = error_json.get("error") or str(error_json)
                except Exception:
                    error_msg = await resp.text()
                raise Exception(f"RAG API error ({resp.status}): {error_msg}") 