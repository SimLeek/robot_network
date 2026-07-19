only put [complete|todo] followed by short descriptions for every bullet point in this file

- [todo] zmq shared-memory bridge between robot_network's brain and a separate AI process (AI crashing must not take robot_network down and vice versa)
- [todo] AI process lifecycle handling: slow startup and safe shutdown, since spinning up/down can take a while
  - [todo] abstract base class the AI side must implement, with four required callbacks
  - [todo] on_robonet_start: fires when robonet itself starts
  - [todo] on_robonet_shutdown: fires on shutdown, sent through the bridge to trigger a callback on the AI side
  - [todo] on_robonet_connect: fires when robonet connects to an endpoint
  - [todo] on_robonet_disconnect: fires when robonet disconnects from an endpoint
  - [todo] rationale: lets connect/disconnect work with a standard/simple AI, while a more stable AI can use start/shutdown to manage the (currently buggy) menu directly, or run restart attempts on shutdown, etc.
- [todo] brain->AI signal: is the brain running as expected (framerate + last-update timestamp)
- [todo] brain->AI signal: is the brain connected to an endpoint, and which one
- [todo] AI->brain signal: is the AI running (liveness)
- [todo] AI->brain signal: does the AI want AI control or human control
- [todo] endpoint side: highlight text, run xsel, send the selected text over the wire
- [todo] brain side: receive selected text, print to console for a human, and forward it to the AI
