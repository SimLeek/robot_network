# in working state

import asyncio

from robonet.brain.main_system import MenuSubSystem, ServerSystem
from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.radio_system import RadioSubSystem


async def main():
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    disp = DisplaySubSystem()
    serv = ServerSystem(radio, menu, disp)
    serv.start()

    loops = serv.async_loops()
    await asyncio.gather(*loops)

    serv.stop()

if __name__ == '__main__':
    asyncio.run(main())