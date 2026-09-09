"""Entry point for the packaged binary - PyInstaller needs a script outside the
mcpersist package so its relative imports resolve correctly.

Also doubles as the entry point for the tunnel client subprocess: since a frozen
build's sys.executable is this same .exe (there's no separate python.exe bundled),
tunnel_relay.py re-invokes it with a sentinel flag instead of "-m", and this checks
for that flag before falling through to the normal GUI."""

import sys


def main():
    if "--tunnel-relay-run" in sys.argv:
        import asyncio

        from mcpersist.tunnel_relay_run import main as tunnel_main

        asyncio.run(tunnel_main())
        return

    from mcpersist.tray_app import main as tray_main

    tray_main()


if __name__ == "__main__":
    main()
