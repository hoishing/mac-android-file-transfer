from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO, cast

from maft import __version__

APP_NAME = "maft"
BACKEND_REPOSITORY = "https://github.com/ganeshrvel/go-mtpfs.git"
BACKEND_VERSION = "v1.0.3"
BACKEND_GO_FUSE_VERSION = "v2.10.1"
DEFAULT_STATE_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
DEFAULT_MACFUSE_PATHS = (
    Path("/Library/Filesystems/macfuse.fs"),
    Path("/Library/Filesystems/osxfuse.fs"),
)


@dataclass(frozen=True)
class MountRecord:
    mountpoint: str
    pid: int
    command: list[str]
    created_at: float


class CliError(Exception):
    pass


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"maft: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maft",
        description="Mount and manage Android MTP files on macOS via go-mtpfs.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="check local dependencies")
    doctor.set_defaults(func=cmd_doctor)

    install_backend = subparsers.add_parser(
        "install-backend",
        help="install the patched go-mtpfs backend for current macFUSE and fresh listings",
    )
    install_backend.set_defaults(func=cmd_install_backend)

    mount = subparsers.add_parser("mount", help="mount an Android device")
    mount.add_argument("mountpoint")
    mount.add_argument("--dev", help="go-mtpfs device selector")
    mount.add_argument("--storage", help="go-mtpfs storage selector")
    mount.add_argument("--android", dest="android", action="store_true", default=True)
    mount.add_argument("--no-android", dest="android", action="store_false")
    mount.add_argument("--allow-other", action="store_true")
    mount.add_argument(
        "--debug",
        nargs="?",
        const="usb,mtp,fuse",
        metavar="OPTIONS",
        help="enable go-mtpfs debug output, optionally comma-separated: usb,data,mtp,fuse",
    )
    mount.add_argument("--usb-timeout", type=int, help="go-mtpfs USB timeout")
    mount.set_defaults(func=cmd_mount)

    unmount = subparsers.add_parser("unmount", help="unmount an Android mount folder")
    unmount.add_argument("mountpoint")
    unmount.set_defaults(func=cmd_unmount)

    cp = subparsers.add_parser("cp", help="copy files to, from, or within a mounted device")
    add_mount_arg(cp)
    cp.add_argument("-r", "--recursive", action="store_true", help="copy directories recursively")
    cp.add_argument("src")
    cp.add_argument("dst")
    cp.set_defaults(func=cmd_cp)

    mv = subparsers.add_parser("mv", help="move files to, from, or within a mounted device")
    add_mount_arg(mv)
    mv.add_argument("src")
    mv.add_argument("dst")
    mv.set_defaults(func=cmd_mv)

    rm = subparsers.add_parser("rm", help="remove files from a mounted device")
    add_mount_arg(rm)
    rm.add_argument("-r", "--recursive", action="store_true", help="remove directories recursively")
    rm.add_argument("paths", nargs="+")
    rm.set_defaults(func=cmd_rm)

    return parser


def add_mount_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mount",
        required=True,
        dest="mountpoint",
        help="mounted Android folder",
    )


def cmd_doctor(_args: argparse.Namespace) -> int:
    go_mtpfs = find_go_mtpfs()
    checks = [
        ("go-mtpfs", go_mtpfs is not None, install_go_mtpfs_help()),
        ("diskutil", shutil.which("diskutil") is not None, "Provided by macOS."),
        ("/sbin/umount", Path("/sbin/umount").exists(), "Provided by macOS."),
        ("macFUSE", macfuse_available(), "Install from https://macfuse.github.io/."),
    ]

    failed = False
    for name, ok, help_text in checks:
        status = "ok" if ok else "missing"
        print(f"{name}: {status}")
        if not ok:
            failed = True
            print(f"  {help_text}")
    return 1 if failed else 0


def cmd_install_backend(_args: argparse.Namespace) -> int:
    go = shutil.which("go")
    if go is None:
        raise CliError("go not found in PATH. Install it with: brew install go")
    git = shutil.which("git")
    if git is None:
        raise CliError("git not found in PATH.")

    with tempfile.TemporaryDirectory(prefix="maft-go-mtpfs-") as directory:
        checkout = Path(directory) / "go-mtpfs"
        run_checked(
            [
                git,
                "clone",
                "--depth",
                "1",
                "--branch",
                BACKEND_VERSION,
                BACKEND_REPOSITORY,
                str(checkout),
            ]
        )
        run_checked(
            [go, "get", f"github.com/hanwen/go-fuse/v2@{BACKEND_GO_FUSE_VERSION}"],
            cwd=checkout,
        )
        patch_backend_cache_timeouts(checkout / "main.go")
        run_checked([go, "mod", "tidy"], cwd=checkout)
        run_checked([go, "install", "."], cwd=checkout)

    print(
        "installed go-mtpfs backend "
        f"{BACKEND_VERSION} with go-fuse {BACKEND_GO_FUSE_VERSION} and zero metadata cache TTLs"
    )
    return 0


def cmd_mount(args: argparse.Namespace) -> int:
    go_mtpfs = find_go_mtpfs()
    if go_mtpfs is None:
        raise CliError(f"go-mtpfs not found in PATH. {install_go_mtpfs_help()}")
    if not macfuse_available():
        raise CliError("macFUSE was not detected. Install it from https://macfuse.github.io/.")

    mountpoint = expand_path(args.mountpoint)
    if mountpoint.exists() and not mountpoint.is_dir():
        raise CliError(f"mountpoint exists and is not a directory: {mountpoint}")
    mountpoint.mkdir(parents=True, exist_ok=True)

    command = [go_mtpfs]
    if args.dev:
        command.extend(["-dev", args.dev])
    if args.storage:
        command.extend(["-storage", args.storage])
    if not args.android:
        command.append("-android=false")
    if args.allow_other:
        command.append("-allow-other")
    if args.debug:
        command.extend(["-debug", args.debug])
    if args.usb_timeout is not None:
        command.extend(["-usb-timeout", str(args.usb_timeout)])
    command.append(str(mountpoint))

    if args.debug:
        process = subprocess.Popen(command, start_new_session=True)
        ensure_backend_started(process)
    else:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as output:
            process = subprocess.Popen(
                command,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            ensure_backend_started(process, output)
    write_mount_record(
        MountRecord(
            mountpoint=str(mountpoint),
            pid=process.pid,
            command=command,
            created_at=time.time(),
        )
    )
    print(f"mounted {mountpoint} with go-mtpfs pid {process.pid}")
    return 0


def ensure_backend_started(
    process: subprocess.Popen[bytes],
    output: TextIO | None = None,
) -> None:
    try:
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        return

    message = f"go-mtpfs exited before mounting with status {returncode}"
    if output is not None:
        output.seek(0)
        details = output.read().strip()
        if details:
            message = f"{message}: {details}"
    raise CliError(message)


def patch_backend_cache_timeouts(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    replacements = {
        "sec := time.Second\n\tmountOpts :=": "zero := time.Duration(0)\n\tmountOpts :=",
        "AttrTimeout:  &sec,": "AttrTimeout:  &zero,",
        "EntryTimeout: &sec,": "EntryTimeout: &zero,",
    }
    for old, new in replacements.items():
        if old not in source:
            raise CliError(f"could not patch go-mtpfs cache timeout source: missing {old!r}")
        source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")


def run_checked(command: list[str], cwd: Path | None = None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    output = result.stderr.strip() or result.stdout.strip()
    detail = f": {output}" if output else ""
    raise CliError(f"{Path(command[0]).name} failed{detail}")


def cmd_unmount(args: argparse.Namespace) -> int:
    mountpoint = require_dir(expand_path(args.mountpoint), "mountpoint")
    errors: list[str] = []
    diskutil = shutil.which("diskutil")
    if diskutil is not None:
        result = subprocess.run(
            [diskutil, "unmount", str(mountpoint)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            remove_mount_record(mountpoint)
            print(f"unmounted {mountpoint}")
            return 0
        errors.append(result.stderr.strip() or result.stdout.strip() or "diskutil unmount failed")

    umount = shutil.which("umount") or "/sbin/umount"
    result = subprocess.run(
        [umount, str(mountpoint)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        remove_mount_record(mountpoint)
        print(f"unmounted {mountpoint}")
        return 0
    errors.append(result.stderr.strip() or result.stdout.strip() or "umount failed")

    joined = "; ".join(error for error in errors if error)
    raise CliError(f"could not unmount {mountpoint}: {joined}")


def cmd_cp(args: argparse.Namespace) -> int:
    mountpoint = require_mountpoint(args.mountpoint)
    src = resolve_operation_path(mountpoint, args.src)
    dst = resolve_operation_path(mountpoint, args.dst)

    if src.path.is_dir():
        if not args.recursive:
            raise CliError(f"{src.original} is a directory; pass --recursive")
        copy_directory(src.path, destination_for_copy(dst.path, src.path.name))
    else:
        copy_file(src.path, destination_for_copy(dst.path, src.path.name))
    return 0


def cmd_mv(args: argparse.Namespace) -> int:
    mountpoint = require_mountpoint(args.mountpoint)
    src = resolve_operation_path(mountpoint, args.src)
    dst = resolve_operation_path(mountpoint, args.dst)
    target = destination_for_copy(dst.path, src.path.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src.path), str(target))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    mountpoint = require_mountpoint(args.mountpoint)
    for raw_path in args.paths:
        if is_host_path(raw_path):
            raise CliError(f"rm only accepts Android-relative paths, got host path: {raw_path}")
        target = resolve_android_path(mountpoint, raw_path)
        if target.is_dir():
            if not args.recursive:
                raise CliError(f"{raw_path} is a directory; pass --recursive")
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        else:
            raise CliError(f"path does not exist: {raw_path}")
    return 0


@dataclass(frozen=True)
class OperationPath:
    original: str
    path: Path
    is_host: bool


def resolve_operation_path(mountpoint: Path, value: str) -> OperationPath:
    if is_host_path(value):
        return OperationPath(value, expand_path(value), True)
    return OperationPath(value, resolve_android_path(mountpoint, value), False)


def resolve_android_path(mountpoint: Path, value: str) -> Path:
    if value in {"", "."}:
        return mountpoint
    candidate = (mountpoint / value).resolve(strict=False)
    try:
        candidate.relative_to(mountpoint)
    except ValueError as exc:
        raise CliError(f"Android path escapes mountpoint: {value}") from exc
    return candidate


def is_host_path(value: str) -> bool:
    return value.startswith("/") or value.startswith("~/") or value == "~"


def destination_for_copy(destination: Path, source_name: str) -> Path:
    if destination.exists() and destination.is_dir():
        return destination / source_name
    if str(destination).endswith(os.sep):
        destination.mkdir(parents=True, exist_ok=True)
        return destination / source_name
    return destination


def copy_file(src: Path, dst: Path) -> None:
    if not src.is_file():
        raise CliError(f"source file does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def copy_directory(src: Path, dst: Path) -> None:
    if not src.is_dir():
        raise CliError(f"source directory does not exist: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)


def require_mountpoint(value: str) -> Path:
    mountpoint = require_dir(expand_path(value), "mountpoint")
    return mountpoint.resolve(strict=True)


def require_dir(path: Path, label: str) -> Path:
    if not path.exists():
        raise CliError(f"{label} does not exist: {path}")
    if not path.is_dir():
        raise CliError(f"{label} is not a directory: {path}")
    return path


def expand_path(value: str) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def state_dir() -> Path:
    override = os.environ.get("MAFT_STATE_DIR")
    return Path(override).expanduser() if override else DEFAULT_STATE_DIR


def state_file() -> Path:
    return state_dir() / "mounts.json"


def load_mount_records() -> dict[str, MountRecord]:
    path = state_file()
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CliError(f"invalid state file: {path}")
    raw_records = cast("dict[object, object]", data)
    records: dict[str, MountRecord] = {}
    for mountpoint, record in raw_records.items():
        if not isinstance(mountpoint, str) or not isinstance(record, dict):
            continue
        raw_record = cast("dict[object, object]", record)
        raw_command = raw_record.get("command")
        raw_mountpoint = raw_record.get("mountpoint")
        raw_pid = raw_record.get("pid")
        raw_created_at = raw_record.get("created_at")
        if (
            not isinstance(raw_mountpoint, str)
            or not isinstance(raw_pid, int)
            or not isinstance(raw_created_at, int | float)
            or not isinstance(raw_command, list)
        ):
            continue
        command = cast("list[object]", raw_command)
        records[mountpoint] = MountRecord(
            mountpoint=raw_mountpoint,
            pid=raw_pid,
            command=[str(part) for part in command],
            created_at=float(raw_created_at),
        )
    return records


def save_mount_records(records: dict[str, MountRecord]) -> None:
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    state_file().write_text(
        json.dumps({key: asdict(value) for key, value in records.items()}, indent=2) + "\n",
        encoding="utf-8",
    )


def write_mount_record(record: MountRecord) -> None:
    records = load_mount_records()
    records[record.mountpoint] = record
    save_mount_records(records)


def remove_mount_record(mountpoint: Path) -> None:
    records = load_mount_records()
    records.pop(str(mountpoint.resolve(strict=False)), None)
    save_mount_records(records)


def macfuse_available() -> bool:
    override = os.environ.get("MAFT_MACFUSE_PATHS")
    paths = [Path(path).expanduser() for path in override.split(os.pathsep)] if override else list(
        DEFAULT_MACFUSE_PATHS
    )
    return any(path.exists() for path in paths)


def find_go_mtpfs() -> str | None:
    executable = shutil.which("go-mtpfs")
    if executable is not None:
        return executable

    for path in go_binary_dirs():
        candidate = path / "go-mtpfs"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def go_binary_dirs() -> list[Path]:
    env_paths: list[Path] = []
    gobin = os.environ.get("GOBIN")
    if gobin:
        env_paths.append(Path(gobin).expanduser())

    gopath = os.environ.get("GOPATH")
    if gopath:
        env_paths.extend(
            Path(path).expanduser() / "bin" for path in gopath.split(os.pathsep) if path
        )
    else:
        env_paths.append(Path.home() / "go" / "bin")

    go = shutil.which("go")
    if go is not None:
        result = subprocess.run(
            [go, "env", "GOBIN", "GOPATH"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            if len(lines) >= 1 and lines[0]:
                env_paths.append(Path(lines[0]).expanduser())
            if len(lines) >= 2 and lines[1]:
                env_paths.extend(
                    Path(path).expanduser() / "bin" for path in lines[1].split(os.pathsep) if path
                )

    return list(dict.fromkeys(env_paths))


def install_go_mtpfs_help() -> str:
    return (
        "Install Go and go-mtpfs, for example: "
        "brew install go libusb pkg-config && go install github.com/ganeshrvel/go-mtpfs@latest. "
        "If go-mtpfs is already installed, add $(go env GOPATH)/bin to PATH."
    )

if __name__ == "__main__":
    raise SystemExit(main())
