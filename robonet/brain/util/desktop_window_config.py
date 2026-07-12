# in working state

from displayarray.input_mgl import PassthruMglWindowConfig
from robonet.brain.util.action_factory import ActionFactory
from robonet.logging_setup import setup_logging
logger = setup_logging()


def register_desktop_actions(af_thru: ActionFactory, af_edit:ActionFactory, sm) -> None:
    """
    Register all desktop-subsystem actions into *af*.

    Call this once after both *af* and *sm* exist.  AI token / neuron
    bindings are application-specific; set them from your training harness:

        af.bind_ai_neuron(toggle_mic_binding, neuron_index=7)
        af.bind_ai_token(menu_binding, token_id=42)
    """
    # Key codes
    KEY_BACKTICK  = 96
    KEY_T         = ord('T')
    KEY_SLASH     = ord('?')
    # Edit-mode scroll actions use y_offset sign; register as separate actions.
    # Mouse/scroll forwarding in pass-through is handled directly in the
    # callback (not key-bound) because it carries continuous float data.


    sm.register_default_human_controls(af_thru)
    sm.register_edit_human_controls(af_edit)

    # these shouldn't be here. They should be part of the AI system
    sm.register_default_token_controls(af_thru)
    sm.register_default_neuron_controls(af_thru)

    return

def make_window_config_for_server(sm, af_thru: ActionFactory = None, af_edit: ActionFactory = None):
    """
    Build a PassthruMglWindowConfig subclass wired to *sm*.

    All key events in both pass-through and edit mode go through af.on_key().
    """
    if af_thru is None:
        af_thru = ActionFactory()

    if af_edit is None:
        af_edit = ActionFactory()

    register_desktop_actions(af_thru, af_edit, sm)

    # mouse_press/release don't carry texel coordinates themselves (only
    # px, py, button) -- track the most recent mouse_pos's tx, ty so a
    # press/release can use the same normalized-coordinate approach as
    # move, rather than the wrong, unscaled px,py.
    _last_texel = {'tx': 0.5, 'ty': 0.5}

    def pass_through_cb(event_type, frame, name, *args):
        if event_type == 'mouse_pos':
            px, py, tx, ty, sx, sy = args
            _last_texel['tx'] = tx
            _last_texel['ty'] = ty
            af_thru.on_mouse_move(ty, tx)  # swapped -- confirmed x/y were backwards
        elif event_type == 'mouse_press':
            px, py, button = args
            af_thru.on_mouse_press(_last_texel['ty'], _last_texel['tx'], button)
        elif event_type == 'mouse_release':
            px, py, button = args
            af_thru.on_mouse_release(_last_texel['ty'], _last_texel['tx'], button)
        elif event_type == 'mouse_scroll':
            x_offset, y_offset = args
            af_thru.on_mouse_scroll(int(y_offset))
        elif event_type == 'key':
            key, action, modifiers = args
            af_thru.on_keyboard(key, action, modifiers)

    def edit_cb(event_type, frame, name, *args):
        if event_type == 'mouse_scroll':
            x_offset, y_offset = args
            # Continuous data: dispatch zoom via ActionFactory binding name
            # so AI can also trigger it via neuron threshold on its own.
            af_edit.on_mouse_scroll(y_offset)
        elif event_type == 'mouse_pos':
            px, py, tx, ty, sx, sy = args
            #sm.mouse_tx = tx
            #sm.mouse_ty = ty
            af_edit.on_mouse_move(tx, ty)
        elif event_type == 'key':
            key, action, modifiers = args
            #wnd_keys = sm.displayer.displayer.config.wnd.keys
            af_edit.on_key(key, action, modifiers)
            af_edit.on_keyboard(key, action, modifiers)

    return PassthruMglWindowConfig.with_callbacks(pass_through_cb, edit_cb)

def make_window_config_for_server_main(sm, af: ActionFactory, obj):
    """
    Build a PassthruMglWindowConfig subclass wired to *sm*.

    All key events in both pass-through and edit mode go through af.on_key().
    """
    if af is None:
        af = ActionFactory()

    register_desktop_actions(af, sm)

    def pass_through_cb(event_type, frame, name, *args):
        if event_type == 'mouse_pos':
            px, py, tx, ty, sx, sy = args
            sm.handle_mouse_move(px, py)
        elif event_type == 'mouse_press':
            sx, sy, button = args
            sm.handle_mouse_click(sx, sy, button)
        elif event_type == 'mouse_scroll':
            x_offset, y_offset = args
            sm.handle_mouse_scroll(int(y_offset))
        elif event_type == 'key':
            key, action, modifiers = args
            wnd_keys = sm.displayer.displayer.config.wnd.keys
            # Route through ActionFactory first; if nothing consumed it,
            # forward the raw key to the remote client.
            af.on_key(key, action == wnd_keys.ACTION_PRESS, modifiers)
            if action == wnd_keys.ACTION_PRESS:
                sm.send_key(key, action, modifiers)

    def edit_cb(event_type, frame, name, *args):
        if event_type == 'mouse_scroll':
            x_offset, y_offset = args
            # Continuous data: dispatch zoom via ActionFactory binding name
            # so AI can also trigger it via neuron threshold on its own.
            if y_offset > 0:
                af.trigger(next(b for b in af._bindings if b.name == 'zoom_in'))
            elif y_offset < 0:
                af.trigger(next(b for b in af._bindings if b.name == 'zoom_out'))
        elif event_type == 'mouse_pos':
            px, py, tx, ty, sx, sy = args
            sm.mouse_tx = tx
            sm.mouse_ty = ty
        elif event_type == 'key':
            key, action, modifiers = args
            wnd_keys = sm.displayer.displayer.config.wnd.keys
            af.on_key(key, action == wnd_keys.ACTION_PRESS, modifiers)

    return PassthruMglWindowConfig.with_callbacks(pass_through_cb, edit_cb)