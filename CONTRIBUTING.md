# Adding a package

A change adds or updates exactly one package: the files under
`packages/<shard>/<name>/` and nothing else. `<shard>` is the first two
characters of the name. The record format is specified in
[docs/record.md](https://github.com/conao3/rust-vug/blob/master/docs/record.md)
and the build in
[docs/build.md](https://github.com/conao3/rust-vug/blob/master/docs/build.md).
`packages/he/hello/` is the smallest example.

## Tools

`vug` comes from the flake of `conao3/rust-vug`; it carries its own sandbox
tool. With Nix installed (flakes enabled):

```console
$ nix build github:conao3/rust-vug -o vug
$ ./vug/bin/vug --help
```

## Steps

1. Write `package.toml` and `versions/<version>.toml`. Start with
   `kind = "autotools"`, `[dependencies] build = ["binutils", "make"]`,
   `run = ["musl"]` plus the libraries the package links against, and
   `[check] enabled = true`, `skip = []`. A library the package links
   against goes in `run`; a tool that only runs during the build goes in
   `build`. Only packages that exist under `packages/` can be listed.
2. Make both files canonical and check the tree:

   ```console
   $ ./vug/bin/vug record format-record --file <file> --write
   $ ./vug/bin/vug record check-tree --root .
   ```

3. Build, keeping the log:

   ```console
   $ ./vug/bin/vug package build-package --root . --name <name> --jobs 4 > build.json 2> build.log
   ```

4. Run the main program of the output:

   ```console
   $ ./vug/bin/vug package run-package --root . --name <name> --program bin/<program> --arguments --version
   ```

## What a build may use

The build has no network and sees only busybox, the C and C++ compiler
(`cc`, `c++`), and the packages listed as dependencies. Headers and libraries
of the dependencies are found by the compiler automatically. `{out}` and
`{dep:NAME}` in a flag are replaced by store paths.

A package with C++ code lists `libcxx` in `dependencies.run`: the compiler
then links the shared C++ runtime in place of a private static copy, and a
build whose output holds such a copy fails.

## When the build fails

Change only the version record:

- add entries to `build.configure-flags` (a `--disable-<feature>` or
  `--without-<feature>` flag for a library that is not packaged, for
  example);
- set `check.enabled = false` when only the test suite fails.

Do not add a flag that works around the build environment itself (a linker
option that is rejected, a missing standard tool). Report that instead: it is
fixed in `vug`, and then every package benefits.

## What to write in the pull request

- The output of the main program.
- One sentence for every configure flag and for `check.enabled = false`,
  saying what failed without it, with the error line.
- What the record leaves out compared to the same package elsewhere:
  optional features that are switched off, and tests that do not run.
