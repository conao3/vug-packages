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

## Python modules

Name Python modules and build backends `python-<normalized PyPI name>`.
Lowercase the PyPI name and replace underscores and dots with hyphens.
Use the upstream command name for standalone programs such as `meson` and `yt-dlp`.

Use the upstream sdist URL and SHA-256. For a small Flit package:

```toml
[build]
kind = "python"
imports = [
  "jinja2",
]

[dependencies]
build = [
  "python-build",
  "python-flit-core",
  "python-installer",
]
run = [
  "markupsafe",
  "python",
]

[check]
enabled = true
skip = []
```

[MarkupSafe](packages/ma/markupsafe/versions/3.0.3.toml) uses setuptools
and checks that its C extension loads. [Jinja2](packages/ji/jinja2/versions/3.1.6.toml)
uses Flit and imports MarkupSafe at runtime.

- `python-build` invokes the PEP 517 backend through `python-pyproject-hooks`.
  `python-installer` installs the resulting wheel. Setuptools builds
  MarkupSafe, and Flit Core builds Jinja2. Use the backend named in the
  sdist's `pyproject.toml`.

  | `[build-system].requires` in the sdist | vug package in `dependencies.build` |
  | --- | --- |
  | `setuptools` | `python-setuptools` |
  | `setuptools-scm`, including `setuptools_scm[toml]` | `python-setuptools-scm` |
  | `vcs-versioning` | `python-vcs-versioning` |
  | `wheel` | `python-wheel` |
  | `flit_core` | `python-flit-core` |
  | `hatchling` | `python-hatchling` |
  | `hatch-vcs` | `python-hatch-vcs` |
  | `hatch-fancy-pypi-readme` | `python-hatch-fancy-pypi-readme` |
  | `python-sbom[hatch]` | `python-python-sbom`, with `python-hatchling` for the Hatch plugin |
  | `poetry-core` | `python-poetry-core` |
  | `pdm-backend` | `python-pdm-backend` |
  | `meson-python` | `python-meson-python` |
  | `meson` | `meson` |
  | `pyproject-metadata` | `python-pyproject-metadata` |
  | `cython` | `python-cython` |

  In-tree backends with a `backend-path` use the code from the sdist.
  Include the frontend, installer, and the backend's applicable build
  requirements. Meson Python uses the packaged Meson, Samurai and patchelf
  executables for Linux builds.

- Put import dependencies and native libraries in `run`. Put suite
  runners and plugins in `build`. Use `imports` for import checks,
  `native-imports` to require compiled modules, or set `pytest = true`
  to run a suite with the packaged pytest runner.
- For a missing build requirement, add the matching backend or tool to
  `build`. For an unsatisfied wheel runtime requirement, add a package
  with matching installed Python distribution metadata to `run`. For
  a missing native extension, check the compiler output and the runtime
  libraries and confirm that the resulting wheel targets the selected Python.

## Go executables

Declare each main package and binary name. Use `.` for the module root or
`./cmd/tool` for a subdirectory.

```toml
[build]
kind = "go"
targets = [{ path = ".", bin = "tool" }]

[dependencies]
build = ["go"]
run = []

[check]
enabled = true
skip = []
```

Use the existing `go` package as a build dependency. List any tools or data
needed at runtime in `dependencies.run`. vug runs `go test ./...` offline
when checks are enabled. Use a source patch for a test that needs a portable
fixture path.

If the source contains `vendor/`, the source hash covers it and Go uses
vendor mode. Otherwise vug fetches the module files from the Go module
proxy and verifies them against the pinned source's `go.sum`. Include
checksums for the manifests and source ZIPs required by the declared
targets and enabled tests.

For a checksum or module lookup error, check the upstream `go.mod` and
`go.sum` at the pinned revision. If Go reports a newer required version,
check the selected `go` record against the source's `go.mod`. This build
kind compiles with cgo disabled.

[gh-poi](packages/gh/gh-poi/versions/0.18.6.toml) embeds its test fixtures so
the tests can run with `-trimpath`.
Use GitHub CLI and Git for its branch-management commands.
[yq-go](packages/yq/yq-go/versions/4.54.1.toml) installs the `yq` executable
and tests the root module.

## Node CLI packages

Pin a source archive that contains `package.json` and a version 3
`package-lock.json`. The lock entries must point to npm registry tarballs
with SHA-512 integrity. Use this build configuration for a CLI whose
manifest declares a `bin` entry and a `build` script:

```toml
[build]
kind = "node"
build-script = true

[dependencies]
build = ["npm"]
run = ["nodejs"]

[check]
enabled = false
skip = []
```

The build uses `npm` with the packaged `nodejs`. The Node executable
uses Alpine's `libgcc` and `libstdcxx` at runtime. The `patchelf`
build tool sets the interpreter and library search path for Node. A
package can also use it to relocate a bundled musl interpreter. Declare
the executable's project-relative path in `build.musl-executables`,
add `patchelf` to build dependencies, and add `musl` to run dependencies.

Set `check.enabled = true` when the upstream `test` script exits on its
own and works offline. Use an empty `check.skip`. Both example records
use ACP `initialize` as a CLI smoke test. Both build scripts run with
dependency install hooks disabled. `codex-acp` uses the lock's esbuild
platform package directly and declares the private C++ runtime in its
bundled Codex distribution.

If the lock is missing, use a pinned source archive or patch containing a
reviewed lock generated for the same manifest. For an integrity failure,
compare the tarball bytes against the lock and review the input before
updating it. For `ENOTCACHED`, look for the missing package in the lock
and check its platform fields. If a package needs a lifecycle hook to
download an executable, supply the pinned platform artifact before the
offline build. For a native executable that exists but returns
`ENOENT`, inspect its ELF interpreter and check that the runtime closure
contains it.

[claude-agent-acp](packages/cl/claude-agent-acp/versions/0.107.0.toml)
uses TypeScript and the Claude SDK's Linux x64 musl optional package.
[codex-acp](packages/co/codex-acp/versions/2.6.0.toml) bundles TypeScript
with esbuild and keeps the Linux x64 Codex native distribution.

## Perl modules

Name Perl modules `perl-<lowercase CPAN distribution name>`, for example
`perl-try-tiny` for Try-Tiny.

Start with the pinned CPAN archive's URL and SHA-256 in `[source]`.
For a distribution with `Makefile.PL`, use these fields:

```toml
[build]
kind = "perl"
driver = "make-maker"
modules = ["Try::Tiny"]

[dependencies]
build = ["make"]
run = ["perl"]

[check]
enabled = true
skip = []
```

[Try::Tiny](packages/tr/try-tiny/versions/0.32.toml) is a pure Perl
example. [Clone](packages/cl/clone/versions/0.51.toml) builds XS and links
against musl. Its upstream suite requires
[B::COW](packages/b-/b-cow/versions/0.007.toml), so Clone lists it in
`dependencies.build`.

- For a distribution with `Build.PL`, choose `driver = "module-build"` and
  provide `Module::Build` in `dependencies.build` if the archive requires it.
- Add required configure, build and test modules to `dependencies.build`.
  Add Perl, imported modules and shared libraries needed by XS extensions to
  `dependencies.run`. Read the distribution's `META.json` or `META.yml`
  for module version constraints.
- Enable the upstream tests with `check.enabled = true` and list the modules
  to load after installation in `build.modules`. The suite runs in the sandbox.
- If a prerequisite check fails, choose a package that supplies the named
  module at a compatible version and place it in the dependency group for
  the failing phase. For a Perl ABI mismatch, rebuild the dependency with
  the selected Perl. For an XS loader error, check the library dependencies
  and load the named module from the installed tree.

## Tree-sitter grammars

Use `build.kind = "tree-sitter"` for archives that ship `src/parser.c`.
Set `build.path` when the grammar lives below the archive root and
`build.language` to the suffix of its exported `tree_sitter_*` symbol.
See the [TSX record](packages/tr/tree-sitter-tsx/versions/0.23.2.toml).

Declare `musl` in `dependencies.run` and add `libcxx` for a C++ scanner.
The builder compiles the generated parser and scanner without running the
upstream generator. Set `check.enabled = false` and validate the installed
library by parsing a short example with the packaged Tree-sitter runtime.
Check that upstream query files are present under `queries/<language>/`.

## Package builds on GitHub

Open the **Package builds** run from your pull request. The plan lists the
changed packages and their transitive reverse dependencies. The primary job
builds those packages and runs any tests enabled in their records. A second
runner starts with an empty store and rebuilds the changed packages. **Compare
output IDs** checks that both runners produced the same ID for every output
of each changed package.

The build summary shows each package's result, elapsed time, output IDs,
installed files, and the tail of the log for failures. The
`package-results-*` artifacts contain the full file lists and logs. The plan
reports **Incomplete** and fails when the change affects more than twelve
packages. Changes outside `packages/` finish after planning.

## When the build fails

Get past the problem by the first of these that applies:

1. An optional feature needs a library or tool that is not packaged: switch
   the feature off with its flag (`--disable-<feature>`,
   `--without-<feature>`, `-D<OPTION>=OFF`).
2. An already packaged tool or library is missing from the dependencies: add
   it.
3. For `autotools` and `make`, set `check.target` when upstream runs its
   tests with a different Make target, such as `test` or `check_local`. List
   individual test exclusions in `check.skip`. Set `check.enabled = false`
   when the suite cannot run.
4. For `autotools` and `meson`, set `build.path` to the source subdirectory
   containing `configure` or `meson.build`.
5. The build environment itself misbehaves (the compiler wrapper rejects an
   option, a standard tool is missing, an install step writes outside the
   output): reproduce it with the smallest command you can, then work around
   it in the record by the least invasive means.
6. The source does not build as released: add a patch under `patches/` and
   list it in `patches` of the version record. Take the upstream commit that
   fixes it, or else the patch another distribution carries, or else a
   minimal patch of your own.
7. A required dependency is not packaged: add it first, as its own pull
   request.

## What to write in the pull request

- The output of the main program.
- One sentence for every configure flag and for `check.enabled = false`,
  saying what failed without it, with the error line.
- What the record leaves out compared to the same package elsewhere:
  optional features that are switched off, and tests that do not run.
- A section `## Workarounds` with one line for every use of ways 5 to 7
  above: the error line, the smallest command that reproduces a problem of
  the build environment, where a patch comes from, and what the record does
  instead. `None` when there is nothing. The build environment is fixed in
  `vug` from this list, and the workaround is then removed.
