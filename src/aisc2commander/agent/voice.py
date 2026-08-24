from __future__ import annotations

import logging
import os
import json
import queue
import subprocess
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path
from typing import Any, Callable


LOG = logging.getLogger(__name__)


def list_input_devices() -> tuple[str, ...]:
    import sounddevice as sd

    lines: list[str] = []
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) > 0:
            lines.append(
                f"[{index}] {device['name']} inputs={device['max_input_channels']} "
                f"default_rate={device['default_samplerate']:.0f}"
            )
    return tuple(lines)


def record_wav(
    duration: float,
    *,
    sample_rate: int = 16_000,
    device: int | str | None = None,
) -> Path:
    if duration < 0.5 or duration > 15.0:
        raise ValueError("voice duration must be between 0.5 and 15 seconds")
    import sounddevice as sd

    frame_count = int(duration * sample_rate)
    LOG.info("Voice recording started: duration=%.1fs rate=%d device=%s", duration, sample_rate, device)
    audio = sd.rec(
        frame_count,
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        device=device,
    )
    sd.wait()
    handle, raw_path = tempfile.mkstemp(prefix="aisc2-voice-", suffix=".wav")
    os.close(handle)
    path = Path(raw_path)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(audio.tobytes())
    LOG.info("Voice recording completed: bytes=%d", path.stat().st_size)
    return path


class StreamingWavRecorder:
    """Capture microphone PCM until the caller explicitly stops recording."""

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        device: int | str | None = None,
        stream_factory: Any | None = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stream_factory = stream_factory
        self._stream: Any | None = None
        self._frames = bytearray()
        self._lock = threading.Lock()
        self._accepting = False
        self._started_at = 0.0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None and self._accepting

    @property
    def elapsed_seconds(self) -> float:
        if not self.is_recording:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("录音已经开始")
        if self._stream_factory is None:
            import sounddevice as sd

            factory = sd.InputStream
        else:
            factory = self._stream_factory
        with self._lock:
            self._frames.clear()
            self._accepting = True

        def audio_callback(indata: Any, _frames: int, _time_info: Any, status: Any) -> None:
            if status:
                LOG.warning("Microphone stream status: %s", status)
            data = indata.tobytes()
            with self._lock:
                if self._accepting:
                    self._frames.extend(data)

        stream = factory(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            device=self.device,
            callback=audio_callback,
        )
        self._stream = stream
        try:
            stream.start()
        except Exception:
            with self._lock:
                self._accepting = False
            self._stream = None
            stream.close()
            raise
        self._started_at = time.monotonic()
        LOG.info("Continuous voice recording started: rate=%d device=%s", self.sample_rate, self.device)

    def stop(self, *, minimum_seconds: float = 0.2) -> Path:
        stream = self._stream
        if stream is None:
            raise RuntimeError("录音尚未开始")
        with self._lock:
            self._accepting = False
        try:
            stream.stop()
        finally:
            try:
                stream.close()
            finally:
                self._stream = None
        with self._lock:
            pcm = bytes(self._frames)
            self._frames.clear()
        duration = len(pcm) / (self.sample_rate * 2)
        if duration < minimum_seconds:
            raise ValueError(f"录音太短（{duration:.2f} 秒），请重新录制")
        path = _write_pcm_wav(pcm, self.sample_rate)
        LOG.info(
            "Continuous voice recording stopped: duration=%.2fs bytes=%d path=%s",
            duration,
            path.stat().st_size,
            path,
        )
        return path

    def cancel(self) -> None:
        stream = self._stream
        with self._lock:
            self._accepting = False
            self._frames.clear()
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()


class VoiceActivitySegmenter:
    """Split mono int16 PCM into utterances using adaptive local energy VAD."""

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        silence_seconds: float = 0.7,
        minimum_speech_seconds: float = 0.25,
        maximum_utterance_seconds: float = 10.0,
        pre_roll_seconds: float = 0.35,
        minimum_rms: float = 0.008,
        calibration_seconds: float = 1.0,
        noise_multiplier: float = 2.5,
        release_multiplier: float = 1.6,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if silence_seconds < 0.2:
            raise ValueError("silence_seconds must be at least 0.2")
        if minimum_speech_seconds <= 0:
            raise ValueError("minimum_speech_seconds must be positive")
        if maximum_utterance_seconds <= minimum_speech_seconds:
            raise ValueError("maximum_utterance_seconds must exceed minimum_speech_seconds")
        if minimum_rms <= 0 or minimum_rms >= 1:
            raise ValueError("minimum_rms must be between 0 and 1")
        if calibration_seconds < 0 or calibration_seconds > 5:
            raise ValueError("calibration_seconds must be between 0 and 5")
        if noise_multiplier <= 1:
            raise ValueError("noise_multiplier must exceed 1")
        if release_multiplier <= 1 or release_multiplier >= noise_multiplier:
            raise ValueError("release_multiplier must be above 1 and below noise_multiplier")
        self.sample_rate = sample_rate
        self.silence_seconds = silence_seconds
        self.minimum_speech_seconds = minimum_speech_seconds
        self.maximum_utterance_seconds = maximum_utterance_seconds
        self.pre_roll_seconds = pre_roll_seconds
        self.minimum_rms = minimum_rms
        self.calibration_seconds = calibration_seconds
        self.noise_multiplier = noise_multiplier
        self.release_multiplier = release_multiplier
        self._bytes_per_second = sample_rate * 2
        self._pre_roll_limit = int(pre_roll_seconds * self._bytes_per_second)
        self._pre_roll: deque[bytes] = deque()
        self._pre_roll_size = 0
        self._utterance = bytearray()
        self._voiced_bytes = 0
        self._silence_bytes = 0
        self._noise_rms = minimum_rms / 3.0
        self._calibration_bytes = int(calibration_seconds * self._bytes_per_second)
        self._calibration_remaining = self._calibration_bytes
        self._calibration_samples: list[float] = []
        self._speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def is_calibrating(self) -> bool:
        return self._calibration_remaining > 0

    @property
    def noise_rms(self) -> float:
        return self._noise_rms

    @property
    def threshold(self) -> float:
        return max(self.minimum_rms, self._noise_rms * self.noise_multiplier)

    @property
    def release_threshold(self) -> float:
        return max(self.minimum_rms * 0.75, self._noise_rms * self.release_multiplier)

    def feed(self, pcm: bytes) -> tuple[bytes, ...]:
        if not pcm:
            return ()
        if len(pcm) % 2:
            raise ValueError("PCM byte length must be even for int16 audio")
        rms = _pcm_rms(pcm)
        if self._calibration_remaining > 0:
            self._calibration_samples.append(rms)
            self._calibration_remaining = max(0, self._calibration_remaining - len(pcm))
            if self._calibration_remaining == 0:
                ordered = sorted(self._calibration_samples)
                if ordered:
                    middle = len(ordered) // 2
                    self._noise_rms = (
                        ordered[middle]
                        if len(ordered) % 2
                        else (ordered[middle - 1] + ordered[middle]) / 2.0
                    )
                self._calibration_samples.clear()
                self._pre_roll.clear()
                self._pre_roll_size = 0
                LOG.info(
                    "Voice VAD calibrated: noise_rms=%.5f activation=%.5f release=%.5f",
                    self._noise_rms,
                    self.threshold,
                    self.release_threshold,
                )
            return ()
        threshold = self.threshold
        if not self._speaking:
            self._append_pre_roll(pcm)
            if rms >= threshold:
                self._speaking = True
                self._utterance.extend(b"".join(self._pre_roll))
                self._pre_roll.clear()
                self._pre_roll_size = 0
                self._voiced_bytes = len(pcm)
                self._silence_bytes = 0
                return ()
            # Adapt only while idle so speech cannot raise the noise floor.
            self._noise_rms = self._noise_rms * 0.96 + rms * 0.04
            return ()

        self._utterance.extend(pcm)
        if rms >= self.release_threshold:
            self._voiced_bytes += len(pcm)
            self._silence_bytes = 0
        else:
            self._silence_bytes += len(pcm)
            # A trailing quiet block is a safe, slow update of the ambient floor.
            self._noise_rms = self._noise_rms * 0.98 + rms * 0.02
        elapsed = len(self._utterance) / self._bytes_per_second
        silent = self._silence_bytes / self._bytes_per_second
        if silent >= self.silence_seconds or elapsed >= self.maximum_utterance_seconds:
            completed = self._finish_utterance()
            return (completed,) if completed else ()
        return ()

    def flush(self) -> tuple[bytes, ...]:
        completed = self._finish_utterance()
        self._pre_roll.clear()
        self._pre_roll_size = 0
        return (completed,) if completed else ()

    def reset(self) -> None:
        self._pre_roll.clear()
        self._pre_roll_size = 0
        self._utterance.clear()
        self._voiced_bytes = 0
        self._silence_bytes = 0
        self._speaking = False
        self._calibration_remaining = self._calibration_bytes
        self._calibration_samples.clear()
        self._noise_rms = self.minimum_rms / 3.0

    def _append_pre_roll(self, pcm: bytes) -> None:
        self._pre_roll.append(pcm)
        self._pre_roll_size += len(pcm)
        while self._pre_roll and self._pre_roll_size > self._pre_roll_limit:
            self._pre_roll_size -= len(self._pre_roll.popleft())

    def _finish_utterance(self) -> bytes:
        voiced_seconds = self._voiced_bytes / self._bytes_per_second
        completed = bytes(self._utterance) if voiced_seconds >= self.minimum_speech_seconds else b""
        self._utterance.clear()
        self._voiced_bytes = 0
        self._silence_bytes = 0
        self._speaking = False
        return completed


class VoiceCommandListener:
    """Continuously listen and emit sentence-sized WAV files without blocking PortAudio."""

    def __init__(
        self,
        on_segment: Callable[[Path], None],
        *,
        sample_rate: int = 16_000,
        device: int | str | None = None,
        silence_seconds: float = 0.7,
        minimum_speech_seconds: float = 0.25,
        maximum_utterance_seconds: float = 10.0,
        minimum_rms: float = 0.008,
        calibration_seconds: float = 1.0,
        noise_multiplier: float = 2.5,
        release_multiplier: float = 1.6,
        stream_factory: Any | None = None,
    ) -> None:
        self.on_segment = on_segment
        self.sample_rate = sample_rate
        self.device = device
        self.segmenter = VoiceActivitySegmenter(
            sample_rate=sample_rate,
            silence_seconds=silence_seconds,
            minimum_speech_seconds=minimum_speech_seconds,
            maximum_utterance_seconds=maximum_utterance_seconds,
            minimum_rms=minimum_rms,
            calibration_seconds=calibration_seconds,
            noise_multiplier=noise_multiplier,
            release_multiplier=release_multiplier,
        )
        self._stream_factory = stream_factory
        self._stream: Any | None = None
        self._accepting = False
        self._started_at = 0.0
        self._segment_count = 0
        self._lock = threading.Lock()
        self._segments: queue.Queue[bytes | None] = queue.Queue(maxsize=8)
        self._worker: threading.Thread | None = None

    @property
    def is_listening(self) -> bool:
        return self._stream is not None and self._accepting

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self.segmenter.is_speaking

    @property
    def is_calibrating(self) -> bool:
        with self._lock:
            return self.segmenter.is_calibrating

    @property
    def elapsed_seconds(self) -> float:
        if not self.is_listening:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    @property
    def segment_count(self) -> int:
        return self._segment_count

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("语音监听已经开始")
        if self._stream_factory is None:
            import sounddevice as sd

            factory = sd.InputStream
        else:
            factory = self._stream_factory
        self.segmenter.reset()
        self._segment_count = 0
        self._accepting = True
        self._worker = threading.Thread(
            target=self._deliver_segments,
            name="voice-segment-writer",
            daemon=True,
        )
        self._worker.start()

        def audio_callback(indata: Any, _frames: int, _time_info: Any, status: Any) -> None:
            if status:
                LOG.warning("Microphone listener status: %s", status)
            with self._lock:
                if not self._accepting:
                    return
                completed = self.segmenter.feed(indata.tobytes())
            for pcm in completed:
                self._queue_segment(pcm)

        try:
            stream = factory(
                samplerate=self.sample_rate,
                blocksize=max(1, int(self.sample_rate * 0.03)),
                channels=1,
                dtype="int16",
                device=self.device,
                callback=audio_callback,
            )
        except Exception:
            self._accepting = False
            self._segments.put(None)
            if self._worker is not None:
                self._worker.join(timeout=1.0)
            self._worker = None
            raise
        self._stream = stream
        try:
            stream.start()
        except Exception:
            self._accepting = False
            self._stream = None
            self._segments.put(None)
            stream.close()
            if self._worker is not None:
                self._worker.join(timeout=1.0)
            self._worker = None
            raise
        self._started_at = time.monotonic()
        LOG.info(
            "Continuous voice command listener started: rate=%d device=%s silence=%.2fs "
            "rms=%.4f calibration=%.2fs noise_multiplier=%.2f release_multiplier=%.2f",
            self.sample_rate,
            self.device,
            self.segmenter.silence_seconds,
            self.segmenter.minimum_rms,
            self.segmenter.calibration_seconds,
            self.segmenter.noise_multiplier,
            self.segmenter.release_multiplier,
        )

    def stop(self, *, flush: bool = True) -> None:
        stream = self._stream
        if stream is None:
            return
        with self._lock:
            self._accepting = False
        try:
            stream.stop()
        finally:
            stream.close()
            self._stream = None
        with self._lock:
            completed = self.segmenter.flush() if flush else ()
            if not flush:
                self.segmenter.reset()
        for pcm in completed:
            self._queue_segment(pcm)
        self._segments.put(None)
        worker = self._worker
        if worker is not None:
            worker.join(timeout=3.0)
        self._worker = None
        LOG.info("Continuous voice command listener stopped: segments=%d", self._segment_count)

    def cancel(self) -> None:
        self.stop(flush=False)

    def _queue_segment(self, pcm: bytes) -> None:
        try:
            self._segments.put_nowait(pcm)
        except queue.Full:
            LOG.error("Voice segment queue is full; dropping one utterance")

    def _deliver_segments(self) -> None:
        while True:
            pcm = self._segments.get()
            if pcm is None:
                return
            path = _write_pcm_wav(pcm, self.sample_rate)
            try:
                self.on_segment(path)
            except Exception:
                path.unlink(missing_ok=True)
                LOG.exception("Voice segment callback failed")
            else:
                self._segment_count += 1


def _pcm_rms(pcm: bytes) -> float:
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16)
    if not samples.size:
        return 0.0
    values = samples.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(values * values)))


def _write_pcm_wav(pcm: bytes, sample_rate: int) -> Path:
    handle, raw_path = tempfile.mkstemp(prefix="aisc2-voice-", suffix=".wav")
    os.close(handle)
    path = Path(raw_path)
    try:
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


class OpenAITranscriber:
    def __init__(
        self,
        model: str = "gpt-transcribe",
        client: Any | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model = model
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=api_key) if api_key else OpenAI()
        self.client = client

    def transcribe(self, path: Path) -> str:
        with path.open("rb") as audio_file:
            result = self.client.audio.transcriptions.create(
                model=self.model,
                file=audio_file,
                language="zh",
                prompt=(
                    "星际争霸2 中文战术指令。常见词：陆战队员、枪兵、劫掠者、兵营、"
                    "指挥中心、移动、攻击、生产、坐标。"
                ),
            )
        text = str(result.text).strip()
        if not text:
            raise ValueError("语音转写结果为空")
        return text


def local_whisper_server_command(project_root: Path, model: str) -> list[str]:
    """Build the resident Whisper command for bundled and source installs."""

    helper = project_root / "AISC2Whisper.exe"
    if helper.is_file():
        command = [str(helper)]
    else:
        python = project_root / ".voice-venv" / "Scripts" / "python.exe"
        script = project_root / "scripts" / "transcribe_local.py"
        if not python.is_file():
            raise RuntimeError("本地语音环境尚未安装，请运行 scripts\\setup-voice.ps1")
        if not script.is_file():
            raise RuntimeError("缺少本地语音转写程序 scripts\\transcribe_local.py")
        command = [str(python), str(script)]
    return [
        *command,
        "--serve",
        "--model",
        model,
        "--model-dir",
        str(project_root / "models" / "whisper"),
    ]


class LocalWhisperTranscriber:
    """Keep faster-whisper resident in its isolated environment."""

    def __init__(self, project_root: Path, model: str = "small") -> None:
        self.project_root = project_root
        self.model = model
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._responses: queue.Queue[str | None] = queue.Queue()
        self._stderr: deque[str] = deque(maxlen=30)

    def transcribe(self, path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(path)
        with self._lock:
            process = self._ensure_process()
            assert process.stdin is not None
            request = json.dumps({"audio": str(path.resolve())}, ensure_ascii=False)
            try:
                process.stdin.write(request + "\n")
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self._discard_process()
                raise RuntimeError("本地 Whisper 常驻进程已经退出") from error
            try:
                line = self._responses.get(timeout=600)
            except queue.Empty as error:
                self._discard_process()
                raise TimeoutError("本地 Whisper 转写超过 600 秒") from error
            if line is None:
                detail = "\n".join(self._stderr).strip() or "本地 Whisper 常驻进程意外退出"
                self._discard_process()
                raise RuntimeError(detail[-1500:])
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"本地 Whisper 返回了无效结果：{line[-500:]}") from error
            if payload.get("error"):
                raise RuntimeError(str(payload["error"])[-1500:])
            text = str(payload.get("text", "")).strip()
            if not text:
                raise ValueError("语音转写结果为空")
            return text

    def close(self) -> None:
        acquired = self._lock.acquire(timeout=0.5)
        if not acquired:
            process = self._process
            self._process = None
            if process is not None and process.poll() is None:
                process.terminate()
            return
        try:
            process = self._process
            if process is None:
                return
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write('{"command":"shutdown"}\n')
                    process.stdin.flush()
                    process.wait(timeout=2.0)
                except Exception:
                    process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
            self._discard_process()
        finally:
            self._lock.release()

    def _ensure_process(self) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        if process is not None:
            self._discard_process()
        command = local_whisper_server_command(self.project_root, self.model)
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self._responses = queue.Queue()
        self._stderr.clear()
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
            bufsize=1,
        )
        self._process = process
        threading.Thread(
            target=self._read_stdout,
            args=(process,),
            name="local-whisper-stdout",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="local-whisper-stderr",
            daemon=True,
        ).start()
        LOG.info("Local Whisper resident process started: pid=%s model=%s", process.pid, self.model)
        return process

    def _read_stdout(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            value = line.strip()
            if value:
                self._responses.put(value)
        self._responses.put(None)

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        assert process.stderr is not None
        for line in process.stderr:
            value = line.rstrip()
            if value:
                self._stderr.append(value)

    def _discard_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
