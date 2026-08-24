from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _transcribe(model, audio: Path) -> dict[str, str]:
    if not audio.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio}")
    segments, info = model.transcribe(
        str(audio),
        language="zh",
        beam_size=5,
        vad_filter=True,
        initial_prompt=(
            "星际争霸二中文战术指令：陆战队员、枪兵、劫掠者、农民、兵营、"
            "指挥中心、编组、一队、移动、攻击、生产、升级、A1、A2。"
        ),
    )
    text = "".join(segment.text for segment in segments).strip()
    return {"text": text, "language": info.language}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path, nargs="?")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--model", default="small")
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    if not args.serve and args.audio is None:
        parser.error("audio is required unless --serve is used")

    from faster_whisper import WhisperModel

    model = WhisperModel(
        args.model,
        device="cpu",
        compute_type="int8",
        download_root=str(args.model_dir),
    )
    if not args.serve:
        assert args.audio is not None
        print(json.dumps(_transcribe(model, args.audio), ensure_ascii=False))
        return 0

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                break
            audio = Path(str(request["audio"]))
            payload = _transcribe(model, audio)
        except Exception as error:
            payload = {"error": str(error)}
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
