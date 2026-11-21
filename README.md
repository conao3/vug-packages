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

A system builds into a disk image that boots in QEMU:

```console
$ vug system build-image --root . --name systemd --image systemd.img
```

## Check

```console
$ vug record check-tree --root .
```

## License

MIT. Records and patches translated from
[nixpkgs](https://github.com/NixOS/nixpkgs) keep its MIT license; see
`LICENSE`.
