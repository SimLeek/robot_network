# todo: need to complete subsystem switching

from dataclasses import dataclass
from typing import Optional

from robonet.brain.display_system import DisplaySubSystem
from robonet.brain.ai_system import AISubSystem
from robonet.brain.menu_system import MenuSubSystem
from robonet.brain.radio_system import RadioSubSystem
import robonet.brain.settings as settings_
from robonet.brain.util.action_factory import ActionFactory
from robonet.brain.util.desktop_window_config import register_desktop_actions
from robonet.brain.util.system_base import SubSystem
from robonet.logging_setup import setup_logging
import asyncio

log = setup_logging()
settings = settings_.get()

class ServerSystem:
    def __init__(
        self,
        radio: RadioSubSystem,
        menu: MenuSubSystem,
        displayer: DisplaySubSystem = None,
        ai: AISubSystem = None,
    ):
        self.radio   = radio
        self.menu = menu

        assert displayer or ai, "Something must see what is going on"

        self.displayer = displayer
        self.ai = ai
        self.active_sub = None
        self.actions = ActionFactory()

        self.radio.setup(self)
        self.menu.setup(self)

        if displayer is not None:
            self.displayer.setup(self)
        if ai is not None:
            self.ai.setup(self)

    def start(self):
        self.menu.start()
        if self.active_sub is not None:
            self.active_sub.start()

    def stop(self):
        if self.active_sub is not None:
            self.active_sub.stop()

    def swap_subsystem(self, new_sub: SubSystem):
        """Hot-swap the active endpoint SubSystem."""
        if self.active_sub and hasattr(self.active_sub, '_tasks'):
            for t in self.active_sub._tasks:
                t.cancel()

        self.active_sub = new_sub
        new_sub.setup(self)
        self.radio.rebuild_handlers(self)

        # Re-register controls with a fresh ActionFactory

        if self.displayer:
            self.displayer.af_thru.unbind_all()
            self.displayer.af_edit.unbind_all()
            register_desktop_actions(self.displayer.af_thru, self.displayer.af_edit, self)
        elif self.ai:
            pass  # todo: add ai neuron or token controls

        new_sub.start()

        new_sub._tasks = [asyncio.ensure_future(c) for c in new_sub.async_loops(self)]
        print(f"[ServerSystem] active SubSystem → {type(new_sub).__name__}")

    def register_default_human_controls(self, af: ActionFactory):
        self.menu.register_default_human_controls(af)
        # todo: put endpoint specific human controls here

    def register_edit_human_controls(self, af: ActionFactory):
        self.menu.register_edit_human_controls(af)
        # todo: put endpoint specific human controls here

    def register_default_token_controls(self, af: ActionFactory):
        self.menu.register_thru_token_controls(af)
        # todo: put endpoint specific token controls here

    def register_default_neuron_controls(self, af: ActionFactory):
        self.menu.register_thru_neuron_controls(af)
        # todo: put endpoint specific neuron controls here

    def async_loops(self):
        loops = [
            self.displayer.run(self),
            *self.radio.async_loops(self),
            *self.menu.async_loops(self)
        ]
        if self.active_sub is not None:
            loops += [ *self.active_sub.async_loops(self)]
        return loops
