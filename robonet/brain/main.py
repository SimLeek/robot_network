# in working state

import argparse
import asyncio
from pathlib import Path

from robonet.brain.ai_system import AISubSystem
from robonet.brain.main_system import MenuSubSystem, ServerSystem
from robonet.brain.radio_system import RadioSubSystem
from robonet.bridge.bridge_control import BridgeServer


async def main(headless: bool = False, monitor_dir=None, monitor_interval_s: float = 1.0):
    radio = RadioSubSystem()
    menu = MenuSubSystem()
    disp = None
    if not headless:
        from robonet.brain.display_system import DisplaySubSystem
        disp = DisplaySubSystem()
    ai = AISubSystem()  # no in-process AI logic -- just a bridge host, so a real external AI process can attach
    ai.bridge = BridgeServer()
    serv = ServerSystem(radio, menu, disp, ai=ai)
    serv.start()

    loops = serv.async_loops()
    if monitor_dir is not None:
        from robonet.brain.monitor import Monitor
        loops.append(Monitor(serv, monitor_dir, monitor_interval_s).run())
    await asyncio.gather(*loops)

    serv.stop()


def cli():
    ap = argparse.ArgumentParser(description='robonet brain')
    ap.add_argument('--headless', action='store_true',
                    help='no viewer window (AI-only session); see --monitor-dir to watch it')
    ap.add_argument('--monitor-dir', type=Path, default=None,
                    help='write frame.png + status.json here every --monitor-interval seconds')
    ap.add_argument('--monitor-interval', type=float, default=1.0)
    args = ap.parse_args()
    asyncio.run(main(args.headless, args.monitor_dir, args.monitor_interval))


if __name__ == '__main__':
    cli()
