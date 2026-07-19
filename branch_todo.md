only put [complete|todo] followed by short descriptions for every bullet point in this file

- [todo] zmq shared-memory bridge between robot_network's brain and a separate AI process
- [todo] fault isolation: AI crashing must not take robot_network down
- [todo] fault isolation: robot_network crashing must not take the AI down
- [todo] AI process lifecycle handling: slow startup and safe shutdown, since spinning up/down can take a while
- [todo] brain->AI signal: is the brain running as expected (framerate + last-update timestamp)
- [todo] brain->AI signal: is the brain connected to an endpoint, and which one
- [todo] AI->brain signal: is the AI running (liveness)
- [todo] AI->brain signal: does the AI want AI control or human control
- [todo] endpoint side: highlight text, run xsel, send the selected text over the wire
- [todo] brain side: receive selected text, print to console for a human, and forward it to the AI
