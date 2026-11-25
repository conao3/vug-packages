import argparse
import base64
import json
import re
import ssl
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, build_opener

CACHE = "https://cache.nixos.org"
KEY_NAME = "cache.nixos.org-1"
KEY = "6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="  # gitleaks:allow
NIX32 = "0123456789abcdfghijklmnpqrsvwxyz"
STORE_PATH = re.compile(rf"/nix/store/[{NIX32}]{{32}}-[A-Za-z0-9+._?=-]+")
NAR_HASH = re.compile(rf"sha256:[01][{NIX32}]{{51}}")
ATTRIBUTE = re.compile(r"[A-Za-z0-9_+'-]+(?:\.[A-Za-z0-9_+'-]+)*")
OUTPUT = re.compile(r"[a-z0-9][a-z0-9+._-]{0,63}")
TLS = ssl.create_default_context()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@lru_cache(maxsize=16384)
def narinfo(path):
    if not STORE_PATH.fullmatch(path):
        raise ValueError(f"invalid Nix store path: {path}")
    try:
        with build_opener(NoRedirect(), HTTPSHandler(context=TLS)).open(
            f"{CACHE}/{Path(path).name[:32]}.narinfo", timeout=30
        ) as response:
            data = response.read(65537)
    except URLError as error:
        error.add_note(path)
        raise
    if len(data) > 65536:
        raise ValueError(f"{path}: oversized narinfo")
    fields = {}
    signatures = []
    for line in data.decode("ascii").splitlines():
        name, value = line.split(": ", 1)
        if name == "Sig":
            if value.startswith(f"{KEY_NAME}:"):
                signatures.append(value.split(":", 1)[1])
        elif name in fields:
            raise ValueError(f"{path}: repeated {name}")
        else:
            fields[name] = value

    nar_hash = fields.get("NarHash", "")
    size = fields.get("NarSize", "")
    if (
        fields.get("StorePath") != path
        or not NAR_HASH.fullmatch(nar_hash)
        or not re.fullmatch(r"[1-9][0-9]*", size)
    ):
        raise ValueError(f"{path}: invalid StorePath, NarHash or NarSize")
    references = [f"/nix/store/{name}" for name in fields.get("References", "").split()]
    if any(not STORE_PATH.fullmatch(ref) for ref in references) or len(
        references
    ) != len(set(references)):
        raise ValueError(f"{path}: invalid References")
    fingerprint = f"1;{path};{nar_hash};{size};{','.join(sorted(references))}"
    return nar_hash, references, fingerprint, signatures


class Cache:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.public_key = self.directory / "public.der"
        self.public_key.write_bytes(
            bytes.fromhex("302a300506032b6570032100") + base64.b64decode(KEY)
        )
        self.verified = {}

    def verify(self, path):
        if path in self.verified:
            return self.verified[path]
        nar_hash, references, fingerprint, signatures = narinfo(path)
        message_file = self.directory / "message"
        signature_file = self.directory / "signature"
        message_file.write_text(fingerprint, encoding="ascii")
        for signature in signatures:
            try:
                signature_file.write_bytes(base64.b64decode(signature, validate=True))
            except ValueError:
                continue
            result = subprocess.run(
                [
                    "openssl",
                    "pkeyutl",
                    "-verify",
                    "-rawin",
                    "-pubin",
                    "-keyform",
                    "DER",
                    "-inkey",
                    self.public_key,
                    "-in",
                    message_file,
                    "-sigfile",
                    signature_file,
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                self.verified[path] = nar_hash, references
                return self.verified[path]
        raise ValueError(f"{path}: missing valid {KEY_NAME} signature")

    def check_closure(self, outputs):
        pending = set()
        for output in outputs.values():
            path = output["store-path"]
            actual_hash, _ = self.verify(path)
            if actual_hash != output["nar-hash"]:
                raise ValueError(
                    f"{path}: expected {output['nar-hash']}, got {actual_hash}"
                )
            pending.add(path)
        visited = set()
        with ThreadPoolExecutor(max_workers=12) as executor:
            while pending:
                if len(visited) + len(pending) > 4096:
                    raise ValueError("Nix reference closure exceeds 4096 paths")
                list(executor.map(narinfo, sorted(pending - self.verified.keys())))
                visited.update(pending)
                pending = {
                    ref for path in pending for ref in self.verify(path)[1]
                } - visited
        return len(visited)


def run(*command):
    return subprocess.check_output(command, text=True)


def version_record(version, system, revision, attribute, outputs):
    lines = [
        f"version = {json.dumps(version)}",
        "",
        "[build]",
        'kind = "nix-output"',
        f"system = {json.dumps(system)}",
        f"nixpkgs-rev = {json.dumps(revision)}",
        f"attribute = {json.dumps(attribute)}",
    ]
    for name, output in sorted(outputs.items()):
        lines.extend(
            [
                "",
                f"[build.nix-outputs.{json.dumps(name)}]",
                f"store-path = {json.dumps(output['store-path'])}",
                f"nar-hash = {json.dumps(output['nar-hash'])}",
                f"expose = {json.dumps(sorted(output['expose']))}",
            ]
        )
    lines.extend(
        [
            "",
            "[dependencies]",
            "build = []",
            "run = []",
            "",
            "[check]",
            "enabled = false",
            "skip = []",
        ]
    )
    return "\n".join(lines)


def generate_version(args, cache):
    if not re.fullmatch(r"[0-9a-f]{40}", args.nixpkgs_rev):
        raise ValueError("--nixpkgs-rev requires a full lowercase Git commit SHA")
    if not re.fullmatch(
        r"[a-z0-9_]+-[a-z0-9_]+", args.system
    ) or not ATTRIBUTE.fullmatch(args.attribute):
        raise ValueError("invalid --system or --attribute")
    paths = {}
    if args.store_path:
        if not args.version:
            raise ValueError("--store-path requires --version")
        for selection in args.store_path:
            name, _, path = selection.partition(":")
            if (
                not OUTPUT.fullmatch(name)
                or name in paths
                or not STORE_PATH.fullmatch(path)
            ):
                raise ValueError(
                    f"invalid store path selection {selection!r}; "
                    "expected a unique OUTPUT:/nix/store/HASH-NAME"
                )
            paths[name] = path
        version = args.version
        names = list(paths)
    else:
        if args.version is not None:
            raise ValueError("--version requires --store-path")
        names = args.outputs or ["out"]
        if len(set(names)) != len(names) or any(
            not OUTPUT.fullmatch(name) for name in names
        ):
            raise ValueError("--outputs requires unique output names")

    exposed = {name: [] for name in names}
    for selection in args.expose:
        name, separator, path = selection.partition(":")
        parts = path.split("/")
        if (
            not separator
            or name not in exposed
            or len(parts) < 2
            or parts[0] not in {"bin", "share"}
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in path
        ):
            raise ValueError(
                f"invalid exposure {selection!r}; expected OUTPUT:bin/FILE or OUTPUT:share/FILE"
            )
        exposed[name].append(path)

    if not paths:
        installable = f"github:NixOS/nixpkgs/{args.nixpkgs_rev}#legacyPackages.{args.system}.{args.attribute}"
        nix_eval = (
            "nix",
            "--extra-experimental-features",
            "nix-command flakes",
            "eval",
            "--option",
            "allow-import-from-derivation",
            "false",
            "--raw",
        )
        version = run(*nix_eval, f"{installable}.version").strip()
        paths = {
            name: run(*nix_eval, f"{installable}.{name}.outPath").strip()
            for name in names
        }
    outputs = {}
    for name, path in paths.items():
        nar_hash, _ = cache.verify(path)
        outputs[name] = {
            "store-path": path,
            "nar-hash": nar_hash,
            "expose": exposed[name],
        }
    cache.check_closure(outputs)
    print(
        version_record(version, args.system, args.nixpkgs_rev, args.attribute, outputs)
    )


def check_closure(args, cache):
    base = run(
        "git", "rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}"
    ).strip()
    head = run(
        "git", "rev-parse", "--verify", "--end-of-options", f"{args.head}^{{commit}}"
    ).strip()
    files = run(
        "git",
        "diff",
        "--name-only",
        "--diff-filter=ACMRT",
        "-z",
        base,
        head,
        "--",
        "packages/",
    ).split("\0")
    failed = False
    count = 0
    for path in files:
        if not re.fullmatch(r"packages/[^/]+/[^/]+/versions/[^/]+\.toml", path):
            continue
        try:
            record = tomllib.loads(run("git", "show", f"{head}:{path}"))
            build = record.get("build", {})
            if not isinstance(build, dict):
                raise TypeError("build must be a table")
            if build.get("kind") == "nix-output":
                outputs = build["nix-outputs"]
                if not isinstance(outputs, dict) or not outputs:
                    raise ValueError("nix-outputs must be a nonempty table")
                paths = cache.check_closure(outputs)
                print(f"{path}: {paths} signed cache paths")
                count += 1
        except (
            KeyError,
            OSError,
            TypeError,
            ValueError,
            subprocess.CalledProcessError,
        ) as error:
            print(f"{path}: {error}", *getattr(error, "__notes__", []), file=sys.stderr)
            failed = True
    print(f"Verified {count} Nix output records")
    return 254 if failed else 0


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_subparsers(required=True).add_parser("record")
    operations = group.add_subparsers(required=True)
    generate = operations.add_parser(
        "generate-version",
        help="Write a version record from evaluated or explicit Nix outputs",
    )
    generate.add_argument("--nixpkgs-rev", required=True)
    generate.add_argument("--system", required=True)
    generate.add_argument("--attribute", required=True)
    selection = generate.add_mutually_exclusive_group()
    selection.add_argument("--outputs", nargs="+")
    selection.add_argument("--store-path", action="append", metavar="OUTPUT:PATH")
    generate.add_argument("--version", help="Package version for explicit store paths")
    generate.add_argument("--expose", nargs="*", default=[], metavar="OUTPUT:PATH")
    generate.set_defaults(operation=generate_version)
    check = operations.add_parser(
        "check-closure", help="Verify signed cache closures of changed version records"
    )
    check.add_argument("--base", required=True)
    check.add_argument("--head", required=True)
    check.set_defaults(operation=check_closure)
    args = parser.parse_args()

    try:
        with TemporaryDirectory() as directory:
            return args.operation(args, Cache(directory))
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(error, *getattr(error, "__notes__", []), file=sys.stderr)
        return 254


if __name__ == "__main__":
    sys.exit(main())
