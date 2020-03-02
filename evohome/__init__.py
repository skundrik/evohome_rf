"""Evohome serial."""
import asyncio
import json
import logging
import signal
import sqlite3
import sys
from queue import Queue
from typing import Optional

import serial
import serial_asyncio

from .command import Command
from .const import INDEX_SQL, TABLE_SQL, INSERT_SQL
from .entity import System
from .logger import set_logging
from .message import _LOGGER as msg_logger, Message
from .packet import _LOGGER as pkt_logger, get_packet_from_port
from .packet import is_valid_packet, is_wanted_device, is_wanted_packet


logging.basicConfig(level=logging.WARNING,)

_LOGGER = logging.getLogger(__name__)
_LOGGER.setLevel(logging.WARNING)  # INFO for files, WARNING for console

BAUDRATE = 115200  # 38400  #  57600  # 76800  # 38400  # 115200
READ_TIMEOUT = 0


class Gateway:
    """The gateway class."""

    def __init__(self, serial_port, loop=None, **config):
        """Initialise the  class."""
        self.serial_port = serial_port
        self.loop = config.get("loop", asyncio.get_event_loop())
        self.config = config

        if self.serial_port and config.get("input_file"):
            _LOGGER.warning(
                "Serial port specified (%s): Ignoring input file (%s)",
                self.serial_port,
                config["input_file"],
            )
            config["input_file"] = None

        if config.get("input_file"):
            if not config.get("listen_only"):
                _LOGGER.warning(
                    "Input file specified (%s): Enabling listen_only mode",
                    config["input_file"],
                )
                config["listen_only"] = True

            if config.get("execute_cmd"):
                _LOGGER.warning(
                    "Input file specified (%s): Disabling command (%s)",
                    config["input_file"],
                    config["execute_cmd"],
                )
                config["execute_cmd"] = None

        if config.get("raw_output") and config.get("message_log"):
            _LOGGER.warning(
                "Raw output specified: Disabling message log (%s)",
                config["message_log"],
            )
            config["message_log"] = False

        set_logging(
            msg_logger,
            file_name=self.config.get("message_log"),
            stream=None if config.get("raw_output") else sys.stdout,
        )
        set_logging(
            pkt_logger,
            file_name=self.config.get("output_file"),
            stream=sys.stdout if config.get("raw_output") else None,
        )

        self.reader = self.writer = None
        self._input_fp = None
        self._output_db = self._db_cursor = None

        self.command_queue = Queue(maxsize=200)
        self.message_queue = Queue(maxsize=400)

        self.zones = []
        self.zone_by_id = {}

        self.domains = []
        self.domain_by_id = {}

        self.devices = []
        self.device_by_id = {}
        self.device_lookup = {}
        self.device_black_list = []
        self.device_white_list = []

        self.system = System(self)
        self.data = {f"{i:02X}": {} for i in range(12)}

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        print(f"\r\n{json.dumps(self.structure, indent=4)}")  # TODO: deleteme

        if self.config.get("known_devices"):
            self.device_lookup.update(
                {
                    d.device_id: {
                        "friendly_name": d._friendly_name,
                        "blacklist": d._blacklist,
                    }
                    for d in self.devices
                }
            )

            with open(self.config["known_devices"], "w") as outfile:
                json.dump(self.device_lookup, outfile, sort_keys=True, indent=4)

        sys.exit()

    @property
    def structure(self) -> Optional[dict]:
        """Calculate a system schema."""
        controllers = [d for d in self.devices if d.device_type == "CTL"]
        if len(controllers) != 1:
            print("fail test 0: more/less than 1 controller")
            return

        structure = {
            "controller": controllers[0].device_id,
            "boiler": {
                "dhw_sensor": controllers[0].dhw_sensor,
                "tpi_relay": controllers[0].tpi_relay,
            },
            "zones": {},
            #  "devices": {},
        }

        orphans = structure["orphans"] = [
            d.device_id for d in self.devices if d.parent_zone is None
        ]

        structure["heat_demand"] = {
            d.device_id: d.heat_demand
            for d in self.devices
            if hasattr(d, "heat_demand")
        }

        thermometers = structure["thermometers"] = {
            d.device_id: d.temperature
            for d in self.devices
            if hasattr(d, "temperature")
        }
        thermometers.pop(structure["boiler"]["dhw_sensor"], None)

        for z in self.zone_by_id:  # [z.zone_idx for z in self.zones]:
            actuators = [k for d in self.data[z].get("actuators", []) for k in d.keys()]
            children = [d.device_id for d in self.devices if d.parent_zone == z]

            zone = structure["zones"][z] = {
                "name": self.data[z].get("name"),  # TODO: do it this way
                "temperature": self.zone_by_id[z].temperature,  # TODO: or this way
                "heat_demand": self.zone_by_id[z].heat_demand,
                "sensor": None,
                "actuators": actuators,
                "children": children,  # TODO: could this include non-actuators?
                "devices": list(set(actuators) | set(children)),
            }
            orphans = list(set(orphans) - set(zone["devices"]))

        # check each zones has a unique (and non-null) temperature
        zone_map = {
            str(v["temperature"]): k
            for k, v in structure["zones"].items()
            if v["temperature"] is not None
        }

        structure["orphans"] = orphans

        # for z in self.zone_by_id:  # [z.zone_idx for z in self.zones]:
        #     if

        # TODO: needed? or just process only those with a unique temp?
        if len(zone_map) != len(structure["zones"]):  # duplicate/null temps
            print("fail test 1: non-unique (null) zone temps")
            return structure

        # check all possible sensors have a unique temp - how?
        temp_map = [t for t in thermometers.values() if t is not None]
        if len(temp_map) != len(thermometers):  # duplicate/null temps
            print("fail test 2: null device temps")
            return structure

        temp_map = {str(v): k for k, v in thermometers.items() if v is not None}

        for zone_idx in structure["zones"]:
            zone = structure["zones"][zone_idx]
            sensor = temp_map.get(str(zone["temperature"]))
            if sensor:
                zone["sensor"] = sensor
                # if sensor in structure["orphans"]:
                #     structure["orphans"].remove(sensor)
                orphans = list(set(orphans) - set(sensor))

        # TODO: max 1 remaining zone without a sensor
        # if len(thermometers) == 0:
        # structure.pop("thermometers")

        structure["orphans"] = orphans

        return structure

    async def start(self) -> None:
        """Fake the docstring."""

        def _setup_files() -> None:
            if self.config.get("database"):
                self._output_db = sqlite3.connect(self.config["database"])
                self._db_cursor = self._output_db.cursor()
                _ = self._db_cursor.execute(TABLE_SQL)  # create if not existant
                _ = self._db_cursor.execute(INDEX_SQL)  # index if not existant
                self._output_db.commit()

            if self.config.get("known_devices"):
                try:
                    with open(self.config["known_devices"]) as json_file:
                        devices = self.device_lookup = json.load(json_file)
                except FileNotFoundError:
                    self.device_lookup = {}
                else:
                    if self.config["white_list"]:
                        self.device_white_list = [
                            k for k, v in devices.items() if not v.get("blacklist")
                        ]
                    else:
                        self.device_black_list = [
                            k for k, v in devices.items() if v.get("blacklist")
                        ]

        _setup_files()

        # Finally, source of packets is either a text file, or a serial port:
        if self.config.get("input_file"):
            with open(self.config["input_file"]) as self._input_fp:
                for ts_packet in self._input_fp:  # main loop when packets from file
                    timestamp, packet = ts_packet[:26], ts_packet[27:].strip()

                    self._proc_packet(timestamp, packet)
                    await self._send_packet(destination=None)  # to empty the buffer

        else:  # if self.config["serial_port"] or if self.serial_port
            try:
                self.reader, self.writer = await serial_asyncio.open_serial_connection(
                    loop=self.loop,
                    url=self.serial_port,
                    baudrate=BAUDRATE,
                    timeout=READ_TIMEOUT,
                    xonxoff=True,
                )
            except serial.serialutil.SerialException:
                raise  # TODO: do better

            if self.config.get("execute_cmd"):  # e.g. "RQ 01:145038 0418 000000"
                cmd = self.config["execute_cmd"]
                self.command_queue.put_nowait(
                    Command(self, cmd[13:17], cmd[:2], cmd[3:12], cmd[18:])
                )
                await self._send_packet(destination=self.writer)

            # with open(self.config["serial_port"]) as serial_port:
            #     self.reader, self.writer = serial_port

            while True:  # main loop when packets from serial port
                timestamp, packet = await get_packet_from_port(self.reader)

                self._proc_packet(timestamp, packet)
                if self.reader._transport.serial.in_waiting == 0:
                    await self._send_packet(destination=self.writer)

    def _proc_packet(self, timestamp, packet) -> None:
        """Receive a packet and validate it as a message."""

        def _useful_packet(timestamp, packet) -> bool:
            """Process the packet."""
            if not is_valid_packet(packet, timestamp):
                return  # drop all invalid packets, log if so

            if not is_wanted_device(
                packet, self.device_white_list, self.device_black_list
            ):
                return  # silently drop packets containing unwanted devices

            # if archiving, store all valid packets, even those not to be parsed
            if self._output_db:
                tsp = f"{timestamp} {packet}"
                w = [0, 27, 31, 34, 38, 48, 58, 68, 73, 77, 165]  # 165? 199 works
                data = tuple([tsp[w[i - 1] : w[i] - 1] for i in range(1, len(w))])

                _ = self._db_cursor.execute(INSERT_SQL, data)

                self._output_db.commit()

            if not is_wanted_packet(packet, timestamp, self.config["black_list"]):
                return  # drop packets containing black-listed text

            return True

        def _decode_packet(timestamp, packet) -> bool:
            """Decode the packet and its payload."""
            try:
                msg = Message(packet, timestamp, self)
            except (ValueError, AssertionError):
                _LOGGER.exception(
                    "%s", packet, extra={"date": timestamp[:10], "time": timestamp[11:]}
                )
                return

            if not msg.is_valid_payload:
                return

            # UPDATE: only certain packets should become part of the canon
            try:
                if "18" in msg.device_id[0][:2]:  # not working?, see KeyError
                    return
                elif msg.device_id[0][:2] == "--":
                    self.device_by_id[msg.device_id[2]].update(msg)
                else:
                    self.device_by_id[msg.device_id[0]].update(msg)
            except KeyError:  # TODO: KeyError: '18:013393'
                pass

        if _useful_packet(timestamp, packet):
            if not self.config.get("raw_output"):
                _decode_packet(timestamp, packet)

    async def _send_packet(self, destination) -> None:
        """Send a command unless in listen_only mode."""
        if not self.command_queue.empty():
            cmd = self.command_queue.get()

            if destination is None:
                pass
            elif destination is self.writer:
                # TODO: if not cmd.entity._pkts.get(cmd.code):
                self.writer.write(bytearray(f"{cmd}\r\n".encode("ascii")))
                await asyncio.sleep(0.05)  # 0.05 works well, 0.03 too short

            self.command_queue.task_done()
