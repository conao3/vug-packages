from collections import defaultdict
import html
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import tomllib


NAME = re.compile(r"[a-z0-9][a-z0-9+._-]{0,63}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]{0,63}")
STORE_ID = re.compile(r"[a-z2-7]{32}")
TEMP = Path(os.environ["RUNNER_TEMP"])
CANDIDATE = TEMP / "candidate"
CACHE_PUBLISH_PLAN = TEMP / "cache-publish-plan"
VUG = TEMP / "vug/bin/vug"


def git(*args, **kwargs):
    return subprocess.run(["git", *args], check=True, **kwargs)


def ensure_commit(sha):
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        raise ValueError(f"Expected a full commit SHA, got {sha!r}")
    if subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"]).returncode:
        git("fetch", "--no-tags", "--depth=1", "origin", sha)
    if git("cat-file", "-t", sha, capture_output=True, text=True).stdout.strip() != "commit":
        raise ValueError(f"{sha} is not a commit")


def checkout(sha, path):
    ensure_commit(sha)
    git("worktree", "add", "--detach", path, sha)


def summary(line):
    print(line.translate({10: r"\n", 13: r"\r"}))
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
        file.write(line + "\n")


def step_output(name, value):
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as file:
        file.write(f"{name}={value}\n")


def version_key(version):
    return [
        (1, int(part)) if part.isdigit() else (0, part)
        for part in re.findall(r"[0-9]+|[^0-9.\-_+]+", version)
    ]


def current_record(package):
    files = [
        file for file in (package / "versions").glob("*.toml")
        if file.is_file() and VERSION.fullmatch(file.stem) and not file.stem.endswith(".toml")
    ]
    if not files:
        raise ValueError(f"No version records for {package.name}")
    file = max(files, key=lambda entry: version_key(entry.stem))
    if sum(version_key(entry.stem) == version_key(file.stem) for entry in files) > 1:
        raise ValueError(f"Ambiguous current version for {package.name}")
    return file.stem, tomllib.loads(file.read_text(encoding="utf-8"))


def is_package(shard, name):
    expected_shard = name[:2] if len(name) > 1 else name + "_"
    return bool(NAME.fullmatch(name)) and not name.endswith(".toml") and shard == expected_shard


def reverse_dependencies(tree, changed):
    reverse = defaultdict(set)
    for package_file in (tree / "packages").glob("*/*/package.toml"):
        package = package_file.parent
        if not is_package(package.parent.name, package.name):
            continue
        _, record = current_record(package)
        dependencies = record.get("dependencies", {})
        selectors = (
            dependencies.get("build", []) + dependencies.get("run", [])
            + dependencies.get("expose", [])
        )
        for output in record.get("outputs", {}).values():
            selectors.extend(output.get("run", []))
        for selector in selectors:
            name = selector.partition(":")[0]
            if name:
                reverse[name].add(package.name)

    affected = set(changed)
    pending = list(changed)
    while pending:
        for dependent in reverse[pending.pop()] - affected:
            affected.add(dependent)
            pending.append(dependent)
    return affected - changed


def plan():
    base, head = (os.environ[key] for key in ("BASE_SHA", "HEAD_SHA"))
    ensure_commit(head)
    if not base:
        git("fetch", "--no-tags", "--depth=2", "origin", head)
        base = git("rev-parse", f"{head}^1", capture_output=True, text=True).stdout.strip()
    ensure_commit(base)
    summary(f"Base `{base}` · candidate `{head}`")
    if os.environ["NETWORK_PROBE"] == "true":
        checkout(head, CANDIDATE)
        step_output("tag", vug_tag())
        step_output("mode", "probe")
        step_output("lanes", '["probe"]')
        return 0

    paths = git(
        "diff", "--name-only", "--no-renames", "-z", base, head, "--", "packages/",
        capture_output=True,
    ).stdout.split(b"\0")
    changed = set()
    for path in filter(None, paths):
        parts = os.fsdecode(path).split("/")
        if len(parts) < 4 or not is_package(*parts[1:3]):
            raise ValueError(f"Unexpected package path: {os.fsdecode(path)!r}")
        changed.add(parts[2])

    if not changed:
        summary("No package changes.")
        return 0

    checkout(head, CANDIDATE)
    checkout(base, TEMP / "base")
    dependents = sorted(
        reverse_dependencies(TEMP / "base", changed)
        | reverse_dependencies(CANDIDATE, changed)
    )
    targets = sorted(changed) + dependents
    limit = int(os.environ["MAX_PACKAGES"])
    summary(f"Changed ({len(changed)}): {', '.join(sorted(changed))}")
    summary(f"Reverse dependencies ({len(dependents)}): {', '.join(dependents) or 'none'}")
    if len(targets) > limit:
        summary(f"**Incomplete:** {len(targets)} targets exceed the {limit}-package limit.")
        return 1
    step_output("tag", vug_tag())
    step_output("mode", "build")
    step_output("lanes", '["primary","repeat"]')
    step_output("changed", json.dumps(sorted(changed)))
    step_output("targets", json.dumps(targets))
    return 0


def vug_tag():
    version, _ = current_record(CANDIDATE / "packages/vu/vug")
    tag = f"v{version}"
    if "~" in tag:
        raise ValueError(f"Expected a rust-vug release tag for version {version}")
    summary(f"vug release `{tag}`")
    return tag


def build_package(name, data, log, from_source=()):
    cache_url = os.environ.get("VUG_BINARY_CACHE_URL")
    public_key = os.environ.get("VUG_CACHE_PUBLIC_KEY")
    options = []
    if cache_url and public_key:
        version, _ = current_record(CANDIDATE / "packages/vu/vug")
        if version_key(version) >= version_key("0.2.14"):
            options = ["--binary-cache", cache_url, "--trusted-key", public_key]
    if options:
        for package in from_source:
            if not NAME.fullmatch(package):
                raise ValueError("Invalid changed package name")
            options.extend(["--build-from-source", package])
    with log.open("w+b") as stderr:
        result = subprocess.run(
            [VUG, "--output", "json", "package", "build-package",
             "--root", CANDIDATE, "--name", name, "--jobs", os.environ.get("VUG_BUILD_JOBS", "4"),
             "--data-dir", data, "--cache-dir", data.parent / "sources", *options],
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
    if result.returncode:
        raise ValueError(f"vug exited with status {result.returncode}")

    info = json.loads(result.stdout)
    version = info["Version"]
    if not VERSION.fullmatch(version) or info["Name"] != name:
        raise ValueError("Unexpected package build result")
    manifest = data / "index-outputs" / name / version
    if manifest.is_file():
        outputs = json.loads(manifest.read_text())["outputs"]
        return {name: output["id"] for name, output in outputs.items()}
    return {"out": Path(info["Path"]).name}


def installed_files(directory):
    files = []
    for parent, dirs, entries in os.walk(directory):
        if Path(parent) == directory and ".vug" in dirs:
            dirs.remove(".vug")
        files.extend(
            (Path(parent) / entry).relative_to(directory).as_posix()
            for entry in dirs + entries
            if entry != ".vug" or Path(parent) != directory
            if (Path(parent) / entry).is_symlink() or (Path(parent) / entry).is_file()
        )
    return sorted(files)


def log_tail(path):
    with path.open("rb") as log:
        log.seek(max(0, path.stat().st_size - 8192))
        text = "\n".join(log.read().decode("utf-8", errors="replace").splitlines()[-40:])
    summary(f"<pre>{html.escape(text)}</pre>")


def build():
    data = TEMP / "vug-data"
    reports = TEMP / "package-results"
    reports.mkdir()
    ids = {}
    preview_budget = 160_000
    names = json.loads(os.environ["PACKAGES"])
    from_source = json.loads(os.environ.get("CHANGED_PACKAGES", "[]"))
    if CACHE_PUBLISH_PLAN.is_file() and "vug" not in names:
        names.append("vug")
    for name in names:
        start = time.monotonic()
        log = reports / f"{name}.log"
        try:
            outputs = build_package(name, data, log, from_source)
            listings = {}
            for output, store_id in sorted(outputs.items()):
                if not NAME.fullmatch(output) or not STORE_ID.fullmatch(store_id):
                    raise ValueError("Invalid output name or ID")
                listings[output] = installed_files(data / "store" / store_id)
            (reports / f"{name}-files.json").write_text(json.dumps(listings, indent=2) + "\n")
            if not any(listings.values()):
                raise ValueError("No installed files outside .vug/")
            ids[name] = outputs
            summary(f"### {name} · passed · {time.monotonic() - start:.1f} s")
            for output, files in listings.items():
                summary(f"- `{output}` · `{outputs[output]}` · {len(files)} files")
                preview = ""
                for file in files:
                    line = html.escape(json.dumps(file, ensure_ascii=True)[1:-1]) + "\n"
                    if len(line) > preview_budget:
                        preview += "… Full file list in the package-results artifact.\n"
                        break
                    preview += line
                    preview_budget -= len(line)
                if preview:
                    summary(f"<details><summary>Installed files</summary><pre>{preview}</pre></details>")
        except (ValueError, OSError, KeyError, TypeError) as error:
            summary(f"### {name} · failed · {time.monotonic() - start:.1f} s")
            if log.is_file():
                log_tail(log)
            summary(f"<pre>{html.escape(str(error))}</pre>")

    (reports / "ids.json").write_text(json.dumps(ids, sort_keys=True) + "\n")
    return int(len(ids) != len(names))


def cache_plan():
    base, head = (os.environ[key] for key in ("BASE_SHA", "HEAD_SHA"))
    ensure_commit(base)
    ensure_commit(head)
    checkout(head, CANDIDATE)
    checkout(base, TEMP / "previous")
    step_output("tag", vug_tag())
    files = git("diff", "--name-only", "--no-renames", "-z", base, head, "--", "packages/",
                capture_output=True).stdout
    packages = set()
    for path in filter(None, files.split(b"\0")):
        _, shard, name, *_ = os.fsdecode(path).split("/")
        if is_package(shard, name) and (CANDIDATE / "packages" / shard / name / "package.toml").is_file():
            packages.add(name)
    CACHE_PUBLISH_PLAN.touch()
    step_output("changed", json.dumps(sorted(packages)))
    return 0


def compare():
    results = []
    for lane in ("primary", "repeat"):
        path = TEMP / "comparison" / f"package-results-{lane}" / "ids.json"
        results.append(json.loads(path.read_text()) if path.is_file() else {})
    failed = False
    for name in json.loads(os.environ["PACKAGES"]):
        primary, repeat = (result.get(name, {}) for result in results)
        if primary and primary == repeat:
            summary(f"- **{name}** · matched · {html.escape(str(primary))}")
        else:
            failed = True
            summary(f"- **{name}** · {'different' if primary and repeat else 'missing result'}"
                    f" · primary {html.escape(str(primary))} · repeat {html.escape(str(repeat))}")
    return int(failed)


def probe():
    address = socket.getaddrinfo("github.com", 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    with socket.create_connection((address, 80), timeout=8) as connection:
        summary(f"Runner TCP control connected to {connection.getpeername()[0]}:80.")

    package = CANDIDATE / "packages/ci/ci-network-probe"
    (package / "versions").mkdir(parents=True)
    (package / "patches").mkdir()
    (package / "package.toml").write_text(
        'name = "ci-network-probe"\nlicense = "GPL-3.0-or-later"\n\n'
        '[upstream]\nsources = ["https://ftp.gnu.org/gnu/hello/"]\n'
    )
    _, hello = current_record(CANDIDATE / "packages/he/hello")
    source = hello["source"]
    (package / "versions/1.toml").write_text(f'''version = "1"
patches = ["1-network.patch"]

[source]
url = "{source["url"]}"
sha256 = "{source["sha256"]}"

[build]
kind = "make"
make-flags = ["-f", "ci-network-probe.mk", "OUT={{out}}"]

[dependencies]
build = ["make"]
run = []

[check]
enabled = false
skip = []
''')
    makefile = f"""all:
\t@if busybox nc -v -n -z -w 8 {address} 80 2> connection-error; then echo reachable > result; \\
\telse cat connection-error >&2; grep -Eqi 'Network (is )?unreachable' connection-error && echo blocked > result; fi
install:
\t@cp result "$(OUT)/result"
"""
    (package / "patches/1-network.patch").write_text(
        "--- /dev/null\n+++ b/ci-network-probe.mk\n"
        f"@@ -0,0 +1,{len(makefile.splitlines())} @@\n"
        + "".join(f"+{line}\n" for line in makefile.splitlines())
    )
    data = TEMP / "vug-data"
    log = TEMP / "network-probe.log"
    try:
        result = build_package("ci-network-probe", data, log)
    finally:
        if log.is_file():
            log_tail(log)
    status = (data / "store" / result["out"] / "result").read_text().strip()
    summary(f"Sandbox public IPv4 connection: **{status}**.")
    return int(status != "blocked")


if __name__ == "__main__":
    handlers = {"plan": plan, "build": build, "compare": compare, "probe": probe, "cache-plan": cache_plan}
    command = sys.argv[1]
    if command == "checkout":
        checkout(os.environ["HEAD_SHA"], CANDIDATE)
    else:
        try:
            sys.exit(handlers[command]())
        except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
            summary(f"<pre>{html.escape(str(error))}</pre>")
            sys.exit(1)
