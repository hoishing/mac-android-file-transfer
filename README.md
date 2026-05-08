# mac-android-file-transfer

`maft` is a macOS CLI wrapper around
[`github.com/ganeshrvel/go-mtpfs`](https://github.com/ganeshrvel/go-mtpfs).
It mounts Android MTP storage to a local folder, then provides explicit `cp`,
`mv`, and `rm` commands over that mounted filesystem.

## Requirements

- macOS
- macFUSE
- `go-mtpfs` available in `PATH`, `GOBIN`, or `GOPATH/bin`
- `diskutil` or `/sbin/umount` for unmounting

Install the backend with:

```sh
brew install go libusb pkg-config
maft install-backend
```

`maft install-backend` builds the supported `go-mtpfs` backend with macFUSE
compatibility updates and zero metadata cache TTLs. If a device is already
mounted, unmount it and mount it again so the running backend process uses the
newly installed binary.

If your shell still cannot find it, add Go's bin directory to `PATH`:

```sh
export PATH="$(go env GOPATH)/bin:$PATH"
```

Run:

```sh
maft doctor
```

## Shell completion

Install bash completion:

```sh
maft completion install bash
```

This writes `maft` to `~/.local/share/bash-completion/completions/maft`. If your
bash setup does not load that directory automatically, source the installed file
from `~/.bashrc`:

```sh
source ~/.local/share/bash-completion/completions/maft
```

Install zsh completion:

```sh
maft completion install zsh
```

This writes `_maft` to `/opt/homebrew/share/zsh/site-functions/_maft`. If that
directory is not already in your zsh completion path, add this before `compinit`
in `~/.zshrc`:

```sh
fpath=(/opt/homebrew/share/zsh/site-functions $fpath)
autoload -Uz compinit
compinit
```

Use `--dir` to install into a custom completion directory and `--force` to
overwrite an existing completion file.

## Usage

```sh
maft mount ~/Android
maft cp --mount ~/Android ~/Downloads/photo.jpg DCIM/photo.jpg
maft cp --mount ~/Android -r DCIM ~/Desktop/DCIM
maft mv --mount ~/Android DCIM/photo.jpg Pictures/photo.jpg
maft rm --mount ~/Android Pictures/photo.jpg
maft rm --mount ~/Android -r Pictures/old-folder
maft unmount ~/Android
```

For file commands, absolute paths and `~/...` paths are treated as host paths.
Other paths are treated as Android paths relative to the mount folder.
