"""Windows smoke test, run by .github/workflows/windows-smoke.yml on a real Windows
runner: the parts that can't be exercised on Linux (window flags, schtasks, msvcrt
locks, the Windows certificate store, the PowerShell updater) plus a real server
start/stop."""

import os
import subprocess
import sys
import tempfile
import time
import types
import zipfile
from pathlib import Path

import psutil

from mcpersist import (
    actions,
    autostart,
    config,
    java_manager,
    javacheck,
    net,
    process_manager,
    setup_flow,
    update_checker,
    world,
)

RELEASE_ZIP = "https://github.com/5TN1rcZRS79VAEFuUCRB/MCPersist/releases/download/v0.1.75/MCPersist-windows.zip"


def step(name):
    print(f"\n=== {name}", flush=True)


def wait_for(cond, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {what}")


step("HTTPS over urllib with the Windows certificate store")
release = net.get_json(update_checker.LATEST_RELEASE_API)
print("latest release:", release["tag_name"])
part = net.download_to_part(RELEASE_ZIP, Path(tempfile.mkdtemp()) / "rel.zip")
assert "MCPersist.exe" in zipfile.ZipFile(part).namelist()

step("Java download, extraction and version check")
java = java_manager.ensure_java(21)
print("java:", java)
assert javacheck.detected_major_version(java) == 21

step("Detached launch with Windows flags, short TEMP, pid files")
tmp = Path(tempfile.mkdtemp())
pid = process_manager.launch_detached(
    [sys.executable, "-c", "import os, time; print(os.environ['TEMP'], flush=True); time.sleep(60)"],
    cwd=tmp,
    log_path=tmp / "child.log",
    short_tmp=True,
)
process_manager.write_pid(tmp / "child.pid", pid)
assert process_manager.read_pid(tmp / "child.pid") == pid
assert process_manager.is_running(pid)
wait_for(lambda: (tmp / "child.log").read_text().strip(), 20, "child output")
assert "mctmp" in (tmp / "child.log").read_text(), (tmp / "child.log").read_text()
process_manager.stop_pid(pid)
assert not process_manager.is_running(pid)
assert process_manager.read_pid(tmp / "child.pid") is None

step("msvcrt world lock detection")
save = tmp / "save"
save.mkdir()
assert not world.is_world_open(save)
with open(save / "session.lock", "wb") as f:
    f.write(b"x")
fd = os.open(save / "session.lock", os.O_RDWR)
import msvcrt  # noqa: E402

msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
assert world.is_world_open(save)
msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
os.close(fd)
assert not world.is_world_open(save)

step("Autostart via schtasks")
ok, out = autostart.install()
print(out)
assert ok and autostart.status()[0]
ok, out = autostart.remove()
print(out)
assert ok and not autostart.status()[0]

step("Real server: setup, start, RCON, stop")
owner = setup_flow.prepare_new_world("", "SmokeWorld", "Notch")
print("\n".join(owner.lines))
assert owner.ok and owner.data["owner_uuid"]
finish = setup_flow.finish_setup(
    "",
    "SmokeWorld",
    "1.21.1",
    "vanilla",
    owner.data["owner_uuid"],
    owner.data["owner_name"],
    copy_mods=False,
    world_options=setup_flow.world_options("survival", "easy", "minecraft:flat", False, 0, "smoke"),
)
print("\n".join(finish.lines))
assert finish.ok
cfg = config.load()
server_dir = config.server_dir(cfg)
started = actions.start_server(cfg, server_dir)
print("\n".join(started.lines))
assert started.ok
log = server_dir / "logs" / "server.out.log"
wait_for(lambda: "Done (" in log.read_text(errors="replace"), 240, "server Done")
added = actions.add_to_whitelist(cfg, server_dir, "jeb_")
print(added)
assert added.ok and "jeb_" in added.lines[0]
assert actions.get_status(cfg, server_dir)["server_running"]
stopped = actions.stop_server(cfg, server_dir)
print(stopped)
assert stopped.ok and not actions.get_status(cfg, server_dir)["server_running"]
assert "Stopping server" in log.read_text(errors="replace")

step("Tunnel: detached launch, failure reporting, stop")
cfg.update(relay_host="127.0.0.1", relay_control_port=9)
config.save(cfg)
assert actions.start_tunnel(cfg, server_dir).ok
wait_for(lambda: actions.get_status(cfg, server_dir)["tunnel_problem"], 60, "tunnel error in log")
print("tunnel problem:", actions.get_status(cfg, server_dir)["tunnel_problem"])
assert actions.stop_tunnel(server_dir).ok
assert not actions.get_status(cfg, server_dir)["tunnel_running"]

step("GUI main window, offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from mcpersist.tray_app import MainWindow  # noqa: E402

app = QApplication([])
window = MainWindow()
window.show()
deadline = time.time() + 30
while window._pending_workers() and time.time() < deadline:
    app.processEvents()
    time.sleep(0.05)
window.status_page.refresh()
print("GUI world label:", window.status_page.world_label.text())
assert "SmokeWorld" in window.status_page.world_label.text()
window.close()

step("Self-update: real release zip, real PowerShell updater")
install = Path(tempfile.mkdtemp()) / "install"
(install / "_internal").mkdir(parents=True)
(install / "MCPersist.exe").write_text("old")
(install / "_internal" / "marker.txt").write_text("kept")


def run_update():
    # apply_update hands the updater our own pid and waits for it to exit; hand it
    # a short-lived stand-in instead, since this process has more to check.
    stand_in = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8)"])
    update_checker.os = types.SimpleNamespace(**{**vars(os), "getpid": lambda: stand_in.pid})
    update_checker.BASE_DIR = install
    sys.frozen = True
    try:
        update_checker.apply_update(RELEASE_ZIP)
    finally:
        del sys.frozen
    return stand_in


def relaunched():
    for p in psutil.process_iter(["exe"]):
        if p.info["exe"] and Path(p.info["exe"]).parent == install:
            return p
    return None


run_update()
wait_for(lambda: relaunched() is not None, 120, "updated MCPersist.exe relaunched")
assert (install / "MCPersist.exe").stat().st_size > 1000
assert (install / "_internal" / "marker.txt").read_text() == "kept"
assert not update_checker.UPDATE_LOG_PATH.exists()
print("updated and relaunched:", relaunched().exe())

step("Self-update refused while MCPersist.exe is in use")
run_update()
wait_for(lambda: "FAILED" in (update_checker.UPDATE_LOG_PATH.read_text() if update_checker.UPDATE_LOG_PATH.exists() else ""), 120, "updater failure log")
failure = update_checker.check_last_update_failure()
print(failure)
assert "stayed in use" in failure
relaunched().kill()

print("\nALL WINDOWS SMOKE CHECKS PASSED")
