from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import html
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request


NAME = re.compile(r"[a-z0-9][a-z0-9+._-]{0,63}")
VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._~-]{0,63}")
STORE_ID = re.compile(r"[a-z2-7]{32}")
ARCHIVE = re.compile(r"[a-z2-7]{32}\.cpio\.zst")
TEMP = Path(os.environ["RUNNER_TEMP"])
CANDIDATE = TEMP / "candidate"
VUG = TEMP / "vug/bin/vug"
BUILD_SECONDS = Path(__file__).with_name("build-seconds.json")
DEFAULT_BUILD_SECONDS = 30
MAX_CACHE_SHARDS = 8


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


def dependency_names(tree):
    graph = {}
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
        graph[package.name] = {
            selector.partition(":")[0] for selector in selectors if selector.partition(":")[0]
        }
    return graph


def reverse_dependencies(tree, changed):
    reverse = defaultdict(set)
    for package, names in dependency_names(tree).items():
        for name in names:
            reverse[name].add(package)

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
    summary(f"Base `{base}` / candidate `{head}`")
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
            _, record = current_record(CANDIDATE / "packages" / (name[:2] if len(name) > 1 else name + "_") / name)
            if not any(listings.values()) and record.get("build", {}).get("kind") != "nix-output":
                raise ValueError("No installed files outside .vug/")
            ids[name] = outputs
            summary(f"### {name} / passed / {time.monotonic() - start:.1f} s")
            for output, files in listings.items():
                summary(f"- `{output}` / `{outputs[output]}` / {len(files)} files")
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
            summary(f"### {name} / failed / {time.monotonic() - start:.1f} s")
            if log.is_file():
                log_tail(log)
            summary(f"<pre>{html.escape(str(error))}</pre>")

    (reports / "ids.json").write_text(json.dumps(ids, sort_keys=True) + "\n")
    return int(len(ids) != len(names))


def cache_shards(tree, targets):
    graph = dependency_names(tree)
    parent = {name: name for name in targets}

    def root(name):
        while parent[name] != name:
            name = parent[name]
        return name

    for name in sorted(targets):
        seen, pending = set(), [name]
        while pending:
            for dependency in graph.get(pending.pop(), set()) - seen:
                seen.add(dependency)
                pending.append(dependency)
        for dependency in seen & targets:
            parent[root(dependency)] = root(name)
    groups = defaultdict(list)
    for name in sorted(targets):
        groups[root(name)].append(name)

    seconds = json.loads(BUILD_SECONDS.read_text(encoding="utf-8"))

    def weight(names):
        return sum(seconds.get(name, DEFAULT_BUILD_SECONDS) for name in names)

    shards = [[] for _ in range(min(MAX_CACHE_SHARDS, len(groups)))]
    for group in sorted(groups.values(), key=lambda names: (-weight(names), names)):
        min(shards, key=weight).extend(group)
    shards.sort(key=lambda names: -weight(names))
    plans = []
    for number, names in enumerate(shards, 1):
        heaviest = max(names, key=lambda name: (seconds.get(name, DEFAULT_BUILD_SECONDS), name))
        label = heaviest if len(names) == 1 else f"{heaviest} and {len(names) - 1} more"
        plans.append({"shard": number, "label": label, "packages": sorted(names)})
        summary(f"Shard {number} (about {weight(names)} s): {', '.join(sorted(names))}")
    return plans


def cache_plan():
    base, head = (os.environ[key] for key in ("BASE_SHA", "HEAD_SHA"))
    ensure_commit(base)
    ensure_commit(head)
    checkout(head, CANDIDATE)
    step_output("tag", vug_tag())
    files = git("diff", "--name-only", "--no-renames", "-z", base, head, "--", "packages/",
                capture_output=True).stdout
    packages = set()
    for path in filter(None, files.split(b"\0")):
        _, shard, name, *_ = os.fsdecode(path).split("/")
        if is_package(shard, name) and (CANDIDATE / "packages" / shard / name / "package.toml").is_file():
            packages.add(name)
    for name in os.environ.get("REBUILD_PACKAGES", "").split():
        shard = name[:2] if len(name) > 1 else name + "_"
        if not is_package(shard, name) or not (CANDIDATE / "packages" / shard / name / "package.toml").is_file():
            raise ValueError(f"Unknown package to rebuild: {name!r}")
        packages.add(name)
    summary(f"Base `{base}` / candidate `{head}`")
    summary(f"Packages built from source ({len(packages)}): {', '.join(sorted(packages)) or 'none'}")
    step_output("changed", json.dumps(sorted(packages)))
    step_output("shards", json.dumps(cache_shards(CANDIDATE, packages | {"vug"})))
    return 0


def gh_api(path, query):
    return subprocess.run(
        ["gh", "api", path, "--jq", query], check=True, capture_output=True, text=True,
    ).stdout.strip()


def cache_current():
    head = os.environ["HEAD_SHA"]
    ensure_commit(head)
    repository = os.environ["GITHUB_REPOSITORY"]
    status = gh_api(f"repos/{repository}/compare/{head}...master", ".status")
    if status not in ("identical", "ahead"):
        raise ValueError(f"master does not contain {head} (compare status {status!r})")
    workflow = Path(os.environ["GITHUB_WORKFLOW_REF"].partition("@")[0]).name
    run = int(os.environ["GITHUB_RUN_ID"])
    newer = gh_api(
        f"repos/{repository}/actions/workflows/{workflow}/runs?status=success&per_page=100",
        f"[.workflow_runs[] | select(.id > {run}) | .id] | length",
    )
    if newer != "0":
        raise ValueError(f"A later run of {workflow} has already succeeded")
    summary(f"master contains `{head}` (compare status `{status}`)")
    return 0


def published(name):
    request = urllib.request.Request(
        f"{os.environ['VUG_BINARY_CACHE_URL'].rstrip('/')}/{name}",
        method="HEAD", headers={"User-Agent": "vug-packages-cache/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def index_entry(index, path):
    contents = path.read_text(encoding="utf-8")
    if index == "index":
        key, output_id = contents.split()
        return f"keys/{key}", {output_id}
    manifest = json.loads(contents)
    return f"output-keys/{manifest['key']}", {output["id"] for output in manifest["outputs"].values()}


def cache_shard():
    staged = TEMP / "cache-export-index"
    subprocess.run(
        [sys.executable, Path(__file__).with_name("cache-export-index.py"),
         "--root", CANDIDATE, "--data-dir", TEMP / "vug-data", "--directory", staged],
        check=True,
    )
    entries = {
        path: index_entry(index, path)
        for index in ("index", "index-outputs")
        for path in sorted((staged / index).glob("*/*"))
    }
    replaced = set(os.environ.get("REPLACE_ARCHIVES", "").split())
    with ThreadPoolExecutor(max_workers=8) as pool:
        present = dict(zip(entries, pool.map(published, (key for key, _ in entries.values()))))
    for path, (_, ids) in entries.items():
        if present[path] and not ids & replaced:
            path.unlink()

    export = TEMP / "shard-export"
    subprocess.run(
        [VUG, "store", "export-cache", "--data-dir", staged, "--directory", export], check=True,
    )
    shard = TEMP / "cache-shard"
    (shard / "paths").mkdir(parents=True)
    kept = 0
    for path, (_, ids) in entries.items():
        archives = [export / "paths" / f"{output_id}.cpio.zst" for output_id in sorted(ids)]
        if not path.is_file() or not all(archive.is_file() for archive in archives):
            continue
        target = shard / path.relative_to(staged)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        for archive in archives:
            if not (shard / "paths" / archive.name).is_file():
                shutil.copyfile(archive, shard / "paths" / archive.name)
        kept += 1
    (shard / "packages.json").write_text(os.environ["PACKAGES"] + "\n", encoding="utf-8")
    sizes = [archive.stat().st_size for archive in (shard / "paths").iterdir()]
    summary(f"Unpublished index entries: {kept} of {len(entries)} / "
            f"{len(sizes)} archives / {sum(sizes)} bytes")
    return 0


def cache_merge():
    staged = TEMP / "cache-export-index"
    generated = TEMP / "generated-cache"
    (staged / "store").mkdir(parents=True)
    (generated / "paths").mkdir(parents=True)
    entries = 0
    shards = sorted((TEMP / "cache-shards").iterdir())
    for shard in shards:
        for path in sorted(shard.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            parts = path.relative_to(shard).parts
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Unexpected file type in {shard.name}: {'/'.join(parts)!r}")
            if parts == ("packages.json",):
                continue
            if len(parts) == 2 and parts[0] == "paths" and ARCHIVE.fullmatch(parts[1]):
                target = generated.joinpath(*parts)
            elif (len(parts) == 3 and parts[0] in ("index", "index-outputs")
                  and NAME.fullmatch(parts[1]) and VERSION.fullmatch(parts[2])):
                target = staged.joinpath(*parts)
            else:
                raise ValueError(f"Unexpected file in {shard.name}: {'/'.join(parts)!r}")
            if target.exists():
                if target.read_bytes() != path.read_bytes():
                    if parts[0] == "paths":
                        raise ValueError(f"Conflicting archive {'/'.join(parts)}")
                    summary(f"Kept the first of conflicting index entries {'/'.join(parts)}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            entries += parts[0] != "paths"
    summary(f"Merged {entries} index entries from {len(shards)} shards")
    step_output("entries", entries)
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
            summary(f"- **{name}** / matched / {html.escape(str(primary))}")
        else:
            failed = True
            summary(f"- **{name}** / {'different' if primary and repeat else 'missing result'}"
                    f" / primary {html.escape(str(primary))} / repeat {html.escape(str(repeat))}")
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
    handlers = {
        "plan": plan, "build": build, "compare": compare, "probe": probe,
        "cache-plan": cache_plan, "cache-current": cache_current,
        "cache-shard": cache_shard, "cache-merge": cache_merge,
    }
    command = sys.argv[1]
    if command == "checkout":
        checkout(os.environ["HEAD_SHA"], CANDIDATE)
    elif command == "cache-checkout":
        checkout(os.environ["HEAD_SHA"], CANDIDATE)
        checkout(os.environ["BASE_SHA"], TEMP / "previous")
    else:
        try:
            sys.exit(handlers[command]())
        except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
            summary(f"<pre>{html.escape(str(error))}</pre>")
            sys.exit(1)
