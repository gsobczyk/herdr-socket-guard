#!/usr/bin/env python3
"""Filtering proxy in front of the herdr socket: forwards only allowlisted methods."""
import asyncio, json, logging, os, socket

ENV = os.environ
# As a herdr plugin, HERDR_SOCKET_PATH is the real socket of its session.
UPSTREAM = ENV.get("HERDR_REAL_SOCKET") or ENV.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock")


def filter_socket_for(upstream):
    """<runtime>/socket-guard/$HERDR_SESSION/herdr.sock, so panes can build it without ${:+};
    session from HERDR_SESSION, else from .../sessions/<name>/herdr.sock, default session -> ''"""
    session = ENV.get("HERDR_SESSION")
    if not session:
        session_dir = os.path.dirname(os.path.abspath(upstream))
        is_named = os.path.basename(os.path.dirname(session_dir)) == "sessions"
        session = os.path.basename(session_dir) if is_named else ""
    return os.path.join(ENV.get("XDG_RUNTIME_DIR", "/tmp"), "socket-guard", session, "herdr.sock")


LISTEN = ENV.get("HERDR_FILTER_SOCKET") or filter_socket_for(UPSTREAM)


def load_allowed():
    """HERDR_ALLOWED_METHODS (comma-separated) > $HERDR_PLUGIN_CONFIG_DIR/allowed_methods (one per line) > defaults."""
    if "HERDR_ALLOWED_METHODS" in ENV:
        return set(filter(None, ENV["HERDR_ALLOWED_METHODS"].split(",")))
    path = os.path.join(ENV.get("HERDR_PLUGIN_CONFIG_DIR", ""), "allowed_methods")
    if ENV.get("HERDR_PLUGIN_CONFIG_DIR") and os.path.exists(path):
        with open(path) as f:
            return {l.strip() for l in f if l.strip() and not l.startswith("#")}
    return {"pane.report_agent", "pane.report_agent_session"}


ALLOWED = load_allowed()
# HERDR_FILTER_LOG=file (default $HERDR_PLUGIN_STATE_DIR/socket-guard.log, stderr outside herdr),
# HERDR_FILTER_LOG_LEVEL=INFO (every request) / WARNING (denied only)
state_dir = ENV.get("HERDR_PLUGIN_STATE_DIR")
if state_dir:
    os.makedirs(state_dir, exist_ok=True)
logging.basicConfig(
    filename=ENV.get("HERDR_FILTER_LOG") or (state_dir and os.path.join(state_dir, "socket-guard.log")),
    level=ENV.get("HERDR_FILTER_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("socket-guard")


def allowed(line):
    try:
        req = json.loads(line)
    except ValueError:
        return False
    params = req.get("params") if isinstance(req, dict) else None
    return req.get("method") in ALLOWED and isinstance(params, dict) and bool(params.get("pane_id"))


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    finally:
        w.close()


async def handle(cr, cw):
    uw = back = None
    try:
        while line := await cr.readline():
            if not allowed(line):
                log.warning("denied: %s", line.decode(errors="replace").rstrip())
                break  # closing is enough, integration clients finish on "close"
            if uw is None:  # connect to herdr only after the first allowed request
                ur, uw = await asyncio.open_unix_connection(UPSTREAM)
                back = asyncio.create_task(pipe(ur, cw))
            log.info("allowed: %s", line.decode(errors="replace").rstrip())
            uw.write(line)
            await uw.drain()
        else:
            if uw:
                uw.write_eof()  # half-close, herdr's response still comes back
                await back
                return
    except Exception as e:
        log.error("error: %s", e)
    if uw:
        uw.close()
    cw.close()


def already_running():
    with socket.socket(socket.AF_UNIX) as s:
        try:
            s.connect(LISTEN)
            return True
        except OSError:
            return False


async def main():
    if os.path.abspath(LISTEN) == os.path.abspath(UPSTREAM):
        raise SystemExit(f"{LISTEN}: guard points at itself")
    # herdr reruns startup on live handoff, when the proxy is already running
    if already_running():
        log.info("%s already running", LISTEN)
        return
    os.umask(0o077)
    os.makedirs(os.path.dirname(LISTEN), exist_ok=True)
    server = await asyncio.start_unix_server(handle, LISTEN)
    log.info("%s -> %s, allowed: %s", LISTEN, UPSTREAM, sorted(ALLOWED))
    await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
