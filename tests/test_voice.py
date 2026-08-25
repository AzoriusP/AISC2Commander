from __future__ import annotations

import wave
from types import SimpleNamespace

import pytest

from aisc2commander.agent.voice import (
    OpenAITranscriber,
    StreamingWavRecorder,
    VoiceActivitySegmenter,
    VoiceCommandListener,
)
from aisc2commander.voice_terms import (
    normalize_sc2_transcript,
    transcription_hotwords,
)
from scripts.transcribe_local import _transcribe as transcribe_local


class _FakeAudio:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm

    def tobytes(self) -> bytes:
        return self.pcm


class _FakeInputStream:
    def __init__(self, **kwargs) -> None:
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        self.started = True
        one_second_pcm = b"\x00\x00" * 16_000
        self.callback(_FakeAudio(one_second_pcm), 16_000, None, None)

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


def test_streaming_recorder_runs_until_explicit_stop_and_writes_wav() -> None:
    streams: list[_FakeInputStream] = []

    def factory(**kwargs):
        stream = _FakeInputStream(**kwargs)
        streams.append(stream)
        return stream

    recorder = StreamingWavRecorder(stream_factory=factory)
    recorder.start()
    assert recorder.is_recording
    assert streams[0].started

    path = recorder.stop()
    try:
        assert not recorder.is_recording
        assert streams[0].stopped
        assert streams[0].closed
        with wave.open(str(path), "rb") as audio:
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2
            assert audio.getframerate() == 16_000
            assert audio.getnframes() == 16_000
    finally:
        path.unlink(missing_ok=True)


def test_streaming_recorder_rejects_double_start() -> None:
    recorder = StreamingWavRecorder(stream_factory=lambda **kwargs: _FakeInputStream(**kwargs))
    recorder.start()
    try:
        with pytest.raises(RuntimeError, match="已经开始"):
            recorder.start()
    finally:
        recorder.cancel()


def _pcm_chunk(value: int, frames: int = 480) -> bytes:
    return int(value).to_bytes(2, "little", signed=True) * frames


def test_voice_activity_segmenter_splits_after_sentence_silence() -> None:
    segmenter = VoiceActivitySegmenter(
        silence_seconds=0.3,
        minimum_speech_seconds=0.15,
        pre_roll_seconds=0.06,
        minimum_rms=0.01,
        calibration_seconds=0,
    )
    completed: list[bytes] = []
    for _ in range(5):
        completed.extend(segmenter.feed(_pcm_chunk(0)))
    for _ in range(10):
        completed.extend(segmenter.feed(_pcm_chunk(4_000)))
    assert segmenter.is_speaking
    for _ in range(11):
        completed.extend(segmenter.feed(_pcm_chunk(0)))

    assert len(completed) == 1
    assert not segmenter.is_speaking
    assert len(completed[0]) >= int(0.6 * 16_000 * 2)


def test_voice_command_listener_emits_wav_and_keeps_listening_until_stop() -> None:
    paths = []

    class SentenceInputStream(_FakeInputStream):
        def start(self) -> None:
            self.started = True
            for _ in range(4):
                self.callback(_FakeAudio(_pcm_chunk(0)), 480, None, None)
            for _ in range(10):
                self.callback(_FakeAudio(_pcm_chunk(4_000)), 480, None, None)
            for _ in range(12):
                self.callback(_FakeAudio(_pcm_chunk(0)), 480, None, None)

    listener = VoiceCommandListener(
        paths.append,
        silence_seconds=0.3,
        minimum_speech_seconds=0.15,
        minimum_rms=0.01,
        calibration_seconds=0,
        stream_factory=lambda **kwargs: SentenceInputStream(**kwargs),
    )
    listener.start()
    assert listener.is_listening
    listener.stop()

    assert not listener.is_listening
    assert listener.segment_count == 1
    assert len(paths) == 1
    try:
        with wave.open(str(paths[0]), "rb") as audio:
            assert audio.getframerate() == 16_000
            assert audio.getnframes() > 0
    finally:
        paths[0].unlink(missing_ok=True)


def test_voice_activity_segmenter_calibrates_noise_and_uses_it_as_sentence_silence() -> None:
    segmenter = VoiceActivitySegmenter(
        silence_seconds=0.3,
        minimum_speech_seconds=0.15,
        maximum_utterance_seconds=2.0,
        minimum_rms=0.008,
        calibration_seconds=0.3,
        noise_multiplier=2.5,
        release_multiplier=1.6,
    )
    ambient = _pcm_chunk(400)
    for _ in range(10):
        assert segmenter.feed(ambient) == ()
    assert not segmenter.is_calibrating
    assert not segmenter.is_speaking
    assert segmenter.noise_rms > 0.01

    for _ in range(10):
        assert segmenter.feed(_pcm_chunk(4_000)) == ()
    assert segmenter.is_speaking
    completed: list[bytes] = []
    for _ in range(11):
        completed.extend(segmenter.feed(ambient))
    assert len(completed) == 1
    assert not segmenter.is_speaking


def test_voice_activity_segmenter_force_splits_when_noise_never_reaches_silence() -> None:
    segmenter = VoiceActivitySegmenter(
        silence_seconds=0.3,
        minimum_speech_seconds=0.15,
        maximum_utterance_seconds=0.75,
        minimum_rms=0.008,
        calibration_seconds=0.3,
        noise_multiplier=2.5,
        release_multiplier=1.6,
    )
    for _ in range(10):
        segmenter.feed(_pcm_chunk(400))
    completed: list[bytes] = []
    for _ in range(10):
        completed.extend(segmenter.feed(_pcm_chunk(4_000)))
    # This persistent noise stays above the release threshold, so only the hard
    # utterance limit can prevent the listener from remaining in speech forever.
    for _ in range(20):
        completed.extend(segmenter.feed(_pcm_chunk(1_000)))
        if completed:
            break
    assert len(completed) == 1
    assert not segmenter.is_speaking


def test_openai_transcriber_uses_llm_audio_transcription() -> None:
    captured: dict[str, object] = {}

    class FakeTranscriptions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="让所有枪兵向右移动十格")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    transcriber = OpenAITranscriber(model="gpt-transcribe", client=client)

    import tempfile
    from pathlib import Path

    handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    path = Path(handle.name)
    handle.write(b"RIFF-test")
    handle.close()
    try:
        text = transcriber.transcribe(path)
    finally:
        path.unlink(missing_ok=True)

    assert text == "让所有枪兵向右移动十格"
    assert captured["model"] == "gpt-transcribe"
    assert captured["language"] == "zh"
    assert "星际争霸2" in str(captured["prompt"])
    assert "English tactical commands" not in str(captured["prompt"])


def test_openai_transcriber_uses_fixed_english_without_language_detection(tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeTranscriptions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(text="Move the selected Marines to A1")

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=FakeTranscriptions()))
    transcriber = OpenAITranscriber(
        model="gpt-transcribe",
        client=client,
        language="en",
    )
    path = tmp_path / "command.wav"
    path.write_bytes(b"RIFF-test")

    assert transcriber.transcribe(path) == "Move the selected Marines to A1"
    assert captured["language"] == "en"
    assert "English tactical commands" in str(captured["prompt"])
    assert "中文战术指令" not in str(captured["prompt"])


def test_transcriber_rejects_automatic_language_detection() -> None:
    with pytest.raises(ValueError, match="zh or en"):
        OpenAITranscriber(client=SimpleNamespace(), language="auto")


def test_sc2_voice_terms_prioritize_domain_words_and_fix_logged_homophones() -> None:
    hotwords = transcription_hotwords("zh")
    assert "采气" in hotwords
    assert "精炼厂" in hotwords
    assert "京恋场" not in hotwords

    assert normalize_sc2_transcript("选一个农民去附近建京恋场。") == (
        "选一个农民去附近建精炼厂。"
    )
    assert normalize_sc2_transcript("选三个农民去采计") == "选三个农民去采气"
    assert normalize_sc2_transcript("选一个农民去旁边建材气场") == (
        "选一个农民去旁边建造精炼厂"
    )
    # “采集”本身是合法命令，不能无条件猜成“采气”。
    assert normalize_sc2_transcript("让农民采集") == "让农民采集"


def test_local_whisper_receives_sc2_hotwords_and_expanded_prompt(tmp_path) -> None:
    captured: dict[str, object] = {}

    class FakeModel:
        def transcribe(self, path, **kwargs):
            captured.update(kwargs)
            return [SimpleNamespace(text=" 选两个农民去采气")], SimpleNamespace(language="zh")

    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF-test")
    payload = transcribe_local(FakeModel(), audio, "zh")

    assert payload["text"] == "选两个农民去采气"
    assert captured["beam_size"] == 8
    assert "采气" in str(captured["hotwords"])
    assert "精炼厂" in str(captured["hotwords"])
    assert "精炼厂" in str(captured["initial_prompt"])
    assert "准确区分采气、采矿、采集" in str(captured["initial_prompt"])
