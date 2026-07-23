# in working state

import asyncio

from robonet.brain.ai_system import AISubSystem
from robonet.brain.main_system import MenuSubSystem, ServerSystem
from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.radio_system import RadioSubSystem
from robonet.bridge.bridge_control import BridgeServer


async def main():
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    disp = DisplaySubSystem()
    ai = AISubSystem()  # no in-process AI logic -- just a bridge host, so a real external AI process can attach
    ai.bridge = BridgeServer()
    serv = ServerSystem(radio, menu, disp, ai=ai)
    serv.start()

    loops = serv.async_loops()
    await asyncio.gather(*loops)

    serv.stop()

if __name__ == '__main__':
    asyncio.run(main())