# in working state

import abc
from typing import Dict,  Type

from robonet.brain.util.action_factory import ActionFactory


class SubSystem(abc.ABC):
    _registry: Dict[str, Type['SubSystem']] = {}

    def register_thru_human_controls(self, af: ActionFactory):
        pass

    def register_thru_token_controls(self, af: ActionFactory):
        pass

    def register_thru_neuron_controls(self, af: ActionFactory):
        pass

    def register_edit_human_controls(self, af: ActionFactory):
        pass

    def register_edit_token_controls(self, af: ActionFactory):
        pass

    def register_edit_neuron_controls(self, af: ActionFactory):
        pass

    @abc.abstractmethod
    def start(self):
        raise NotImplementedError

    def __enter__(self):
        self.start()

    @abc.abstractmethod
    def stop(self):
        raise NotImplementedError

    def __del__(self):
        self.stop()

    @classmethod
    def register(cls, endpoint_type: str, subclass: Type['SubSystem']):
        cls._registry[endpoint_type] = subclass

    @classmethod
    def for_endpoint_type(cls, endpoint_type: str) -> Type['SubSystem']:
        sub = cls._registry.get(endpoint_type)
        if sub is None:
            raise KeyError(f"No SubSystem registered for endpoint_type={endpoint_type!r}. "
                           f"Available: {list(cls._registry)}")
        return sub