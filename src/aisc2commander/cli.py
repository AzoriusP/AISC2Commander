from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .app import AppConfig, CommanderApp
from .agent.harness import ollama_model_available, probe_ollama_models
from .logging_setup import configure_logging
from .sc2 import ComputerPlayerSetup, MultiplayerPortConfig, SC2Session, SessionConfig
from .settings import load_env_file, load_openai_api_key
from .smoke import run_smoke_test


LOG = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAP = PROJECT_ROOT / "vendor" / "s2client-api" / "maps" / "Ladder" / "(2)Bel'ShirVestigeLE (Void).SC2Map"
SMOKE_MAP = PROJECT_ROOT / "vendor" / "s2client-api" / "maps" / "Test" / "Empty.SC2Map"
OPENAI_KEY_FILE = PROJECT_ROOT / "config" / "openai.env"
LLM_CONFIG_FILE = PROJECT_ROOT / "config" / "llm.env"
LLM_SETTING_NAMES = {
    "LLM_PROVIDER",
    "OPENAI_MODEL",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_API_KEY",
    "OLLAMA_TIMEOUT",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sc2-commander",
        description="StarCraft II AI Commander prototype using Blizzard's official API protocol",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    run = subparsers.add_parser("run", help="run the interactive realtime prototype")
    _common_arguments(run, None)
    run.add_argument("--poll-interval", type=float, default=0.10)
    run.add_argument("--snapshot-interval", type=float, default=1.0)
    run.add_argument("--keep-game", action="store_true", help="don't quit an attached SC2 process on exit")
    run.add_argument(
        "--agent-provider",
        choices=("auto", "openai", "ollama", "rules"),
        default=os.getenv("LLM_PROVIDER", "auto"),
        help="select GPT-5.6, local Ollama, or deterministic local rules",
    )
    run.add_argument("--model", default=None, help="planning model name; provider default if omitted")
    run.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
        default="low",
    )
    run.add_argument("--transcription-model", default="gpt-transcribe")
    run.add_argument("--voice-duration", type=float, default=4.0)
    run.add_argument("--voice-device", default=None, help="sounddevice input index or device name")
    run.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
        help="Ollama OpenAI-compatible base URL",
    )
    run.add_argument(
        "--agent-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT", "120")),
        help="LLM request timeout in seconds",
    )
    run.add_argument(
        "--control-port",
        type=int,
        default=8765,
        help="loopback GUI control API port",
    )

    smoke = subparsers.add_parser("smoke", help="run a real SC2 integration smoke test")
    _common_arguments(smoke, SMOKE_MAP)
    smoke.add_argument("--timeout", type=float, default=20.0)
    smoke.add_argument(
        "--soak-seconds",
        type=float,
        default=0.0,
        help="continue realtime observations for a stability soak test",
    )
    return parser


def _common_arguments(parser: argparse.ArgumentParser, default_map: Path | None) -> None:
    maps = parser.add_mutually_exclusive_group()
    maps.add_argument("--map", type=Path, default=default_map, help="local .SC2Map path")
    maps.add_argument(
        "--battlenet-map",
        default=None,
        help="exact published/cached Battle.net map name for official RequestCreateGame",
    )
    parser.add_argument("--sc2", type=Path, default=None, help="path to SC2_x64.exe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--attach", action="store_true", help="connect to an already API-launched SC2")
    opponents = parser.add_mutually_exclusive_group()
    opponents.add_argument("--no-opponent", action="store_true")
    opponents.add_argument(
        "--computer",
        action="append",
        type=_parse_computer_player,
        metavar="RACE,DIFFICULTY,AI_BUILD",
        help=(
            "repeat for each official SC2 Computer player, for example "
            "--computer zerg,easy,rush"
        ),
    )
    parser.add_argument(
        "--race",
        choices=("terran", "zerg", "protoss", "random"),
        default="terran",
        help="participant race for official RequestCreateGame/RequestJoinGame",
    )
    parser.add_argument(
        "--multiplayer",
        choices=("single", "host", "join"),
        default="single",
        help="single player, create an official peer game, or join one",
    )
    parser.add_argument(
        "--game-host",
        default=None,
        help="creator's reachable IPv4 address; both multiplayer peers use the same value",
    )
    parser.add_argument(
        "--network-port",
        type=_parse_multiplayer_port_start,
        default=5001,
        metavar="PORT",
        help="first of five consecutive official SC2 multiplayer ports (default: 5001)",
    )
    parser.add_argument("--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    loaded_settings = load_env_file(LLM_CONFIG_FILE, LLM_SETTING_NAMES)
    args = build_parser().parse_args(argv)
    log_path = configure_logging(PROJECT_ROOT / "logs", verbose=args.verbose)
    LOG.info("Detailed log: %s", log_path)
    if load_openai_api_key(OPENAI_KEY_FILE):
        LOG.info("Loaded OpenAI API key from %s", OPENAI_KEY_FILE)
    if loaded_settings:
        LOG.info("Loaded local LLM settings from %s: %s", LLM_CONFIG_FILE, list(loaded_settings))
    if args.attach and args.port is None:
        raise SystemExit("--attach requires --port")
    if args.mode == "smoke" and args.multiplayer != "single":
        raise SystemExit("The integration smoke fixture only supports --multiplayer single.")
    if args.multiplayer == "join":
        if args.map is not None or args.battlenet_map:
            raise SystemExit("A multiplayer joiner does not select the map; the host creates the game.")
        if args.computer:
            raise SystemExit("Only the multiplayer host can configure Computer players.")
    else:
        if args.map is not None and not args.map.is_file():
            raise SystemExit(
                f"Map not found: {args.map}. Run .\\scripts\\bootstrap.ps1 to fetch Blizzard's official maps."
            )
        if args.map is None and not args.battlenet_map:
            raise SystemExit(
                "Select a map with --map <file.SC2Map> or --battlenet-map <published name>."
            )
    if args.multiplayer != "single" and not (args.game_host or "").strip():
        raise SystemExit("--game-host <creator IPv4> is required for official multiplayer.")
    if args.mode == "smoke":
        return run_smoke_test(_build_session(args), timeout=args.timeout, soak_seconds=args.soak_seconds)

    if args.agent_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        raise SystemExit(
            "OpenAI API key is missing. Write it to config\\openai.env "
            "or set OPENAI_API_KEY."
        )
    agent_model = _resolve_model(args.agent_provider, args.model)
    if args.agent_provider == "ollama":
        try:
            models = probe_ollama_models(
                args.ollama_url,
                os.getenv("OLLAMA_API_KEY", "ollama"),
                min(args.agent_timeout, 5.0),
            )
        except Exception as error:
            raise SystemExit(
                f"Cannot connect to Ollama at {args.ollama_url}: {error}. "
                "Start it with 'ollama serve'."
            ) from error
        if not ollama_model_available(agent_model, models):
            detected = ", ".join(models) if models else "(none)"
            raise SystemExit(
                f"Ollama is reachable, but model '{agent_model}' is not installed. "
                f"Detected models: {detected}. Check 'ollama list' and update config\\llm.env."
            )
        LOG.info("Ollama preflight passed: url=%s model=%s", args.ollama_url, agent_model)
    session = _build_session(args)
    return CommanderApp(
        session,
        AppConfig(
            poll_interval=args.poll_interval,
            snapshot_interval=args.snapshot_interval,
            quit_game_on_exit=not args.keep_game,
            agent_provider=args.agent_provider,
            agent_model=agent_model,
            reasoning_effort=args.reasoning_effort,
            transcription_model=args.transcription_model,
            voice_duration=args.voice_duration,
            voice_device=_parse_voice_device(args.voice_device),
            ollama_base_url=args.ollama_url,
            ollama_api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            agent_timeout=args.agent_timeout,
            control_port=args.control_port,
            map_points_path=PROJECT_ROOT / "config" / "map_points.json",
            command_plans_path=PROJECT_ROOT / "config" / "command_plans.json",
        ),
    ).run()


def _build_session(args: argparse.Namespace) -> SC2Session:
    if args.multiplayer == "single":
        computers = None if args.computer is None else tuple(args.computer)
    else:
        computers = () if args.computer is None else tuple(args.computer)
    multiplayer_ports = (
        None
        if args.multiplayer == "single"
        else MultiplayerPortConfig.from_start(args.network_port)
    )
    return SC2Session(
        SessionConfig(
            map_path=args.map,
            battlenet_map_name=args.battlenet_map,
            executable=args.sc2,
            host=args.host,
            port=args.port,
            launch=not args.attach,
            realtime=True,
            opponent=(
                args.multiplayer == "single"
                and not args.no_opponent
                and (computers is None or bool(computers))
            ),
            player_race=args.race,
            computer_players=computers,
            multiplayer_mode=args.multiplayer,
            multiplayer_host_ip=args.game_host,
            multiplayer_ports=multiplayer_ports,
        )
    )


def _parse_computer_player(value: str) -> ComputerPlayerSetup:
    parts = tuple(part.strip() for part in value.split(","))
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError(
            "computer must use RACE,DIFFICULTY,AI_BUILD (example: zerg,easy,rush)"
        )
    try:
        return ComputerPlayerSetup(*parts).normalized()
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parse_multiplayer_port_start(value: str) -> int:
    try:
        return MultiplayerPortConfig.from_start(int(value)).port_start
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _parse_voice_device(value: str | None) -> int | str | None:
    if value is None:
        return None
    return int(value) if value.isdecimal() else value


def _resolve_model(provider: str, requested: str | None) -> str:
    if requested:
        return requested
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "qwen3.6")
    return os.getenv("OPENAI_MODEL", "gpt-5.6")
