# vug-packages

Package records for [vug](https://github.com/conao3/rust-vug).

A package is a directory of TOML records that carry data only: where the
source comes from, its SHA-256, the build kind, and the dependencies. The
format is specified in
[docs/record.md](https://github.com/conao3/rust-vug/blob/master/docs/record.md).

```text
seed.toml
packages/
  he/hello/
    package.toml
    versions/
      2.12.2.toml
services/
  dhcpcd.toml
systems/
  systemd.toml
```

- `seed.toml` names the upstream binaries every build starts from.
- Each version of a package is one file, so two changes that add different
  versions never touch the same file.
- A system record names a kernel, packages, files below `/etc`, users, and
  service records; a service record is a part several systems share. Both
  are specified in
  [docs/system.md](https://github.com/conao3/rust-vug/blob/master/docs/system.md).

## Build

```console
$ vug package build-package --root . --name hello
```

The first build fetches the seed (busybox and zig) and builds the tool chain
from it; every later build takes what is already in the store.

The tree holds the base system (musl, the GNU tools, systemd, PAM, OpenSSH),
the tools to build it (perl, python, cmake, meson, LLVM with clang, Rust),
`vug` itself built from source, and a Wayland desktop (Mesa, wlroots, sway).

## Try a system

`systems/systemd.toml` is a system with systemd, logins through PAM, sshd, a
DHCP client, time synchronization, and everyday tools. Build a disk image and
start it in QEMU with the kernel of the system:

```console
$ vug system build-image --root . --name systemd --image systemd.img --size 4g
$ qemu-system-x86_64 -enable-kvm -m 2048 -nographic \
    -kernel <Kernel of the output above> \
    -drive file=systemd.img,if=virtio,format=raw \
    -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22 \
    -append "console=ttyS0 root=/dev/vda rw init=/vug/profiles/system/init"
```

The systems here are test configurations: the console logs in as the user
of the record without a password, and that user may run `sudo` without one.
Change the users, the `serial-autologin` service, and the `sudo` parameter
before using one on a machine others can reach. The records are on the
machine at `/vug/tree`; after an edit,

```console
# vug system switch-system --root /vug/tree --name systemd --binary-cache <URL>
```

builds the new system and switches to it, and
`/vug/profiles/system-<N>/bin/switch-to-system` goes back to generation `N`.
A binary cache is a directory written by `vug store export-cache` on the
machine that built the packages, served over HTTP.

`systems/generic.toml` is the same system with a kernel for real hardware
and a boot loader. Its image is a GPT disk that EFI firmware boots, in QEMU
with OVMF or written to a disk:

```console
$ vug system build-image --root . --name generic --image generic.img --size 8g
$ qemu-system-x86_64 -enable-kvm -m 2048 \
    -drive if=pflash,format=raw,readonly=on,file=OVMF_CODE.fd \
    -drive if=pflash,format=raw,file=OVMF_VARS.fd \
    -drive file=generic.img,if=virtio,format=raw
```

The boot menu lists the generations of the machine.

`systems/minimal.toml` is the smallest system that boots: a kernel, a shell
script as init, and the basic tools.

## Desktop

`services/sway.toml` adds a Wayland session to a system: the sway compositor
with a status bar, the foot terminal, a launcher, a screen lock that also
locks after ten idle minutes, screenshots, and the clipboard tools. Enable
it in a system record:

```toml
[service.sway]
user = "<a user of the record>"
```

The session draws on the CPU. `renderer = "gles2"` uses the GPU through
Mesa, which needs a display the Mesa drivers support (in QEMU,
`-device virtio-vga-gl -display egl-headless` or a display with OpenGL).

## Check

```console
$ vug record check-tree --root .
```

## License

MIT. Records and patches translated from
[nixpkgs](https://github.com/NixOS/nixpkgs) keep its MIT license; see
`LICENSE`.
