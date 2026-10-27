"""Twilio Programmable Voice adapter: webhook signatures, TwiML, Media Streams, and call control."""

import base64
import hashlib
import hmac
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape, quoteattr

import httpx

if TYPE_CHECKING:
    from services.voice.omni_voice.runtime import VoiceOutput

PROVIDER = "twilio"
# Twilio Media Streams always carry 8 kHz mono G.711 mu-law in both directions.
TWILIO_CODEC = "mulaw"
TWILIO_SAMPLE_RATE_HZ = 8000
TERMINAL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


def twilio_signature(auth_token: str, url: str, params: Iterable[tuple[str, str]]) -> str:
    """Compute X-Twilio-Signature: HMAC-SHA1 over the URL plus sorted form fields."""

    payload = url + "".join(key + value for key, value in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def verify_twilio_signature(auth_token: str, url: str, params: Iterable[tuple[str, str]], signature: str) -> bool:
    return hmac.compare_digest(twilio_signature(auth_token, url, params), signature)


def twiml(*verbs: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?><Response>' + "".join(verbs) + "</Response>"


def connect_stream_twiml(stream_url: str, token: str) -> str:
    # Twilio drops query strings on Stream URLs, so the media token travels as a
    # custom parameter and arrives in the stream's `start` message.
    return twiml(
        f"<Connect><Stream url={quoteattr(stream_url)}>"
        f'<Parameter name="token" value={quoteattr(token)} />'
        "</Stream></Connect>"
    )


def unavailable_twiml(transfer_number: str | None) -> str:
    if transfer_number:
        return twiml(
            "<Say>Our assistant is unavailable right now. Connecting you with a person.</Say>",
            f"<Dial>{escape(transfer_number)}</Dial>",
        )
    return twiml(
        "<Say>Our assistant is unavailable right now. Please call back during business hours.</Say>", "<Hangup/>"
    )


def dial_twiml(transfer_number: str) -> str:
    return twiml(f"<Dial>{escape(transfer_number)}</Dial>")


class TwilioClient:
    """Minimal REST client for in-progress call control (transfer and hang-up)."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        base_url: str = "https://api.twilio.com",
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10,
    ):
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    async def update_call(self, call_sid: str, **fields: str) -> None:
        url = f"{self.base_url}/2010-04-01/Accounts/{self.account_sid}/Calls/{call_sid}.json"
        async with httpx.AsyncClient(transport=self.transport, timeout=self.timeout_seconds) as client:
            response = await client.post(url, data=fields, auth=(self.account_sid, self.auth_token))
        response.raise_for_status()

    async def transfer(self, call_sid: str, transfer_number: str) -> None:
        await self.update_call(call_sid, Twiml=dial_twiml(transfer_number))

    async def hang_up(self, call_sid: str) -> None:
        await self.update_call(call_sid, Status="completed")


def to_internal_frame(message: dict) -> dict | None:
    """Translate one Twilio Media Streams message into the gateway's frame protocol."""

    event = message.get("event")
    if event == "media":
        media = message.get("media") or {}
        if media.get("track", "inbound") != "inbound":
            return None
        payload = str(media.get("payload", ""))
        size = len(base64.b64decode(payload, validate=True)) if payload else 0
        return {"type": "audio", "audio": payload, "duration_ms": max(1, size * 1000 // TWILIO_SAMPLE_RATE_HZ)}
    if event == "dtmf":
        return {"type": "dtmf", "digit": str((message.get("dtmf") or {}).get("digit", ""))}
    if event == "mark":
        name = str((message.get("mark") or {}).get("name", ""))
        _, _, generation = name.rpartition("-")
        return {"type": "playback_complete", "generation_id": int(generation) if generation.isdigit() else None}
    if event == "stop":
        return {"type": "hangup"}
    return None


@dataclass(frozen=True)
class PendingAction:
    kind: str  # "transfer" or "hang_up"
    transfer_number: str | None = None


def to_twilio_messages(output: "VoiceOutput", stream_sid: str, *, end_call: bool) -> list[dict]:
    """Render a runtime output as Twilio messages.

    Playback ends with a mark whose name carries the generation. Twilio echoes
    the mark once audio has played, which is when transfers and hang-ups run,
    so callers hear the whole goodbye or handoff line first.
    """

    if output.event_type == "clear_playback":
        return [{"event": "clear", "streamSid": stream_sid}]
    messages = [
        {"event": "media", "streamSid": stream_sid, "media": {"payload": base64.b64encode(chunk).decode()}}
        for chunk in output.audio_chunks or []
        if chunk
    ]
    if output.generation_id is not None:
        if output.event_type == "transfer":
            prefix = "transfer"
        elif end_call:
            prefix = "hangup"
        else:
            prefix = "gen"
        messages.append(
            {"event": "mark", "streamSid": stream_sid, "mark": {"name": f"{prefix}-{output.generation_id}"}}
        )
    return messages


def mark_action(message: dict, pending: dict[int, PendingAction]) -> PendingAction | None:
    if message.get("event") != "mark":
        return None
    name = str((message.get("mark") or {}).get("name", ""))
    _, _, generation = name.rpartition("-")
    return pending.pop(int(generation), None) if generation.isdigit() else None
