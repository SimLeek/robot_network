"""
robotar/robot_model.py — MasterPi robot node (robotar example).

Network mode and ports are read from ~/.robobrain/settings.json.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Union, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from robonet.buffers.buffer_objects import RobotCapabilities
import threading
from examples.basicpibot.actual_robot import Robot, MoveCommand, ArmVelocity, ARM_JOINT_LIMITS, TICK_RATE
from robonet.endpoint.hardware_system import CamMicSpkRobotHardware
from robonet.endpoint.base import RobotNode
from robonet.endpoint.radio_system import RobotRadio, HOSTNAME
from robonet.endpoint.settings import get as get_settings

settings = get_settings()
log = logging.getLogger(__name__)

from dataclasses import dataclass, asdict


@dataclass
class Axis:
    name: str
    index: int
    description: str
    keys: list[list[int | float]]
    neuron: int
    servo_id: int | None = None
    limit_lo: float | None = None
    limit_hi: float | None = None


@dataclass
class CamStream:
    name: str
    type: str
    width: int
    height: int


@dataclass
class MicStream:
    name: str
    type: str
    sample_rate: int
    channels: int


def build_masterpi_capabilities(robot: Robot) -> RobotCapabilities:
    axes: list[Axis] = [
        Axis("forward", 0, "wheel forward / back", [[ord("w"), 1.0], [ord("s"), -1.0]], 0),
        Axis("strafe_right", 1, "wheel strafe left / right", [[ord("d"), 1.0], [ord("a"), -1.0]], 1),
        Axis("turn_right", 2, "wheel yaw", [[ord("e"), 1.0], [ord("q"), -1.0]], 2),
        Axis("in_out", 3, "mecanum in / out", [[ord("r"), 1.0], [ord("f"), -1.0]], 3),
    ]

    lims = [ARM_JOINT_LIMITS.get(x, (-1.0, 1.0)) for x in [6, 5, 4, 3, 1]]
    axes += [
        Axis("yaw", 4, f"arm yaw. Limits: {lims[0]}", [[ord("l"), 1.0], [ord("j"), -1.0]], 4, 6, *lims[0]),
        Axis("p1", 5, f"p1 pitch. Limits: {lims[1]}", [[ord("p"), 1.0], [ord(";"), -1.0]], 5, 5, *lims[1]),
        Axis("p2", 6, f"p2 pitch. Limits: {lims[2]}", [[ord("u"), 1.0], [ord("o"), -1.0]], 6, 4, *lims[2]),
        Axis("p3", 7, f"p3 pitch. Limits: {lims[3]}", [[ord("i"), 1.0], [ord("k"), -1.0]], 7, 3, *lims[3]),
        Axis("grip", 8, f"grip. Limits: {lims[4]}", [[ord("n"), 1.0], [ord("m"), -1.0]], 8, 1, *lims[4]),
    ]

    streams: list[Union[CamStream, MicStream]] = [
        CamStream("camera", "mjpeg", robot.camera_width, robot.camera_height),
        MicStream("microphone", "audio_float32", robot.mic_rate or 48000, 1),
    ]

    return RobotCapabilities.build(
        axes=[asdict(axis) for axis in axes],
        streams=[asdict(stream) for stream in streams],
        hostname=HOSTNAME,
    )


class MasterPiHw(CamMicSpkRobotHardware):
    def __init__(self):
        super().__init__()
        self._robot = Robot()

    def setup(self, parent):
        super().setup(parent)
        # Hardware always needs to be running — serial, audio, position polling.
        # This is independent of whether a server ever connects.
        self._robot.start()
        log.info('MasterPi hardware started')

    def build_capabilities(self) -> RobotCapabilities:
        return build_masterpi_capabilities(self._robot)

    def halt(self):
        self._robot.stop_drive()
        self._robot.arm_stop()

    def stop(self):
        self._robot.on_frame = None
        self._robot.on_audio = None
        self._robot.stop()
        super().stop()

    def async_loops(self) -> list:
        return [*super().async_loops(), self._arm_tick_loop()]

    async def _arm_tick_loop(self):
        dt = 1.0 / TICK_RATE
        while True:
            self._robot.arm_tick()
            await asyncio.sleep(dt)

    def apply_tensor(self, idx_vec: np.ndarray, val_vec: np.ndarray):
        n = len(idx_vec)
        if n < 1:
            log.warning("Control vector empty — ignoring", n)
            return

        drive_mappings = {
            0: 'fwd',
            1: 'strafe_right',
            2: 'turn_right',
            3: 'in_out'
        }
        arm_mappings = {
            4: 'yaw',
            5: 'p1',
            6: 'p2',
            7: 'p3',
            8: 'grip'
        }

        mv_dict = {}
        arm_dict = {}
        for i, v in zip(idx_vec, val_vec):
            if i in drive_mappings:
                mv_dict[drive_mappings[i]] = v
            elif i in arm_mappings:
                arm_dict[arm_mappings[i]] = v
            else:
                log.error(f"Received bad sparse movement index: {i}")

        if mv_dict:
            self._robot.drive(MoveCommand(**mv_dict))
        if arm_dict:
            self._robot.arm_set_velocity(ArmVelocity(**arm_dict))


if __name__ == '__main__':
    radio = RobotRadio()
    hw = MasterPiHw()
    main_node = RobotNode(radio, hw)
    asyncio.run(main_node.run())
