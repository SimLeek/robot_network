from __future__ import annotations

import typing
if typing.TYPE_CHECKING:
    from robonet.endpoint.hardware_system import RobotHardware
    from robonet.endpoint.radio_system import RobotRadio

import asyncio
from robotar.logging_setup import setup_logging
log = setup_logging()
import robonet.endpoint.settings as settings_


settings = settings_.get()


class RobotNode:
    def __init__(self, radio: 'RobotRadio', hardware: 'RobotHardware'):
        self.radio = radio
        self.hardware = hardware
        self.loop = None

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.radio.setup(self)
        self.hardware.setup(self)
        try:
            await asyncio.gather(
                *self.radio.async_loops(),
                *self.hardware.async_loops(),
            )
        finally:
            self.radio.stop()
