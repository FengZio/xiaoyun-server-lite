import base64
import io
import re
import queue
import wave
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple


STRONG_PUNCTUATIONS = "。！？!?；;"
SOFT_PUNCTUATIONS = "，、：, \n\t~～…—"
TONE_BREAK_CHARS = "呀啊吧呢啦喔哦嘛了咯"


def pcm16_base64_chunks_to_wav_bytes(
    chunks: List[str],
    sample_rate: int = 24000,
    channels: int = 1,
) -> bytes:
    pcm_bytes = b"".join(base64.b64decode(chunk) for chunk in chunks if chunk)
    if not pcm_bytes:
        raise ValueError("empty pcm16 audio chunks")
    if len(pcm_bytes) % 2 != 0:
        raise ValueError(f"invalid pcm16 byte length: {len(pcm_bytes)}")

    with io.BytesIO() as output:
        with wave.open(output, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return output.getvalue()


def pcm16_base64_chunks_to_wav_base64(
    chunks: List[str],
    sample_rate: int = 24000,
    channels: int = 1,
) -> str:
    return base64.b64encode(
        pcm16_base64_chunks_to_wav_bytes(chunks, sample_rate, channels)
    ).decode("ascii")


def pcm16_base64_chunks_duration_ms(
    chunks: List[str],
    sample_rate: int = 24000,
    channels: int = 1,
) -> int:
    pcm_byte_count = sum(len(base64.b64decode(chunk)) for chunk in chunks if chunk)
    bytes_per_second = sample_rate * channels * 2
    if pcm_byte_count <= 0 or bytes_per_second <= 0:
        return 0
    return int(round(pcm_byte_count * 1000 / bytes_per_second))


@dataclass
class SentenceUnit:
    message_id: str
    sentence_index: int
    display_text: str
    speak_text: str
    is_final: bool
    reply_meta: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    audio_skipped: bool = False
    audio_failed: bool = False
    audio_sent: bool = False
    text_sent: bool = False
    audio_url: str | None = None
    audio_base64: str | None = None
    audio_format: str | None = None
    speech_plan: Dict[str, Any] = field(default_factory=dict)
    audio_path: str | None = None
    audio_chunks: Any = field(default_factory=queue.Queue)
    audio_stream_started: bool = False
    audio_stream_done: bool = False
    audio_started_sent: bool = False
    audio_chunk_count: int = 0
    audio_failure_reason: str | None = None
    audio_start_ms: int | None = None
    audio_end_ms: int | None = None
    audio_duration_ms: int | None = None
    audio_ready_timestamp_ms: int | None = None
    audio_sent_timestamp_ms: int | None = None


@dataclass
class ReplyTurn:
    message_id: str
    session_id: str
    mode: str
    send_text_chunks: bool
    full_display_text: str = ""
    next_sentence_index: int = 0
    llm_completed: bool = False
    aborted: bool = False
    reply_started: bool = False
    text_final_sent: bool = False
    reply_end_sent: bool = False
    next_audio_dispatch_index: int = 0
    rewrite_failures: int = 0
    rewrite_disabled: bool = False
    audio_dispatch_done: bool = False
    first_audio_sent: bool = False
    turn_style: str | None = None
    sentences: Dict[int, SentenceUnit] = field(default_factory=dict)
    audio_terminal_count: int = 0
    sent_audio_files: List[str] = field(default_factory=list)
    audio_timeline_ms: int = 0


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _should_merge_with_previous(sentence: str, min_sentence_chars: int) -> bool:
    normalized = _normalize_text(sentence)
    if not normalized:
        return False
    if len(normalized) < min_sentence_chars:
        return True
    if normalized.startswith(("-", "•", "*")):
        return True
    if re.match(r"^\d+[\.、]\s*", normalized):
        return True
    return False


def _merge_sentences(sentences: List[str], min_sentence_chars: int) -> List[str]:
    merged: List[str] = []
    for sentence in sentences:
        normalized = _normalize_text(sentence)
        if not normalized:
            continue
        if merged and _should_merge_with_previous(normalized, min_sentence_chars):
            merged[-1] = _normalize_text(f"{merged[-1]} {normalized}")
            continue
        merged.append(normalized)

    if len(merged) >= 2 and len(merged[-1]) < min_sentence_chars:
        merged[-2] = _normalize_text(f"{merged[-2]} {merged[-1]}")
        merged.pop()
    return merged


def split_sentence_buffer(
    buffer: str,
    *,
    soft_limit: int,
    hard_limit: int,
    min_sentence_chars: int = 8,
    force_flush: bool = False,
) -> Tuple[List[str], str]:
    text = str(buffer or "")
    if not text:
        return [], ""

    results: List[str] = []
    start = 0
    last_soft_cut = -1
    index = 0
    text_len = len(text)

    while index < text_len:
        char = text[index]
        chunk_len = index - start + 1

        if char in SOFT_PUNCTUATIONS:
            last_soft_cut = index

        is_strong_cut = char in STRONG_PUNCTUATIONS
        is_paragraph_cut = text[index : index + 2] == "\n\n"
        should_flush = False
        cut_index = -1

        if is_strong_cut:
            should_flush = True
            cut_index = index
        elif is_paragraph_cut:
            should_flush = True
            cut_index = index + 1
        elif chunk_len >= hard_limit:
            should_flush = True
            cut_index = last_soft_cut if last_soft_cut >= start else index
        elif chunk_len >= soft_limit and char in TONE_BREAK_CHARS:
            should_flush = True
            cut_index = index
        elif chunk_len >= soft_limit and last_soft_cut >= start:
            should_flush = True
            cut_index = last_soft_cut

        if should_flush:
            sentence = _normalize_text(text[start : cut_index + 1])
            if sentence:
                results.append(sentence)
            start = cut_index + 1
            while start < text_len and text[start] in "\n ":
                start += 1
            index = start
            last_soft_cut = -1
            continue

        index += 1

    remaining = text[start:]
    remaining = remaining.lstrip("\n")
    if force_flush:
        final_sentence = _normalize_text(remaining)
        if final_sentence:
            results.append(final_sentence)
        remaining = ""
    return _merge_sentences(results, min_sentence_chars=min_sentence_chars), remaining
