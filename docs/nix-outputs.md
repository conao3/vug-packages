# Nix outputs

A `nix-output` version records the Nix system, a full nixpkgs commit, an
attribute, and each selected output's store path and NAR hash. vug obtains
the signed reference closure from `https://cache.nixos.org`. It publishes
links for the selected files under `bin/` and `share/`.

## Generate a version record

Use Python 3.11 or newer, OpenSSL with Ed25519 support, Nix, and a version of
vug that supports `nix-output`. Choose the nixpkgs attribute corresponding
to the upstream package and verify its license. Choose files to expose
from the Nix output's `bin` and `share` paths.

For example, generate a GNU Which version record:

```sh
rev=2786a2a72088ecf32aea5b3ec52099e6a7cff78f
python3 .github/scripts/nix-outputs.py record generate-version \
  --nixpkgs-rev "$rev" --system x86_64-linux --attribute which \
  --outputs out --expose out:bin/which > /tmp/which-2.25.toml
```

The generator evaluates the version and each selected output with Nix.
It reads narinfo from the public cache and verifies the signature and
hash of each root and every path in its reference closure. Its standard
output is a version record. Put it in the package's `versions` directory
with the version as the filename. For a new package, add a
`package.toml` with its name, license, and homepage. Set
`sources = ["https://cache.nixos.org/"]` in `[upstream]`.
Format both files with `vug record format-record --file <file> --write`,
then run `vug record check-tree --root .`.

List the actual Nix output names in `--outputs`. Prefix each exposure
with its output name, as in `--expose bin:bin/ffmpeg bin:bin/ffprobe`.
For an executable in a separate `bin` output, use a selector such as
`ffmpeg:bin`. Keep libraries and headers within the Nix closure. Use the
program that owns those dependencies for glibc-based execution.

## Verify changed closures

Run the same metadata check as CI from the repository root. Pass the
base and head commit IDs in `base` and `head`:

```sh
python3 .github/scripts/nix-outputs.py record check-closure \
  --base "$base" --head "$head"
```

The check starts from each changed `nix-output` version record and verifies
each root hash and the `cache.nixos.org-1` signature of every reachable
narinfo. A missing path, root hash mismatch, or invalid signature fails
the check. vug verifies and unpacks the NARs when it acquires the output.

## Run GCC and PyTorch

Run from the repository root with a vug executable that mounts the
acquired Nix closure for `package run-package`.

```sh
data=$(mktemp -d)
cache=$(mktemp -d)

vug package run-package --root . --name gcc \
  --data-dir "$data" --cache-dir "$cache" --program bin/gcc --arguments --version

cat > "$data/torch-smoke.py" <<'PYTHON'
import torch

print(torch.__version__)
print(torch.tensor([1, 2, 3]).sum().item())
PYTHON

vug package run-package --root . --name python-torch \
  --data-dir "$data" --cache-dir "$cache" --program bin/torchrun \
  --arguments --standalone --nproc-per-node=1 --start-method=fork --run-path \
  "$data/torch-smoke.py"
```

GCC prints version `16.2.0`. The Nix `torchrun` launcher supplies the
matching Python and PyTorch, and the script prints `2.15.0` and `6`.
