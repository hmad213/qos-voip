signaling/ is the TCP control channel (Step 1).
The call flow is INVITE → ACCEPT → media over UDP → BYE. Both sides must treat BYE as the signal to stop and flush logs.