from __future__ import annotations

import ipaddress
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from s2clientprotocol import (
    common_pb2,
    debug_pb2,
    error_pb2,
    raw_pb2,
    sc2api_pb2,
    ui_pb2,
)

from ..catalog import GameCatalog
from .process import SC2Process, choose_free_port, discover_sc2_executable
from .protocol import SC2ProtocolClient, SC2ProtocolError


LOG = logging.getLogger(__name__)
MOVE_ABILITY_ID = 16  # Blizzard stable ability id: Move.
ATTACK_ABILITY_ID = 23  # Blizzard stable ability id: Attack.
PLAYER_RACES = {
    "terran": common_pb2.Terran,
    "zerg": common_pb2.Zerg,
    "protoss": common_pb2.Protoss,
    "random": common_pb2.Random,
}
COMPUTER_DIFFICULTIES = {
    "very_easy": sc2api_pb2.VeryEasy,
    "easy": sc2api_pb2.Easy,
    "medium": sc2api_pb2.Medium,
    "medium_hard": sc2api_pb2.MediumHard,
    "hard": sc2api_pb2.Hard,
    "harder": sc2api_pb2.Harder,
    "very_hard": sc2api_pb2.VeryHard,
    "cheat_vision": sc2api_pb2.CheatVision,
    "cheat_money": sc2api_pb2.CheatMoney,
    "cheat_insane": sc2api_pb2.CheatInsane,
}
COMPUTER_AI_BUILDS = {
    "random": sc2api_pb2.RandomBuild,
    "rush": sc2api_pb2.Rush,
    "timing": sc2api_pb2.Timing,
    "power": sc2api_pb2.Power,
    "macro": sc2api_pb2.Macro,
    "air": sc2api_pb2.Air,
}
MULTIPLAYER_MODES = {"single", "host", "join"}


def player_race_id(value: str) -> int:
    race = value.strip().casefold()
    try:
        return PLAYER_RACES[race]
    except KeyError as error:
        raise ValueError(f"Unsupported SC2 player race: {value}") from error


def computer_difficulty_id(value: str) -> int:
    difficulty = value.strip().casefold().replace("-", "_")
    try:
        return COMPUTER_DIFFICULTIES[difficulty]
    except KeyError as error:
        raise ValueError(f"Unsupported SC2 computer difficulty: {value}") from error


def computer_ai_build_id(value: str) -> int:
    ai_build = value.strip().casefold().replace("-", "_")
    try:
        return COMPUTER_AI_BUILDS[ai_build]
    except KeyError as error:
        raise ValueError(f"Unsupported SC2 computer AI build: {value}") from error


@dataclass(frozen=True, slots=True)
class ComputerPlayerSetup:
    race: str
    difficulty: str = "easy"
    ai_build: str = "random"

    def normalized(self) -> ComputerPlayerSetup:
        race = self.race.strip().casefold()
        difficulty = self.difficulty.strip().casefold().replace("-", "_")
        ai_build = self.ai_build.strip().casefold().replace("-", "_")
        player_race_id(race)
        computer_difficulty_id(difficulty)
        computer_ai_build_id(ai_build)
        return ComputerPlayerSetup(race, difficulty, ai_build)


@dataclass(frozen=True, slots=True)
class MultiplayerPortConfig:
    """The five-port layout used by Blizzard's official 1v1 API flow."""

    shared_port: int
    server_game_port: int
    server_base_port: int
    client_game_port: int
    client_base_port: int

    @classmethod
    def from_start(cls, port_start: int) -> MultiplayerPortConfig:
        try:
            start = int(port_start)
        except (TypeError, ValueError) as error:
            raise ValueError("Multiplayer port start must be an integer") from error
        ports = cls(start, start + 1, start + 2, start + 3, start + 4)
        ports.validate()
        return ports

    def validate(self) -> None:
        values = self.ports
        if any(port < 1 or port > 65535 for port in values):
            raise ValueError("Multiplayer ports must all be between 1 and 65535")
        if len(set(values)) != len(values):
            raise ValueError("Multiplayer ports must be distinct")

    @property
    def port_start(self) -> int:
        return self.shared_port

    @property
    def ports(self) -> tuple[int, int, int, int, int]:
        return (
            self.shared_port,
            self.server_game_port,
            self.server_base_port,
            self.client_game_port,
            self.client_base_port,
        )


@dataclass(slots=True)
class SessionConfig:
    map_path: Path | None = None
    battlenet_map_name: str | None = None
    executable: Path | None = None
    host: str = "127.0.0.1"
    port: int | None = None
    launch: bool = True
    realtime: bool = True
    opponent: bool = True
    player_race: str = "terran"
    # SC2 owns multiplayer transport, synchronization and validation. The
    # pinned official protocol supports one host and one remote participant.
    multiplayer_mode: str = "single"
    multiplayer_host_ip: str | None = None
    multiplayer_ports: MultiplayerPortConfig | None = None
    # None retains the original single VeryEasy Zerg opponent behavior.
    # An explicit tuple creates exactly these official Computer PlayerSetup rows.
    computer_players: tuple[ComputerPlayerSetup, ...] | None = None
    connect_timeout: float = 60.0
    window_width: int = 1280
    window_height: int = 800


class SC2Session:
    """High-level SC2 API facade; no SC2 traffic escapes this module."""

    def __init__(self, config: SessionConfig) -> None:
        config.player_race = config.player_race.strip().casefold()
        player_race_id(config.player_race)
        config.multiplayer_mode = config.multiplayer_mode.strip().casefold()
        if config.multiplayer_mode not in MULTIPLAYER_MODES:
            raise ValueError(f"Unsupported multiplayer mode: {config.multiplayer_mode}")
        if config.multiplayer_mode == "single":
            if config.multiplayer_ports is not None:
                raise ValueError("Multiplayer ports cannot be used in single-player mode")
        else:
            host_ip = (config.multiplayer_host_ip or "").strip()
            if not host_ip:
                raise ValueError("Multiplayer host IPv4 address is required")
            try:
                config.multiplayer_host_ip = str(ipaddress.IPv4Address(host_ip))
            except ipaddress.AddressValueError as error:
                raise ValueError(f"Invalid multiplayer host IPv4 address: {host_ip}") from error
            if config.multiplayer_ports is None:
                raise ValueError("Official multiplayer ports are required")
            config.multiplayer_ports.validate()
        if config.computer_players is not None:
            maximum_computers = 14 if config.multiplayer_mode == "host" else 15
            if len(config.computer_players) > maximum_computers:
                raise ValueError(
                    f"SC2 supports at most {maximum_computers} computer players "
                    "with this participant setup"
                )
            config.computer_players = tuple(
                computer.normalized() for computer in config.computer_players
            )
        self.config = config
        self.port = config.port or choose_free_port(config.host)
        self.transport = SC2ProtocolClient(config.host, self.port)
        self.process: SC2Process | None = None
        self.player_id: int | None = None
        self.catalog = GameCatalog()
        self._closed = False
        self._joined = False

    def start(self) -> None:
        if self.config.launch:
            executable = discover_sc2_executable(self.config.executable)
            self.process = SC2Process(
                executable=executable,
                host=self.config.host,
                port=self.port,
                window_width=self.config.window_width,
                window_height=self.config.window_height,
            )
            self.process.launch()
        self.transport.connect(timeout=self.config.connect_timeout)
        ping_response = self.ping()
        ping = ping_response.ping
        LOG.info(
            "SC2 ping game_version=%s base_build=%s data_version=%s status=%s",
            ping.game_version,
            ping.base_build,
            ping.data_version,
            sc2api_pb2.Status.Name(ping_response.status),
        )
        if ping_response.status in (sc2api_pb2.launched, sc2api_pb2.ended):
            if self.config.multiplayer_mode == "join":
                if ping_response.status != sc2api_pb2.launched:
                    raise SC2ProtocolError(
                        "An official multiplayer joiner must start from SC2 status launched"
                    )
                self._join_game()
            else:
                self._create_game()
                self._join_game()
        elif (
            ping_response.status == sc2api_pb2.init_game
            and self.config.multiplayer_mode == "host"
        ):
            self._join_game()
        elif ping_response.status != sc2api_pb2.in_game:
            raise SC2ProtocolError(
                "Cannot initialize session from SC2 status "
                f"{sc2api_pb2.Status.Name(ping_response.status)}"
            )
        elif self.config.multiplayer_mode != "single":
            # An attached in-game multiplayer client has already completed its
            # official JoinGame lifecycle before Commander connected.
            self._joined = True
        self.catalog = self._load_catalog()
        LOG.info(
            "SC2 session ready: realtime=%s player_id=%s units=%d abilities=%d upgrades=%d",
            self.config.realtime,
            self.player_id,
            len(self.catalog.unit_types),
            len(self.catalog.abilities),
            len(self.catalog.upgrades),
        )

    def ping(self) -> sc2api_pb2.Response:
        return self.transport.request(lambda request: request.ping.SetInParent(), "ping")

    def _create_game(self) -> None:
        map_path = (
            self.config.map_path.resolve(strict=True)
            if self.config.map_path is not None
            else None
        )
        battlenet_map_name = (self.config.battlenet_map_name or "").strip()
        if map_path is None and not battlenet_map_name:
            raise SC2ProtocolError("No local or Battle.net map was selected")
        if map_path is not None and battlenet_map_name:
            raise SC2ProtocolError("Select either a local map or a Battle.net map, not both")

        def populate(request: sc2api_pb2.Request) -> None:
            create = request.create_game
            if map_path is not None:
                create.local_map.map_path = str(map_path)
            else:
                create.battlenet_map_name = battlenet_map_name
            create.realtime = self.config.realtime
            human = create.player_setup.add()
            human.type = sc2api_pb2.Participant
            human.race = player_race_id(self.config.player_race)
            if self.config.multiplayer_mode == "host":
                # A Participant chooses its race in its own JoinGame request;
                # PlayerSetup.race is officially used only by Computer players.
                guest = create.player_setup.add()
                guest.type = sc2api_pb2.Participant
            computers = self.config.computer_players
            if computers is None:
                if self.config.multiplayer_mode == "host":
                    computers = ()
                else:
                    computers = (
                        (ComputerPlayerSetup("zerg", "very_easy", "random"),)
                        if self.config.opponent
                        else ()
                    )
            for index, computer in enumerate(computers, start=1):
                opponent = create.player_setup.add()
                opponent.type = sc2api_pb2.Computer
                opponent.race = player_race_id(computer.race)
                opponent.difficulty = computer_difficulty_id(computer.difficulty)
                opponent.ai_build = computer_ai_build_id(computer.ai_build)
                opponent.player_name = f"Computer {index}"

        response = self.transport.request(populate, "create_game")
        if response.create_game.HasField("error"):
            name = sc2api_pb2.ResponseCreateGame.Error.Name(response.create_game.error)
            raise SC2ProtocolError(
                f"CreateGame failed: {name}: {response.create_game.error_details}"
            )
        selected_map = str(map_path) if map_path is not None else f"Battle.net:{battlenet_map_name}"
        LOG.info("Created realtime=%s game on map=%s", self.config.realtime, selected_map)

    def available_maps(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return maps reported by Blizzard's official RequestAvailableMaps."""

        response = self.transport.request(
            lambda request: request.available_maps.SetInParent(),
            "available_maps",
        )
        maps = response.available_maps
        return tuple(maps.local_map_paths), tuple(maps.battlenet_map_names)

    def _join_game(self) -> None:
        def populate(request: sc2api_pb2.Request) -> None:
            join = request.join_game
            join.race = player_race_id(self.config.player_race)
            join.player_name = "AI Commander Prototype"
            options = join.options
            options.raw = True
            # Preserve the human's mouse selection around raw API commands.
            options.raw_affects_selection = False
            # ObservationUI is populated only when feature-layer or render is enabled.
            options.feature_layer.width = 24.0
            options.feature_layer.resolution.x = 84
            options.feature_layer.resolution.y = 84
            options.feature_layer.minimap_resolution.x = 64
            options.feature_layer.minimap_resolution.y = 64
            ports = self.config.multiplayer_ports
            if ports is not None:
                # This is the complete topology prescribed by s2client-proto;
                # SC2 itself performs transport, lockstep sync and validation.
                join.shared_port = ports.shared_port
                join.server_ports.game_port = ports.server_game_port
                join.server_ports.base_port = ports.server_base_port
                client = join.client_ports.add()
                client.game_port = ports.client_game_port
                client.base_port = ports.client_base_port
                join.host_ip = self.config.multiplayer_host_ip

        response = self.transport.request(populate, "join_game")
        if response.join_game.HasField("error"):
            name = sc2api_pb2.ResponseJoinGame.Error.Name(response.join_game.error)
            raise SC2ProtocolError(f"JoinGame failed: {name}: {response.join_game.error_details}")
        self.player_id = int(response.join_game.player_id)
        self._joined = True
        LOG.info(
            "Joined game as %s participant, player_id=%s mode=%s",
            self.config.player_race,
            self.player_id,
            self.config.multiplayer_mode,
        )

    def _load_catalog(self) -> GameCatalog:
        def populate(request: sc2api_pb2.Request) -> None:
            request.data.ability_id = True
            request.data.unit_type_id = True
            request.data.upgrade_id = True

        response = self.transport.request(populate, "request_data")
        return GameCatalog.from_response(response.data)

    def game_info(self) -> sc2api_pb2.ResponseGameInfo:
        response = self.transport.request(lambda request: request.game_info.SetInParent(), "game_info")
        return response.game_info

    def observe(self, target_game_loop: int | None = None) -> sc2api_pb2.ResponseObservation:
        def populate(request: sc2api_pb2.Request) -> None:
            request.observation.SetInParent()
            if target_game_loop is not None:
                request.observation.game_loop = target_game_loop

        response = self.transport.request(populate, "observation")
        self._log_observation_action_errors(response.observation)
        return response.observation

    def move_units(
        self,
        unit_tags: list[int] | tuple[int, ...],
        x: float,
        y: float,
        queue: bool = False,
    ) -> tuple[str, ...]:
        if not unit_tags:
            raise ValueError("move_units requires at least one unit tag")

        LOG.info(
            "Sending unit move action: tags=%s target=(%.2f, %.2f) queue=%s",
            list(unit_tags),
            x,
            y,
            queue,
        )
        return self.unit_command(
            MOVE_ABILITY_ID,
            unit_tags,
            target_position=(x, y),
            queue=queue,
            operation="action.move",
        )

    def attack_units(
        self,
        unit_tags: list[int] | tuple[int, ...],
        *,
        x: float | None = None,
        y: float | None = None,
        target_unit_tag: int | None = None,
        queue: bool = False,
    ) -> tuple[str, ...]:
        if target_unit_tag is None and (x is None or y is None):
            raise ValueError("attack_units requires a target position or unit tag")
        LOG.info(
            "Sending attack action: tags=%s target_pos=%s target_tag=%s queue=%s",
            list(unit_tags),
            None if x is None else (x, y),
            target_unit_tag,
            queue,
        )
        return self.unit_command(
            ATTACK_ABILITY_ID,
            unit_tags,
            target_position=None if x is None or y is None else (x, y),
            target_unit_tag=target_unit_tag,
            queue=queue,
            operation="action.attack",
        )

    def unit_command(
        self,
        ability_id: int,
        unit_tags: list[int] | tuple[int, ...],
        *,
        target_position: tuple[float, float] | None = None,
        target_unit_tag: int | None = None,
        queue: bool = False,
        operation: str = "action.unit_command",
    ) -> tuple[str, ...]:
        if not unit_tags:
            raise ValueError("unit_command requires at least one unit tag")

        def populate(request: sc2api_pb2.Request) -> None:
            command = request.action.actions.add().action_raw.unit_command
            command.ability_id = ability_id
            if target_position is not None:
                command.target_world_space_pos.x = target_position[0]
                command.target_world_space_pos.y = target_position[1]
            elif target_unit_tag is not None:
                command.target_unit_tag = target_unit_tag
            command.unit_tags.extend(unit_tags)
            command.queue_command = queue

        response = self.transport.request(populate, operation)
        return self._log_immediate_action_results(response.action, ability_id, unit_tags)

    def available_abilities(
        self,
        unit_tags: list[int] | tuple[int, ...],
        *,
        ignore_resource_requirements: bool = False,
    ) -> dict[int, set[int]]:
        if not unit_tags:
            return {}

        def populate(request: sc2api_pb2.Request) -> None:
            request.query.ignore_resource_requirements = ignore_resource_requirements
            for tag in unit_tags:
                request.query.abilities.add().unit_tag = tag

        response = self.transport.request(populate, "query.available_abilities")
        return {
            int(item.unit_tag): {int(ability.ability_id) for ability in item.abilities}
            for item in response.query.abilities
        }

    def building_placement_error(
        self,
        ability_id: int,
        x: float,
        y: float,
        placing_unit_tag: int,
    ) -> str | None:
        """Return None when Blizzard's placement query accepts the location."""

        return self.building_placement_errors(
            ability_id,
            ((x, y),),
            placing_unit_tag,
        )[0]

    def building_placement_errors(
        self,
        ability_id: int,
        positions: tuple[tuple[float, float], ...],
        placing_unit_tag: int,
    ) -> tuple[str | None, ...]:
        """Batch-check candidate points in one official RequestQuery round trip."""

        if not positions:
            return ()

        def populate(request: sc2api_pb2.Request) -> None:
            request.query.ignore_resource_requirements = True
            for x, y in positions:
                placement = request.query.placements.add()
                placement.ability_id = ability_id
                placement.target_pos.x = x
                placement.target_pos.y = y
                placement.placing_unit_tag = placing_unit_tag

        response = self.transport.request(populate, "query.building_placement")
        results: list[str | None] = []
        response_items = tuple(response.query.placements)
        for index, (x, y) in enumerate(positions):
            if index >= len(response_items):
                results.append("SC2 returned no building-placement result")
                continue
            result = response_items[index].result
            if result == error_pb2.Success:
                LOG.debug(
                    "SC2 building placement accepted: ability=%d worker=%d target=(%.2f, %.2f)",
                    ability_id,
                    placing_unit_tag,
                    x,
                    y,
                )
                results.append(None)
                continue
            name = _action_result_name(result)
            LOG.warning(
                "SC2 building placement rejected: result=%s ability=%d worker=%d target=(%.2f, %.2f)",
                name,
                ability_id,
                placing_unit_tag,
                x,
                y,
            )
            results.append(name)
        return tuple(results)

    def build_structure(
        self,
        structure_type: int,
        worker_tag: int,
        *,
        target_position: tuple[float, float] | None = None,
        target_unit_tag: int | None = None,
        queue: bool = False,
    ) -> tuple[str, ...]:
        info = self.catalog.unit_types.get(structure_type)
        if info is None or not info.is_structure or not info.ability_id:
            raise ValueError(f"unit type {structure_type} has no SCV build ability in RequestData")
        if target_unit_tag is None and target_position is None:
            raise ValueError("build_structure requires a target position or target unit")
        ability_id = info.ability_id
        available = self.available_abilities((worker_tag,), ignore_resource_requirements=True)
        actual_ability_id = self.catalog.available_variant(
            ability_id,
            available.get(worker_tag, set()),
        )
        if actual_ability_id is None:
            raise ValueError(
                f"SCV {worker_tag} cannot currently build {info.name} (ability={ability_id})"
            )
        action_position = None if target_unit_tag is not None else target_position
        LOG.info(
            "Sending normal build action: structure=%s ability=%d worker=%d "
            "target_pos=%s target_tag=%s queue=%s",
            info.name,
            ability_id,
            worker_tag,
            action_position,
            target_unit_tag,
            queue,
        )
        return self.unit_command(
            actual_ability_id,
            (worker_tag,),
            target_position=action_position,
            target_unit_tag=target_unit_tag,
            queue=queue,
            operation="action.build_structure",
        )

    def train_units(
        self,
        unit_type: int,
        count: int,
        producer_tags: list[int] | tuple[int, ...],
        *,
        queue: bool = False,
        target_position: tuple[float, float] | None = None,
    ) -> tuple[str, ...]:
        if count < 1 or count > 20:
            raise ValueError("train count must be between 1 and 20")
        info = self.catalog.unit_types.get(unit_type)
        if info is None or not info.ability_id:
            raise ValueError(f"unit type {unit_type} has no train/build ability in RequestData")
        ability_id = info.ability_id
        # Producer/tech validation must not disappear merely because resources
        # are temporarily low; the caller preflights resources from Observation,
        # while SC2 remains authoritative through ActionResult.
        available = self.available_abilities(producer_tags, ignore_resource_requirements=True)
        producer_abilities = {
            tag: self.catalog.production_variant(
                ability_id,
                info.name,
                available.get(tag, set()),
                has_position=target_position is not None,
            )
            for tag in producer_tags
        }
        producers = tuple(
            tag
            for tag in producer_tags
            if producer_abilities[tag] is not None
        )
        if not producers:
            raise ValueError(
                f"No requested producer can currently train {info.name} (ability={ability_id})"
            )

        assignments = [producers[index % len(producers)] for index in range(count)]

        def populate(request: sc2api_pb2.Request) -> None:
            queued_per_producer: dict[int, int] = {}
            for tag in assignments:
                command = request.action.actions.add().action_raw.unit_command
                command.ability_id = int(producer_abilities[tag] or ability_id)
                command.unit_tags.append(tag)
                if target_position is not None:
                    command.target_world_space_pos.x = float(target_position[0])
                    command.target_world_space_pos.y = float(target_position[1])
                command.queue_command = queue or queued_per_producer.get(tag, 0) > 0
                queued_per_producer[tag] = queued_per_producer.get(tag, 0) + 1

        LOG.info(
            "Sending normal production actions: unit=%s count=%d ability=%d producers=%s target=%s",
            info.name,
            count,
            ability_id,
            list(producers),
            target_position,
        )
        response = self.transport.request(populate, "action.train")
        return self._log_action_batch(response.action, ability_id, assignments)

    def research_upgrade(
        self,
        upgrade_id: int,
        researcher_tags: list[int] | tuple[int, ...],
    ) -> tuple[str, ...]:
        info = self.catalog.upgrades.get(upgrade_id)
        if info is None or not info.ability_id:
            raise ValueError(f"upgrade {upgrade_id} has no research ability in RequestData")
        available = self.available_abilities(researcher_tags, ignore_resource_requirements=True)
        researcher = next(
            (
                (tag, actual)
                for tag in researcher_tags
                if (
                    actual := self.catalog.available_variant(
                        info.ability_id,
                        available.get(tag, set()),
                    )
                )
                is not None
            ),
            None,
        )
        if researcher is None:
            raise ValueError(
                f"No requested structure can currently research {info.name} (ability={info.ability_id})"
            )
        LOG.info(
            "Sending normal research action: upgrade=%s ability=%d researcher=%d",
            info.name,
            info.ability_id,
            researcher[0],
        )
        return self.unit_command(
            researcher[1],
            (researcher[0],),
            operation="action.research_upgrade",
        )

    def toggle_autocast(
        self,
        ability_id: int,
        unit_tags: list[int] | tuple[int, ...],
    ) -> tuple[str, ...]:
        if not unit_tags:
            raise ValueError("toggle_autocast requires at least one unit tag")

        def populate(request: sc2api_pb2.Request) -> None:
            toggle = request.action.actions.add().action_raw.toggle_autocast
            toggle.ability_id = ability_id
            toggle.unit_tags.extend(unit_tags)

        LOG.info("Toggling autocast: ability=%d tags=%s", ability_id, list(unit_tags))
        response = self.transport.request(populate, "action.toggle_autocast")
        return self._log_immediate_action_results(response.action, ability_id, unit_tags)

    def select_army_for_test(self) -> tuple[str, ...]:
        """Use the official UI action for deterministic selection smoke testing."""

        def populate(request: sc2api_pb2.Request) -> None:
            action = request.action.actions.add()
            action.action_ui.select_army.selection_add = False

        response = self.transport.request(populate, "action.select_army")
        return self._log_immediate_action_results(response.action, 0, ())

    def recall_control_group(self, number: int) -> tuple[str, ...]:
        """Recall a human hotkey group through official ActionUI.

        ObservationUI exposes group leader/count but not member tags. Recalling the
        group is the official, deterministic way to make its members appear as
        raw Unit.is_selected in the next Observation.
        """

        if number < 1 or number > 10:
            raise ValueError("control group number must be between 1 and 10")

        def populate(request: sc2api_pb2.Request) -> None:
            control_group = request.action.actions.add().action_ui.control_group
            control_group.action = ui_pb2.ActionControlGroup.Recall
            control_group.control_group_index = number - 1

        response = self.transport.request(populate, "action.control_group.recall")
        LOG.info("Recalled official UI control group %d", number)
        return self._log_immediate_action_results(response.action, 0, ())

    def manage_control_group(self, number: int, operation: str) -> tuple[str, ...]:
        """Apply an official UI control-group operation to the human selection."""

        if number < 1 or number > 10:
            raise ValueError("control group number must be between 1 and 10")
        operations = {
            "recall": ui_pb2.ActionControlGroup.Recall,
            "set": ui_pb2.ActionControlGroup.Set,
            "append": ui_pb2.ActionControlGroup.Append,
            "set_and_steal": ui_pb2.ActionControlGroup.SetAndSteal,
            "append_and_steal": ui_pb2.ActionControlGroup.AppendAndSteal,
        }
        try:
            action = operations[operation]
        except KeyError as error:
            raise ValueError(f"unsupported control-group operation: {operation}") from error

        def populate(request: sc2api_pb2.Request) -> None:
            control_group = request.action.actions.add().action_ui.control_group
            control_group.action = action
            control_group.control_group_index = number - 1

        response = self.transport.request(populate, f"action.control_group.{operation}")
        LOG.info("Official UI control group operation=%s number=%d", operation, number)
        return self._log_immediate_action_results(response.action, 0, ())

    def debug_create_unit(
        self,
        unit_type: int,
        x: float,
        y: float,
        quantity: int,
        owner: int | None = None,
    ) -> None:
        if quantity < 1:
            raise ValueError("quantity must be positive")
        actual_owner = owner or self.player_id
        if actual_owner is None:
            raise RuntimeError("player id is unavailable")

        def populate(request: sc2api_pb2.Request) -> None:
            create = request.debug.debug.add().create_unit
            create.unit_type = unit_type
            create.owner = actual_owner
            create.pos.x = x
            create.pos.y = y
            create.quantity = quantity

        self.transport.request(populate, "debug.create_unit")
        LOG.warning(
            "Official debug command created unit_type=%s owner=%s quantity=%s at (%.2f, %.2f)",
            unit_type,
            actual_owner,
            quantity,
            x,
            y,
        )

    def debug_game_state(self, state: int) -> None:
        """Apply an official DebugGameState for an integration-test fixture only."""

        def populate(request: sc2api_pb2.Request) -> None:
            request.debug.debug.add().game_state = state

        self.transport.request(populate, "debug.game_state")
        try:
            name = debug_pb2.DebugGameState.Name(state)
        except ValueError:
            name = str(state)
        LOG.warning("Official debug game state applied for test fixture: %s", name)

    def leave_game(self) -> None:
        try:
            self.transport.request(lambda request: request.leave_game.SetInParent(), "leave_game")
        except SC2ProtocolError:
            LOG.exception("SC2 leave_game request failed")

    def close(self, quit_game: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        quit_was_sent = False
        try:
            if self.transport.is_connected:
                if self._joined and self.config.multiplayer_mode != "single":
                    # The official multiplayer lifecycle requires LeaveGame
                    # even when --keep-game leaves the API process running.
                    self.leave_game()
                    self._joined = False
                if quit_game:
                    try:
                        self.transport.request(lambda request: request.quit.SetInParent(), "quit")
                        quit_was_sent = True
                    except (SC2ProtocolError, OSError) as error:
                        LOG.info("SC2 quit request was unavailable: %s", error)
        finally:
            self.transport.close()
            if self.process is not None and quit_game:
                if quit_was_sent:
                    deadline = time.monotonic() + 5
                    while self.process.handle is not None and self.process.handle.poll() is None:
                        if time.monotonic() >= deadline:
                            break
                        time.sleep(0.1)
                    self.process.terminate_if_running()
                else:
                    self.process.terminate_if_running(
                        "SC2 API connection is unavailable, so graceful quit isn't possible"
                    )

    def _log_immediate_action_results(
        self,
        action_response: sc2api_pb2.ResponseAction,
        ability_id: int,
        unit_tags: list[int] | tuple[int, ...],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for result in action_response.result:
            name = _action_result_name(result)
            if result == error_pb2.Success:
                LOG.debug("SC2 action accepted: result=%s ability=%s tags=%s", name, ability_id, list(unit_tags))
            else:
                message = f"API Action Error (immediate): result={name} ability={ability_id} tags={list(unit_tags)}"
                LOG.error(message)
                errors.append(message)
        if not action_response.result:
            LOG.debug("SC2 action accepted with no immediate errors: ability=%s tags=%s", ability_id, list(unit_tags))
        return tuple(errors)

    def _log_action_batch(
        self,
        action_response: sc2api_pb2.ResponseAction,
        ability_id: int,
        unit_tags: list[int] | tuple[int, ...],
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for index, result in enumerate(action_response.result):
            if result == error_pb2.Success:
                continue
            tag = unit_tags[index] if index < len(unit_tags) else 0
            message = (
                "API Action Error (immediate batch): "
                f"result={_action_result_name(result)} ability={ability_id} unit_tag={tag}"
            )
            LOG.error(message)
            errors.append(message)
        if not errors:
            LOG.debug("SC2 batch action accepted: ability=%s actions=%d", ability_id, len(unit_tags))
        return tuple(errors)

    def _log_observation_action_errors(self, observation: sc2api_pb2.ResponseObservation) -> None:
        for error in observation.action_errors:
            message = (
                "API Action Error (late): "
                f"result={_action_result_name(error.result)} "
                f"ability={error.ability_id} unit_tag={error.unit_tag}"
            )
            LOG.error(message)


def _action_result_name(result: int) -> str:
    try:
        return error_pb2.ActionResult.Name(result)
    except ValueError:
        return f"UnknownActionResult({result})"
