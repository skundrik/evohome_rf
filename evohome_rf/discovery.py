#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
"""Evohome serial discovery scripts."""

import asyncio
import logging

from .command import Command, Priority
from .const import __dev_mode__, CODE_SCHEMA, DEVICE_TABLE, Address


_LOGGER = logging.getLogger(__name__)
if False and __dev_mode__:
    _LOGGER.setLevel(logging.DEBUG)
else:
    _LOGGER.setLevel(logging.WARNING)


async def periodic(gwy, cmd, count=1440, interval=5):
    if count <= 0:
        while True:
            await asyncio.sleep(interval)
            gwy._que.put_nowait(cmd)
    else:
        for _ in range(count):
            await asyncio.sleep(interval)
            gwy._que.put_nowait(cmd)


async def schedule_task(delay, func, *args, **kwargs):
    """Start a coro after delay seconds."""

    async def scheduled_func(delay, func, *args, **kwargs):
        await asyncio.sleep(delay)
        await func(*args, **kwargs)

    asyncio.create_task(scheduled_func(delay, func, *args, **kwargs))


async def get_faults(gwy, device_id):
    dev_addr = Address(id=device_id, type=device_id[:2])
    device = gwy._get_device(dev_addr, ctl_addr=dev_addr)

    device._evo._fault_log.start()
    while not device._evo._fault_log._fault_log_done:
        await asyncio.sleep(0.05)
    # print("get_faults", device._evo.fault_log())

    await gwy.shutdown("get_faults()")


async def get_schedule(gwy, device_id, zone_id):
    dev_addr = Address(id=device_id, type=device_id[:2])
    zone = gwy._get_device(dev_addr, ctl_addr=dev_addr)._evo._get_zone(zone_id)

    await zone._schedule.start()
    while not zone._schedule._schedule_done:
        await asyncio.sleep(0.05)
    # print("get_schedule", zone.schedule())

    await gwy.shutdown("get_schedule()")


def poll_device(gwy, device_id):
    qos = {"retry_limit": 0, "priority": Priority.LOW}

    if "poll_codes" in DEVICE_TABLE.get(device_id[:2]):
        codes = DEVICE_TABLE[device_id[:2]]["poll_codes"]
    else:
        codes = ["0016", "1FC9"]

    for code in codes:
        cmd = Command("RQ", device_id, code, "00", **qos)
        _ = asyncio.create_task(periodic(gwy, cmd, count=0, interval=60))
        cmd = Command("RQ", device_id, code, "0000", **qos)
        _ = asyncio.create_task(periodic(gwy, cmd, count=0, interval=60))


def probe_device(gwy, device_id):
    _LOGGER.warning("probe_device() invoked - expect a lot of Warnings")

    # for _code in range(0x4000):
    #     code = f"{_code:04X}"

    qos = {"retry_limit": 0, "priority": Priority.LOW}

    for code in sorted(CODE_SCHEMA):
        if code == "0005":
            for zone_type in range(18):
                cmd = Command("RQ", device_id, code, f"00{zone_type:02X}", **qos)
                asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))
            continue

        if code == "000C":
            for zone_idx in range(16):
                cmd = Command("RQ", device_id, code, f"{zone_idx:02X}00", **qos)
                asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))
            continue

        if code == "0418":
            for log_idx in range(2):
                cmd = Command("RQ", device_id, code, f"{log_idx:06X}", **qos)
                asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))
            continue

        if code == "1100":
            cmd = Command("RQ", device_id, code, "FC", **qos)
            asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))
            continue

        if code == "2E04":
            cmd = Command("RQ", device_id, code, "FF", **qos)
            asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))
            continue

        cmd = Command("RQ", device_id, code, "00", **qos)
        asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))

        cmd = Command("RQ", device_id, code, "0000", **qos)
        asyncio.create_task(periodic(gwy, cmd, count=1, interval=0))

    # for code in ("0016", "1FC9"):  # payload 0000 OK for both these
    #     cmd = Command("RQ", device_id, code, "0000", retry_limit=9)
    #     asyncio.create_task(periodic(gwy, cmd, count=1))


# if self.config.get("evofw_flag") and "evofw3" in raw_pkt.packet:
#     # !V, !T - print the version, or the current mask
#     # !T00   - turn off all mask bits
#     # !T01   - cause raw data for all messages to be printed
#     await manager.put_pkt(self.config["evofw_flag"], _LOGGER)


# # used for development only...
# for code in range(0x4000):
#     # cmd = Command("RQ", "01:145038", f"{code:04X}", "0000")
#     cmd = Command("RQ", "13:035462", f"{code:04X}", "0000")
#     await destination.put_pkt(cmd, _LOGGER)
#     if code % 0x10 == 0:
#         await asyncio.sleep(15)  # 10 too short - 15 seconds works OK


# # used for development only...
# for payload in ("0000", "0100", "F8", "F9", "FA", "FB", "FC", "FF"):
#     cmd = Command("RQ", "01:145038", "11F0", payload)
#     await destination.put_pkt(cmd, _LOGGER)
#     cmd = Command("RQ", "13:035462", "11F0", payload)
#     await destination.put_pkt(cmd, _LOGGER)


# for device_type in ("0D", "0E", "0F"):  # CODE_000C_DEVICE_TYPE:
#     cmd = Command("RQ", "01:145038", "000C", f"00{device_type}")
#     await manager.put_pkt(cmd, _LOGGER)


# for z in range(4):
#     for x in range(12):
#         cmd = Command("RQ", "01:145038", "000C", f"{z:02X}{x:02X}")
#         await manager.put_pkt(cmd, _LOGGER)


# for p in ("00", "01", "FF", "0000", "0100", "FF00"):
#     for c in ("0003", "0007", "000B", "000D", "000F"):
#         cmd = Command("RQ", "01:145038", c, f"0008{p}")
#         print(cmd)
#         await manager.put_pkt(cmd, _LOGGER)
