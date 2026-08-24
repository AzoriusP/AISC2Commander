from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .harness import AgentHarness
from .models import AgentGameState, AgentJobProgress, AgentJobResult
from .voice import record_wav


LOG = logging.getLogger(__name__)


class Transcriber(Protocol):
    def transcribe(self, path: Path) -> str: ...


@dataclass(frozen=True, slots=True)
class _AgentJob:
    state: AgentGameState
    job_id: str = ""
    text: str = ""
    voice_duration: float = 0.0
    voice_device: int | str | None = None


class AgentWorker:
    """Runs microphone capture, transcription, and network planning off the SC2 loop."""

    def __init__(self, harness: AgentHarness, transcriber: Transcriber | None = None) -> None:
        self.harness = harness
        self.transcriber = transcriber
        self._jobs: queue.Queue[_AgentJob | None] = queue.Queue(maxsize=32)
        self.results: queue.Queue[AgentJobResult] = queue.Queue()
        self.progress: queue.Queue[AgentJobProgress] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="agent-harness", daemon=True)
        self._started = False

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def submit_text(self, text: str, state: AgentGameState, *, job_id: str = "") -> None:
        self._submit(_AgentJob(state=state, job_id=job_id, text=text))

    def submit_voice(
        self,
        duration: float,
        state: AgentGameState,
        device: int | str | None = None,
        *,
        job_id: str = "",
    ) -> None:
        if self.transcriber is None:
            raise ValueError("语音转写需要 OPENAI_API_KEY；当前未配置")
        self._submit(
            _AgentJob(
                state=state,
                job_id=job_id,
                voice_duration=duration,
                voice_device=device,
            )
        )

    def close(self) -> None:
        if not self._started:
            return
        try:
            self._jobs.put_nowait(None)
        except queue.Full:
            pass
        self._thread.join(timeout=0.3)

    def _submit(self, job: _AgentJob) -> None:
        try:
            self._jobs.put_nowait(job)
        except queue.Full as error:
            raise ValueError("Agent 请求队列已满，请等待上一条指令完成") from error
        self.progress.put(
            AgentJobProgress(
                job.job_id,
                "queued",
                f"已进入队列，前面还有 {max(0, self._jobs.qsize() - 1)} 条指令",
            )
        )

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            path: Path | None = None
            transcript = ""
            selection_tags = tuple(
                int(tag) for tag in job.state.selection.get("unit_tags", [])
            )
            try:
                text = job.text
                if job.voice_duration:
                    self.progress.put(AgentJobProgress(job.job_id, "transcribing", "正在录音并转写"))
                    path = record_wav(
                        job.voice_duration,
                        device=job.voice_device,
                    )
                    assert self.transcriber is not None
                    text = self.transcriber.transcribe(path)
                    transcript = text
                self.progress.put(
                    AgentJobProgress(
                        job.job_id,
                        "planning",
                        "正在解析指令（本地规则优先，必要时调用 LLM）",
                    )
                )
                LOG.info(
                    "Agent planning started: routing=rules_first llm_provider=%s text=%r",
                    self.harness.active_provider,
                    text,
                )
                plan = self.harness.plan(text, job.state)
                route = "本地规则" if plan.provider == "rules_fast_path" else plan.provider
                self.progress.put(
                    AgentJobProgress(
                        job.job_id,
                        "plan_ready",
                        f"计划已生成（{route}），共 {len(plan.tool_calls)} 个动作",
                        0,
                        len(plan.tool_calls),
                    )
                )
                self.results.put(
                    AgentJobResult(
                        job_id=job.job_id,
                        plan=plan,
                        transcript=transcript,
                        selection_tags=selection_tags,
                    )
                )
            except Exception as error:
                LOG.exception("Agent background job failed")
                self.results.put(
                    AgentJobResult(
                        job_id=job.job_id,
                        transcript=transcript,
                        error=str(error),
                        selection_tags=selection_tags,
                    )
                )
            finally:
                if path is not None:
                    path.unlink(missing_ok=True)
