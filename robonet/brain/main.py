# in working state

import asyncio

from robonet.brain.main_system import MenuSubSystem, ServerSystem
from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.radio_system import RadioSubSystem
from robonet.bridge.bridge_control import BridgeServer


async def main():
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    disp = DisplaySubSystem()
    bridge = BridgeServer()  # a separate AI process is optional -- robonet runs fine with nothing attached
    serv = ServerSystem(radio, menu, disp, bridge=bridge)
    serv.start()

    loops = serv.async_loops()
    await asyncio.gather(*loops)

    serv.stop()

if __name__ == '__main__':
    asyncio.run(main())