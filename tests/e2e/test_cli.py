from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHONPATH = str(ROOT / "src")


def run_maft(
    tmp_path: Path,
    *args: str,
    path: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = PYTHONPATH
    env["MAFT_STATE_DIR"] = str(tmp_path / "state")
    env["MAFT_MACFUSE_PATHS"] = str(tmp_path / "macfuse.fs")
    if path is not None:
        env["PATH"] = path
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "maft.cli", *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_version_flag_prints_current_version(tmp_path: Path) -> None:
    result = run_maft(tmp_path, "--version")

    assert result.returncode == 0
    assert result.stdout == "maft 0.1.3\n"


def test_doctor_reports_missing_backend(tmp_path: Path) -> None:
    result = run_maft(
        tmp_path,
        "doctor",
        path="/usr/bin:/bin",
        extra_env={"GOPATH": str(tmp_path / "missing-go"), "HOME": str(tmp_path / "home")},
    )

    assert result.returncode == 1
    assert "go-mtpfs: missing" in result.stdout
    assert "macFUSE: missing" in result.stdout


def test_doctor_finds_go_installed_backend_outside_path(tmp_path: Path) -> None:
    go_bin = tmp_path / "go" / "bin"
    go_bin.mkdir(parents=True)
    make_executable(go_bin / "go-mtpfs", "#!/bin/sh\nexit 0\n")
    (tmp_path / "macfuse.fs").mkdir()

    result = run_maft(
        tmp_path,
        "doctor",
        path="/usr/bin:/bin:/usr/sbin:/sbin",
        extra_env={"GOPATH": str(tmp_path / "go"), "HOME": str(tmp_path / "home")},
    )

    assert result.returncode == 0
    assert "go-mtpfs: ok" in result.stdout


def test_install_backend_patches_and_installs_go_mtpfs(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "backend-install.log"
    make_executable(
        bin_dir / "git",
        """#!/bin/sh
last=""
for arg in "$@"; do last="$arg"; done
mkdir -p "$last"
cat > "$last/main.go" <<'GO'
package main

import "time"

func main() {
\tsec := time.Second
\tmountOpts := struct {
\t\tAttrTimeout  *time.Duration
\t\tEntryTimeout *time.Duration
\t}{
\t\tAttrTimeout:  &sec,
\t\tEntryTimeout: &sec,
\t}
\t_ = mountOpts
}
GO
""",
    )
    make_executable(
        bin_dir / "go",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> {log}
if [ "$1" = install ]; then
  grep 'zero := time.Duration(0)' main.go >/dev/null || exit 10
  grep 'AttrTimeout:  &zero' main.go >/dev/null || exit 11
  grep 'EntryTimeout: &zero' main.go >/dev/null || exit 12
fi
exit 0
""",
    )

    result = run_maft(
        tmp_path,
        "install-backend",
        path=f"{bin_dir}:/usr/bin:/bin",
        extra_env={"HOME": str(tmp_path / "home")},
    )

    assert result.returncode == 0, result.stderr
    assert "zero metadata cache TTLs" in result.stdout
    assert "get github.com/hanwen/go-fuse/v2@v2.10.1" in log.read_text(encoding="utf-8")


def test_completion_install_writes_bash_completion(tmp_path: Path) -> None:
    completion_dir = tmp_path / "completions"

    result = run_maft(tmp_path, "completion", "install", "bash", "--dir", str(completion_dir))

    assert result.returncode == 0, result.stderr
    target = completion_dir / "maft"
    content = target.read_text(encoding="utf-8")
    assert "complete -o default -F _maft_completion maft" in content
    assert "doctor install-backend mount unmount cp mv rm completion" in content
    assert "--mount --recursive -r --help" in content
    assert f"installed bash completion to {target}" in result.stdout


def test_completion_install_writes_zsh_completion(tmp_path: Path) -> None:
    completion_dir = tmp_path / "zfunc"

    result = run_maft(tmp_path, "completion", "install", "zsh", "--dir", str(completion_dir))

    assert result.returncode == 0, result.stderr
    target = completion_dir / "_maft"
    content = target.read_text(encoding="utf-8")
    assert "#compdef maft" in content
    assert "'install-backend:install the patched go-mtpfs backend'" in content
    assert "'--mount[mounted Android folder]:mountpoint:_files -/'" in content
    assert "'--force[overwrite an existing completion file]'" in content
    assert f"installed zsh completion to {target}" in result.stdout


def test_completion_install_refuses_existing_file_without_force(tmp_path: Path) -> None:
    completion_dir = tmp_path / "completions"
    completion_dir.mkdir()
    target = completion_dir / "maft"
    target.write_text("existing\n", encoding="utf-8")

    result = run_maft(tmp_path, "completion", "install", "bash", "--dir", str(completion_dir))

    assert result.returncode == 1
    assert "completion file already exists" in result.stderr
    assert "Pass --force to overwrite it" in result.stderr
    assert target.read_text(encoding="utf-8") == "existing\n"


def test_completion_install_force_overwrites_existing_file(tmp_path: Path) -> None:
    completion_dir = tmp_path / "completions"
    completion_dir.mkdir()
    target = completion_dir / "maft"
    target.write_text("existing\n", encoding="utf-8")

    result = run_maft(
        tmp_path,
        "completion",
        "install",
        "bash",
        "--dir",
        str(completion_dir),
        "--force",
    )

    assert result.returncode == 0, result.stderr
    assert "complete -o default -F _maft_completion maft" in target.read_text(encoding="utf-8")
    assert "existing" not in target.read_text(encoding="utf-8")


def test_mount_invokes_go_mtpfs_and_records_metadata(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "go-mtpfs.log"
    make_executable(
        bin_dir / "go-mtpfs",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\nwhile true; do sleep 1; done\n",
    )
    (tmp_path / "macfuse.fs").mkdir()
    mountpoint = tmp_path / "Android"

    result = run_maft(
        tmp_path,
        "mount",
        str(mountpoint),
        "--dev",
        "1",
        "--storage",
        "2",
        "--allow-other",
        "--usb-timeout",
        "10",
        path=f"{bin_dir}:/usr/bin:/bin",
    )

    assert result.returncode == 0, result.stderr
    assert mountpoint.is_dir()
    wait_for_path(log)
    invocation = log.read_text(encoding="utf-8")
    assert "-dev\n1\n-storage\n2\n-allow-other\n-usb-timeout\n10" in invocation
    state = json.loads((tmp_path / "state" / "mounts.json").read_text(encoding="utf-8"))
    assert str(mountpoint.resolve()) in state
    os.kill(state[str(mountpoint.resolve())]["pid"], signal.SIGTERM)


def test_mount_reports_backend_startup_failure(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    make_executable(
        bin_dir / "go-mtpfs",
        "#!/bin/sh\nprintf 'backend failed\\n' >&2\nexit 1\n",
    )
    (tmp_path / "macfuse.fs").mkdir()

    result = run_maft(
        tmp_path,
        "mount",
        str(tmp_path / "Android"),
        path=f"{bin_dir}:/usr/bin:/bin",
    )

    assert result.returncode == 1
    assert "go-mtpfs exited before mounting with status 1: backend failed" in result.stderr
    assert not (tmp_path / "state" / "mounts.json").exists()


def test_unmount_uses_available_command_and_cleans_metadata(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "umount.log"
    make_executable(bin_dir / "umount", f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\nexit 0\n")
    mountpoint = tmp_path / "Android"
    mountpoint.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "mounts.json").write_text(
        json.dumps(
            {
                str(mountpoint.resolve()): {
                    "mountpoint": str(mountpoint.resolve()),
                    "pid": 123,
                    "command": ["go-mtpfs", str(mountpoint.resolve())],
                    "created_at": 1.0,
                }
            }
        ),
        encoding="utf-8",
    )

    result = run_maft(tmp_path, "unmount", str(mountpoint), path=f"{bin_dir}:/usr/bin:/bin")

    assert result.returncode == 0, result.stderr
    assert str(mountpoint) in log.read_text(encoding="utf-8")
    state = json.loads((state_dir / "mounts.json").read_text(encoding="utf-8"))
    assert state == {}


def test_cp_mv_rm_file_operations_against_mount_folder(tmp_path: Path) -> None:
    mountpoint = tmp_path / "Android"
    host = tmp_path / "host"
    mountpoint.mkdir()
    host.mkdir()
    (host / "photo.jpg").write_text("photo", encoding="utf-8")

    copy_to_device = run_maft(
        tmp_path,
        "cp",
        "--mount",
        str(mountpoint),
        str(host / "photo.jpg"),
        "DCIM/photo.jpg",
    )
    assert copy_to_device.returncode == 0, copy_to_device.stderr
    assert (mountpoint / "DCIM" / "photo.jpg").read_text(encoding="utf-8") == "photo"

    move_on_device = run_maft(
        tmp_path,
        "mv",
        "--mount",
        str(mountpoint),
        "DCIM/photo.jpg",
        "Pictures/photo.jpg",
    )
    assert move_on_device.returncode == 0, move_on_device.stderr
    assert not (mountpoint / "DCIM" / "photo.jpg").exists()
    assert (mountpoint / "Pictures" / "photo.jpg").exists()

    copy_to_host = run_maft(
        tmp_path,
        "cp",
        "--mount",
        str(mountpoint),
        "Pictures/photo.jpg",
        str(host / "copy.jpg"),
    )
    assert copy_to_host.returncode == 0, copy_to_host.stderr
    assert (host / "copy.jpg").read_text(encoding="utf-8") == "photo"

    remove_file = run_maft(tmp_path, "rm", "--mount", str(mountpoint), "Pictures/photo.jpg")
    assert remove_file.returncode == 0, remove_file.stderr
    assert not (mountpoint / "Pictures" / "photo.jpg").exists()


def test_recursive_operations_and_path_safety(tmp_path: Path) -> None:
    mountpoint = tmp_path / "Android"
    host = tmp_path / "host"
    mountpoint.mkdir()
    host.mkdir()
    (host / "album").mkdir()
    (host / "album" / "a.txt").write_text("a", encoding="utf-8")

    refused_directory_copy = run_maft(
        tmp_path,
        "cp",
        "--mount",
        str(mountpoint),
        str(host / "album"),
        "Albums/album",
    )
    assert refused_directory_copy.returncode == 1
    assert "pass --recursive" in refused_directory_copy.stderr

    copied_directory = run_maft(
        tmp_path,
        "cp",
        "--mount",
        str(mountpoint),
        "--recursive",
        str(host / "album"),
        "Albums/album",
    )
    assert copied_directory.returncode == 0, copied_directory.stderr
    assert (mountpoint / "Albums" / "album" / "a.txt").exists()

    refused_escape = run_maft(tmp_path, "rm", "--mount", str(mountpoint), "../outside")
    assert refused_escape.returncode == 1
    assert "escapes mountpoint" in refused_escape.stderr

    refused_directory_remove = run_maft(tmp_path, "rm", "--mount", str(mountpoint), "Albums")
    assert refused_directory_remove.returncode == 1
    assert "pass --recursive" in refused_directory_remove.stderr

    removed_directory = run_maft(
        tmp_path,
        "rm",
        "--mount",
        str(mountpoint),
        "--recursive",
        "Albums",
    )
    assert removed_directory.returncode == 0, removed_directory.stderr
    assert not (mountpoint / "Albums").exists()


def wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.01)
    raise AssertionError(f"path was not created: {path}")
