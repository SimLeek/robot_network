# in working state

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Set, Tuple, Optional


@dataclass
class ActionInfo:
    """
    Information about inputs bound to an action.
    """
    keys: List[int] = field(default_factory=list)
    tokens: List[int] = field(default_factory=list)
    neurons: List[Tuple[int, float]] = field(default_factory=list)

    def __str__(self) -> str:
        parts = []
        if self.keys:
            key_strs = [chr(k) if 32 <= k <= 126 else str(k) for k in self.keys]
            parts.append(f"Keys: {', '.join(key_strs)}")
        if self.tokens:
            parts.append(f"Tokens: {', '.join(str(t) for t in self.tokens)}")
        if self.neurons:
            neuron_strs = [f"{i}@{t:.2f}" for i, t in self.neurons]
            parts.append(f"Neurons: {', '.join(neuron_strs)}")
        return '; '.join(parts) or 'No bindings'


class ActionFactory:
    """
    Maps ActionBinding -> callable and dispatches from multiple input
    modalities (keyboard, AI tokens, AI neurons).
    """

    def __init__(self):
        self._key_to_handlers: Dict[int, Set[Callable]] = {}
        self._mouse_move_handler: Optional[Callable[[float, float], None]] = None
        self._mouse_click_handler: Optional[Callable[[float, float, int], None]] = None
        self._mouse_scroll_handler: Optional[Callable[[int], None]] = None
        self._keyboard_handler: List[Callable[[int, int, int], None]] = []

        self._token_to_handlers: Dict[int, Set[Callable]] = {}
        self._neuron_to_handler_thresholds: Dict[int, Dict[Callable, float]] = {}

    # region Binding

    def bind_key(self, handler: Callable, key_code: int) -> ActionFactory:
        self._key_to_handlers.setdefault(key_code, set()).add(handler)
        return self

    def bind_keyboard(self, handler:Callable):
        self._keyboard_handler.append(handler)
        return self

    def bind_mouse_move(self, handler: Callable):
        self._mouse_move_handler = handler
        return self

    def bind_mouse_click(self, handler: Callable):
        self._mouse_click_handler = handler
        return self

    def bind_mouse_scroll(self, handler:Callable):
        self._mouse_scroll_handler = handler
        return self

    def bind_ai_token(self, handler: Callable,
                      token_id: int) -> ActionFactory:
        # tokens cannot work like mouse or joystick controls. Use neuron outputs.
        self._token_to_handlers.setdefault(token_id, set()).add(handler)
        return self

    def bind_ai_neuron(self, handler: Callable,
                       neuron_index: int,
                       threshold: float = 0.5) -> ActionFactory:
        # set threshold to 0 to use as a mouse or joystick
        neuron_dict = self._neuron_to_handler_thresholds.setdefault(neuron_index, {})
        neuron_dict[handler] = threshold
        return self

    # endregion

    # region Unbinding

    def unbind_all(self) -> ActionFactory:
        self._key_to_handlers.clear()
        self._token_to_handlers.clear()
        self._neuron_to_handler_thresholds.clear()
        self._keyboard_handler.clear()
        self._mouse_move_handler = None
        self._mouse_click_handler = None
        self._mouse_scroll_handler = None
        return self

    def unbind_handler(self, handler: Callable) -> ActionFactory:
        # note: cannot unbind special controls such as mouse move
        for kc in list(self._key_to_handlers):
            self._key_to_handlers[kc].discard(handler)
            if not self._key_to_handlers[kc]:
                del self._key_to_handlers[kc]
        for tid in list(self._token_to_handlers):
            self._token_to_handlers[tid].discard(handler)
            if not self._token_to_handlers[tid]:
                del self._token_to_handlers[tid]
        for ni in list(self._neuron_to_handler_thresholds):
            if handler in self._neuron_to_handler_thresholds[ni]:
                del self._neuron_to_handler_thresholds[ni][handler]
            if not self._neuron_to_handler_thresholds[ni]:
                del self._neuron_to_handler_thresholds[ni]
        return self

    def unbind_key(self, key_code: int, handler: Optional[Callable] = None) -> ActionFactory:
        if key_code is None:
            raise ValueError("key_code must be specified")
        if handler is not None and key_code is not None:
            if key_code in self._key_to_handlers:
                self._key_to_handlers[key_code].discard(handler)
                if not self._key_to_handlers[key_code]:
                    del self._key_to_handlers[key_code]
        elif key_code is not None:
            if key_code in self._key_to_handlers:
                del self._key_to_handlers[key_code]
        return self

    def unbind_keyboard(self):
        self._keyboard_handler = []
        return self

    def unbind_mouse_move(self):
        self._mouse_move_handler = None
        return self

    def unbind_mouse_click(self):
        self._mouse_click_handler = None
        return self

    def unbind_mouse_scroll(self):
        self._mouse_scroll_handler = None
        return self

    def unbind_ai_token(self, token_id:int, handler: Optional[Callable] = None) -> ActionFactory:
        if token_id is None:
            raise ValueError("token_id must be specified")
        if handler is not None and token_id is not None:
            if token_id in self._token_to_handlers:
                self._token_to_handlers[token_id].discard(handler)
                if not self._token_to_handlers[token_id]:
                    del self._token_to_handlers[token_id]
        elif token_id is not None:
            if token_id in self._token_to_handlers:
                del self._token_to_handlers[token_id]
        return self

    def unbind_ai_neuron(self, neuron_index:int, handler: Optional[Callable] = None) -> ActionFactory:
        if neuron_index is None:
            raise ValueError("neuron_index must be specified")
        if handler is not None and neuron_index is not None:
            if neuron_index in self._neuron_to_handler_thresholds:
                if handler in self._neuron_to_handler_thresholds[neuron_index]:
                    del self._neuron_to_handler_thresholds[neuron_index][handler]
                if not self._neuron_to_handler_thresholds[neuron_index]:
                    del self._neuron_to_handler_thresholds[neuron_index]
        elif neuron_index is not None:
            if neuron_index in self._neuron_to_handler_thresholds:
                del self._neuron_to_handler_thresholds[neuron_index]
        return self

    # endregion

    # region Dispatch

    def on_key(self, key_code: int, action_press: bool,
               modifiers: Any = None):
        if not action_press:
            return
        handlers = self._key_to_handlers.get(key_code, set())
        for handler in handlers:
            handler()

    def on_keyboard(self, key, action, modifiers):
        # can run alongside key handlers, but there is only one
        for keyboard_handler in self._keyboard_handler:
            keyboard_handler(key, action, modifiers)

    def on_mouse_move(self, x:float, y:float):
        if self._mouse_move_handler is not None:
            self._mouse_move_handler(x,y)

    def on_mouse_click(self, x:float, y:float, b: int):
        if self._mouse_click_handler is not None:
            self._mouse_click_handler(x,y, b)

    def on_mouse_scroll(self, y_offset:int):
        if self._mouse_scroll_handler is not None:
            self._mouse_scroll_handler(y_offset)

    def on_token(self, token_id: int):
        handlers = self._token_to_handlers.get(token_id, set())
        for handler in handlers:
            handler()

    def on_neuron_outputs(self, output_vector):
        for neuron_index in self._neuron_to_handler_thresholds:
            if neuron_index < len(output_vector):
                value = output_vector[neuron_index]
                for handler, thresh in self._neuron_to_handler_thresholds[neuron_index].items():
                    if value >= thresh:
                        handler(value)

    # endregion

    # region Printing

    def __str__(self) -> str:
        rev: Dict[Callable, ActionInfo] = defaultdict(ActionInfo)
        for key_code, handlers in self._key_to_handlers.items():
            for h in handlers:
                rev[h].keys.append(key_code)
        for token_id, handlers in self._token_to_handlers.items():
            for h in handlers:
                rev[h].tokens.append(token_id)
        for neuron_index, h_to_thresh in self._neuron_to_handler_thresholds.items():
            for h, thresh in h_to_thresh.items():
                rev[h].neurons.append((neuron_index, thresh))
        # Sort lists
        for info in rev.values():
            info.keys.sort()
            info.tokens.sort()
            info.neurons.sort()
        # Build string
        lines = []
        for h, info in sorted(rev.items(), key=lambda x: x[0].__name__ or ''):
            name = h.__name__ if hasattr(h, '__name__') and h.__name__ != '<lambda>' else 'lambda'
            lines.append(f"{name}: {info}")

        system_handlers = []
        for keyboard_handler in self._keyboard_handler:
            system_handlers.append(f"Keyboard: {getattr(keyboard_handler, '__name__', 'lambda')}")
        if self._mouse_move_handler:
            system_handlers.append(f"Mouse Move: {getattr(self._mouse_move_handler, '__name__', 'lambda')}")
        if self._mouse_click_handler:
            system_handlers.append(f"Mouse Click: {getattr(self._mouse_click_handler, '__name__', 'lambda')}")
        if self._mouse_scroll_handler:
            system_handlers.append(f"Mouse Scroll: {getattr(self._mouse_scroll_handler, '__name__', 'lambda')}")

        if system_handlers:
            if lines: lines.append("")  # Spacer
            lines.append("--- Global Handlers ---")
            lines.extend(system_handlers)

        return '\n'.join(lines)

    def __repr__(self) -> str:
        return self.__str__()

    # endregion