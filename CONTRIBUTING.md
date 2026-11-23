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

Get past the problem by the first of these that applies:

1. An optional feature needs a library or tool that is not packaged: switch
   the feature off with its flag (`--disable-<feature>`,
   `--without-<feature>`, `-D<OPTION>=OFF`).
2. An already packaged tool or library is missing from the dependencies: add
   it.
3. A few tests fail for a reason outside the package: list them in
   `check.skip`. The suite cannot run at all: set `check.enabled = false`.
4. The build environment itself misbehaves (the compiler wrapper rejects an
   option, a standard tool is missing, an install step writes outside the
   output): reproduce it with the smallest command you can, then work around
   it in the record by the least invasive means.
5. The source does not build as released: add a patch under `patches/` and
   list it in `patches` of the version record. Take the upstream commit that
   fixes it, or else the patch another distribution carries, or else a
   minimal patch of your own.
6. A required dependency is not packaged: add it first, as its own pull
   request.

## What to write in the pull request

- The output of the main program.
- One sentence for every configure flag and for `check.enabled = false`,
  saying what failed without it, with the error line.
- What the record leaves out compared to the same package elsewhere:
  optional features that are switched off, and tests that do not run.
- A section `## Workarounds` with one line for every use of ways 4 to 6
  above: the error line, the smallest command that reproduces a problem of
  the build environment, where a patch comes from, and what the record does
  instead. `None` when there is nothing. The build environment is fixed in
  `vug` from this list, and the workaround is then removed.
