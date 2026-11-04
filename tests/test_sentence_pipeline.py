import pathlib
import sys
import base64
import io
import wave

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.utils.sentence_pipeline import (
    pcm16_base64_chunks_duration_ms,
    pcm16_base64_chunks_to_wav_base64,
    split_sentence_buffer,
)
from core.utils.speech_planner import build_speech_plan, strip_mimo_tts_markup


def test_split_sentence_buffer_with_strong_punctuation():
    sentences, remaining = split_sentence_buffer(
        "你好呀。今天过得怎么样？我还不错！",
        soft_limit=28,
        hard_limit=40,
        min_sentence_chars=8,
        force_flush=False,
    )
    assert sentences == ["你好呀。", "今天过得怎么样？ 我还不错！"]
    assert remaining == ""


def test_split_sentence_buffer_with_force_flush():
    sentences, remaining = split_sentence_buffer(
        "这是一段没有句号但是需要在结尾强制刷出的文本",
        soft_limit=28,
        hard_limit=40,
        min_sentence_chars=8,
        force_flush=True,
    )
    assert sentences == ["这是一段没有句号但是需要在结尾强制刷出的文本"]
    assert remaining == ""


def test_split_sentence_buffer_flushes_on_natural_tone_breakpoint():
    sentences, remaining = split_sentence_buffer(
        "我先想跟你说一件事情～后面还有很多内容会继续流式输出",
        soft_limit=12,
        hard_limit=40,
        min_sentence_chars=8,
        force_flush=False,
    )
    assert sentences == ["我先想跟你说一件事情～"]
    assert remaining == "后面还有很多内容会继续流式输出"


def test_split_sentence_buffer_flushes_on_tone_particle_after_threshold():
    sentences, remaining = split_sentence_buffer(
        "我知道你现在有点不开心啦这件事我们可以慢慢说",
        soft_limit=12,
        hard_limit=40,
        min_sentence_chars=8,
        force_flush=False,
    )
    assert sentences == ["我知道你现在有点不开心啦"]
    assert remaining == "这件事我们可以慢慢说"


def test_split_sentence_buffer_merges_short_list_items():
    sentences, remaining = split_sentence_buffer(
        "可以试试这些： - 散散步 - 喝点水 - 找朋友聊聊",
        soft_limit=28,
        hard_limit=40,
        min_sentence_chars=8,
        force_flush=True,
    )
    assert remaining == ""
    assert len(sentences) <= 2
    assert "散散步" in " ".join(sentences)


def test_mimo_display_text_strips_style_and_audio_tags():
    text = "<style>温柔</style>（小声）我在。（停顿片刻）你慢慢说。"
    assert strip_mimo_tts_markup(text) == "我在。你慢慢说。"


def test_speech_plan_keeps_tts_tags_but_cleans_display_text():
    plan = build_speech_plan("<style>开心</style>（紧张，深呼吸）呼……没事。")
    assert plan["display_text"] == "呼……没事。"
    assert plan["assistant_text"] == "<style>开心</style>（紧张，深呼吸）呼……没事。"
    assert plan["tts_style"] == "开心"


def test_pcm16_base64_chunks_to_wav_base64_wraps_audio():
    pcm_chunk_1 = base64.b64encode(b"\x00\x00\x01\x00").decode("ascii")
    pcm_chunk_2 = base64.b64encode(b"\xff\xff\x00\x00").decode("ascii")

    wav_bytes = base64.b64decode(
        pcm16_base64_chunks_to_wav_base64(
            [pcm_chunk_1, pcm_chunk_2],
            sample_rate=24000,
            channels=1,
        )
    )

    assert wav_bytes[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 24000
        assert wav_file.getsampwidth() == 2
        assert wav_file.readframes(3) == b"\x00\x00\x01\x00\xff\xff"


def test_pcm16_base64_chunks_duration_ms():
    # 24 kHz, mono, pcm16 => 48 bytes/ms.
    one_ms_pcm = b"\x00\x00" * 24
    chunks = [
        base64.b64encode(one_ms_pcm).decode("ascii"),
        base64.b64encode(one_ms_pcm * 2).decode("ascii"),
    ]
    assert pcm16_base64_chunks_duration_ms(chunks, sample_rate=24000, channels=1) == 3
