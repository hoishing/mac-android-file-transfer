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
go install github.com/ganeshrvel/go-mtpfs@latest
```

If your shell still cannot find it, add Go's bin directory to `PATH`:

```sh
export PATH="$(go env GOPATH)/bin:$PATH"
```

Run:

```sh
maft doctor
```

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
