"""Entry point for the packaged binary - PyInstaller needs a script outside the
mcpersist package so its relative imports resolve correctly."""

from mcpersist.tray_app import main

if __name__ == "__main__":
    main()
