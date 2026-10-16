# Socket Guard

A [herdr](https://herdr.dev) plugin that exposes a filtered copy of the herdr API socket.

herdr's socket has no access control. Any process that can connect to it can drive the whole session: send keys to other panes, start agents, read output. Socket Guard starts with herdr and listens on a second socket. It forwards only an allowlist of API methods to herdr, by default the agent status reports sent by agent integrations (`pane.report_agent`, `pane.report_agent_session`). Everything else is logged and the connection is closed.

Point untrusted processes (e.g. sandboxed coding agents) at the guard socket instead of the real one. They can still report their state to herdr, but they can't control it.

> The guard only helps if those processes can't reach the real socket. Processes run as your user, so they can always connect to `~/.config/herdr/herdr.sock` directly. Hide that socket from them with a sandbox (see [Quick start](#quick-start)).

## Requirements

- Linux
- herdr 0.9.0 or newer
- Python 3.8+ (standard library only)
- `setsid` (util-linux)

## Quick start

1. Install the plugin from GitHub:

   ```bash
   herdr plugin install gsobczyk/herdr-socket-guard
   ```

   Remove it with `herdr plugin uninstall gsobczyk.socket-guard`. To update, uninstall and install again.

   For local development, link a clone instead:

   ```bash
   git clone https://github.com/gsobczyk/herdr-socket-guard.git
   herdr plugin link ./herdr-socket-guard
   ```

2. Restart the herdr server. The plugin starts on herdr startup, and installing or linking doesn't run startup hooks. The guard now listens on `$XDG_RUNTIME_DIR/socket-guard/$HERDR_SESSION/herdr.sock`.

3. Run the agent so it uses the guard socket and can't see the real one. For example, with bubblewrap:

   ```bash
   bwrap --dev-bind / / --unshare-pid --proc /proc \
         --tmpfs ~/.config/herdr \
         --setenv HERDR_SOCKET_PATH "$XDG_RUNTIME_DIR/socket-guard/$HERDR_SESSION/herdr.sock" \
         -- claude
   ```

   herdr injects `HERDR_SOCKET_PATH` into every pane and you can't override it from herdr config, so set it in the sandbox.

4. Check the log:

   ```bash
   tail -f <plugin state dir>/socket-guard.log
   ```

   ```
   2026-10-16 08:17:43,369 INFO allowed: {"id": "1", "method": "pane.report_agent_session", ...}
   2026-10-16 08:17:43,370 WARNING denied: {"id": "2", "method": "pane.send_keys", ...}
   ```

## How it works

- herdr runs the `[[startup]]` command once its API socket is ready (and again on live handoff). The command detaches `socket-guard.py` with `setsid`. If a guard is already listening on the target socket, the new instance exits.
- Requests are newline-delimited JSON. Each line is forwarded only if its `method` is allowed and `params.pane_id` is set. Responses from herdr are passed back unchanged.
- The guard connects to herdr only after the first allowed request, so denied requests never reach herdr.
- herdr doesn't supervise startup processes. If the guard dies, it comes back on the next herdr start.

## Configuration

### Allowed methods

Highest priority first:

1. `HERDR_ALLOWED_METHODS`: comma-separated list, e.g. `pane.report_agent,pane.report_agent_session`. An empty value denies everything, which is useful to find out which methods your integrations send.
2. `allowed_methods` file in the plugin config dir (`herdr plugin config-dir gsobczyk.socket-guard`): one method per line, `#` starts a comment.
3. Default: `pane.report_agent`, `pane.report_agent_session`.

See the [socket API docs](https://herdr.dev/docs/socket-api/) for all method names.

### Environment variables

The startup command inherits the herdr server's environment, so set these where you start herdr.

| Variable | Default | Description |
|---|---|---|
| `HERDR_ALLOWED_METHODS` | see above | Comma-separated method allowlist |
| `HERDR_REAL_SOCKET` | `$HERDR_SOCKET_PATH`, then `~/.config/herdr/herdr.sock` | herdr socket to forward to |
| `HERDR_FILTER_SOCKET` | derived from the real socket, see below | Socket the guard listens on |
| `HERDR_FILTER_LOG` | `$HERDR_PLUGIN_STATE_DIR/socket-guard.log`, stderr when run outside herdr | Log file |
| `HERDR_FILTER_LOG_LEVEL` | `INFO` | `INFO` logs every request, `WARNING` only denied ones |

### Socket path per session

Each herdr session gets its own guard socket in `$XDG_RUNTIME_DIR` (`/tmp` if unset):

| herdr socket | Guard socket |
|---|---|
| `~/.config/herdr/herdr.sock` | `socket-guard/herdr.sock` |
| `~/.config/herdr/sessions/<name>/herdr.sock` | `socket-guard/<name>/herdr.sock` |

The session name comes from `HERDR_SESSION`, or from the real socket path when it's unset. Inside a pane, `$XDG_RUNTIME_DIR/socket-guard/$HERDR_SESSION/herdr.sock` points at the guard of the current session. In the default session `HERDR_SESSION` is empty, and `socket-guard//herdr.sock` resolves to the same file.

The guard directories and socket are created with mode `0700` (owner only).

## Running without herdr

You can run the script by hand, e.g. to test an allowlist:

```bash
HERDR_ALLOWED_METHODS= ./socket-guard.py   # deny all, log to stderr
```

## License

MIT, see [LICENSE](LICENSE).
