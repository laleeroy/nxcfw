#!/usr/bin/env python3
"""Manifest-driven SD card builder.

Reads tools.toml, downloads the latest GitHub release assets and assembles
them into a build directory. Needs Python 3.11+, no third-party packages.

After a complete build it writes manifest.json (versions + file hashes) next
to this script. If a manifest.json already existed, it also writes
changelog.md with what changed since then.

Examples:
  ./build.py                         # build everything into ./sdcard
  ./build.py --jobs 8                # download 8 tools at a time (default: 1)
  ./build.py --dry-run               # resolve every asset, write nothing
  ./build.py --only hekate,DBIPatcher       # iterate on a subset
  ./build.py --clean --zip out/sd.zip
  ./build.py --diff old.json new.json   # markdown changelog between two builds
"""
import argparse
import datetime
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

API = "https://api.github.com"
REPO_JSON = "https://switch.cdn.fortheusers.org/repo.json"
APPSTORE_KEYS = ["title", "description", "author", "version", "license",
                 "url", "category", "details", "changelog"]

_releases = {}  # in-memory cache: repo -> release json
_token = {"value": None}  # GitHub token, set in main()
_disk = {"dir": None, "ttl": 0}  # on-disk release cache, set in main()


class BuildError(Exception):
    pass


class NotModified(Exception):
    """Server answered 304 to a conditional request: our cached copy is current."""


_tls = threading.local()


def log(msg=""):
    # inside a fetch worker, buffer lines so parallel tools don't interleave output
    buf = getattr(_tls, "buf", None)
    if buf is not None:
        buf.append(msg)
    else:
        print(msg, flush=True)


# ----------------------------------------------------------------- network

def http_open(url, api=False, extra=None):
    headers = {"User-Agent": "sdcard-builder"}
    # only ever send the token to GitHub's API, never to other hosts
    if _token["value"] and url.startswith(API + "/"):
        headers["Authorization"] = f"Bearer {_token['value']}"
    headers.update(extra or {})
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified() from e
        hint = " (rate limited? set GITHUB_TOKEN)" if e.code in (403, 429) else ""
        raise BuildError(f"HTTP {e.code} for {url}{hint}") from e
    except urllib.error.URLError as e:
        raise BuildError(f"network error for {url}: {e.reason}") from e


def latest_release(repo):
    if repo in _releases:
        return _releases[repo]
    cache_file = None
    if _disk["dir"] is not None:
        cache_file = _disk["dir"] / "releases" / (repo.replace("/", "__") + ".json")
        if cache_file.exists() and time.time() - cache_file.stat().st_mtime < _disk["ttl"]:
            try:
                _releases[repo] = json.loads(cache_file.read_text())
                return _releases[repo]
            except ValueError:
                pass  # corrupt cache file, refetch
    try:
        with http_open(f"{API}/repos/{repo}/releases/latest", api=True) as r:
            data = json.load(r)
    except BuildError as e:
        # network/rate-limit failure: fall back to an expired cache entry if we have one
        if cache_file is not None and cache_file.exists():
            log(f"  warning: {e}; using stale cached release info for {repo}")
            _releases[repo] = json.loads(cache_file.read_text())
            return _releases[repo]
        raise
    _releases[repo] = data
    if cache_file is not None:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
    return data


def resolve_asset(repo, pattern):
    rel = latest_release(repo)
    assets = rel.get("assets", [])
    if not assets:
        raise BuildError(f"{repo} {rel.get('tag_name')}: release has no assets")
    if pattern is None:
        return assets[0]
    hits = [a for a in assets if fnmatch.fnmatchcase(a["name"], pattern)]
    if not hits:
        names = ", ".join(a["name"] for a in assets)
        raise BuildError(f"{repo} {rel.get('tag_name')}: no asset matches '{pattern}'. "
                         f"Available: {names}")
    if len(hits) > 1:
        log(f"  warning: '{pattern}' matched {len(hits)} assets, using {hits[0]['name']}")
    return hits[0]


def download(url, name, cache_dir, use_cache, revalidate=False, tool="misc"):
    """Fetch url into the cache. Returns (path, how) with how = cached/unchanged/downloaded.

    Release assets have versioned URLs, so a cached copy is always current. Branch
    archives (revalidate=True) change under the same URL: we ask the server with the
    stored ETag / Last-Modified and only re-download when they say it changed.

    Layout: <cache>/downloads/<tool>/<file> plus <file>.meta (url + validators).
    A new release has a new URL, so it replaces the old file instead of piling up."""
    folder = cache_dir / "downloads" / re.sub(r"[^\w.+-]", "_", tool)
    target, meta_file = folder / name, folder / (name + ".meta")
    try:
        meta = json.loads(meta_file.read_text())
    except (OSError, ValueError):
        meta = {}
    cached = use_cache and target.exists() and meta.get("url") == url
    if cached and not revalidate:
        return target, "cached"

    extra = {}
    if cached:
        if meta.get("etag"):
            extra["If-None-Match"] = meta["etag"]
        elif meta.get("last_modified"):
            extra["If-Modified-Since"] = meta["last_modified"]

    folder.mkdir(parents=True, exist_ok=True)
    try:
        resp = http_open(url, extra=extra)
    except NotModified:
        return target, "unchanged"
    except BuildError as e:
        if cached:  # can't reach the server: better a possibly-stale copy than a failed build
            log(f"  warning: {e}; using cached copy of {name}")
            return target, "cached"
        raise
    part = target.with_suffix(target.suffix + ".part")
    with resp, open(part, "wb") as f:
        shutil.copyfileobj(resp, f)
        meta = {"url": url, "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified")}
    part.replace(target)
    meta_file.write_text(json.dumps(meta))
    return target, "downloaded"


# ----------------------------------------------------------------- install

def zip_members(z, prefix=None):
    """Yield (member, path parts) for the files wanted from a zip; 'prefix' (the
    manifest's `from`) selects one folder inside it and is stripped from the paths."""
    pp = prefix.strip("/").split("/") if prefix else []
    for m in z.infolist():
        parts = list(PurePosixPath(m.filename).parts)
        if pp:
            if len(parts) <= len(pp) or not all(
                    fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pp)):
                continue
            parts = parts[len(pp):]
        if parts:
            yield m, parts


def unzip(zpath, dest, prefix=None):
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    count = 0
    with zipfile.ZipFile(zpath) as z:
        for m, parts in zip_members(z, prefix):
            target = (dest / Path(*parts)).resolve()
            if root != target and root not in target.parents:
                raise BuildError(f"unsafe path in zip: {m.filename}")
            if m.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(m) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    if count == 0:
        raise BuildError(f"{zpath.name}: nothing extracted"
                         + (f" (from='{prefix}' matched no files)" if prefix else ""))


def asset_mode(asset, file):
    return asset.get("mode") or ("unzip" if file.name.lower().endswith(".zip") else "copy")


def content_digest(asset, file):
    """Hash of what actually gets installed (not of the archive bytes), so it only
    changes when the wanted files change, e.g. one folder of a whole-repo archive."""
    if asset_mode(asset, file) != "unzip":
        return sha256_file(file)
    rows = []
    with zipfile.ZipFile(file) as z:
        for m, parts in zip_members(z, asset.get("from")):
            if m.is_dir():
                continue
            h = hashlib.sha256()
            with z.open(m) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            rows.append(f"{'/'.join(parts)} {h.hexdigest()}")
    return hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()


def install(asset, file, build):
    dest = build / asset.get("dest", ".")
    mode = asset_mode(asset, file)
    if mode == "unzip":
        unzip(file, dest, asset.get("from"))
    elif mode == "copy":
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, dest / asset.get("rename", file.name))
    else:
        raise BuildError(f"unknown mode '{mode}'")


def matches(build, pattern):
    return sorted(Path(h) for h in glob.glob(glob.escape(str(build)) + "/" + pattern))


def one_match(build, pattern):
    hits = matches(build, pattern)
    if len(hits) != 1:
        found = [str(h.relative_to(build)) for h in hits] or "nothing"
        raise BuildError(f"post: '{pattern}' must match exactly 1 path, found {found}")
    return hits[0]


def run_post(ops, build):
    for op in ops:
        kind, *args = op
        if kind == "touch":
            p = build / args[0]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.touch()
        elif kind in ("cp", "mv"):
            src, dst = one_match(build, args[0]), build / args[1]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                # folders merge into an existing destination instead of nesting inside it
                shutil.copytree(src, dst, dirs_exist_ok=True)
                if kind == "mv":
                    shutil.rmtree(src)
            elif kind == "cp":
                shutil.copy2(src, dst)
            else:
                shutil.move(str(src), str(dst))
        elif kind == "rm":
            hits = matches(build, args[0])
            if not hits:
                raise BuildError(f"post: rm '{args[0]}' matched nothing")
            for h in hits:
                shutil.rmtree(h) if h.is_dir() else h.unlink()
        else:
            raise BuildError(f"unknown post op '{kind}'")


def fetch_tool(tool, args, cache_dir):
    """Network half: resolve + download every asset. Safe to run in parallel.
    Returns (items, buffered log lines, error or None)."""
    _tls.buf = []
    items, err = [], None
    try:
        repo = tool.get("repo")
        for a in tool["asset"]:
            version = None
            if "url" in a:
                url = a["url"]
                fname, revalidate = a.get("name") or url.rstrip("/").split("/")[-1], True
            else:
                if not repo:
                    raise BuildError("asset has no 'url' and tool has no 'repo'")
                gh = resolve_asset(repo, a.get("match"))
                url, fname, revalidate = gh["browser_download_url"], gh["name"], False
                version = latest_release(repo).get("tag_name")
            file, how = (None, None) if args.dry_run else download(
                url, fname, cache_dir, not args.no_cache, revalidate, tool["name"])
            log(f"  {fname} -> {a.get('dest', '.')}"
                + (f"  (rename {a['rename']})" if "rename" in a else "")
                + (f"  [{how}]" if how else ""))
            items.append((a, file, {"name": fname, "url": url, "version": version,
                                    "sha256": sha256_file(file) if file else None,
                                    "content": content_digest(a, file) if file else None}))
    except (BuildError, OSError, zipfile.BadZipFile) as e:
        err = e
    finally:
        lines, _tls.buf = _tls.buf, None
    return items, lines, err


def install_tool(tool, items, args, build):
    """Disk half: always sequential, in manifest order (later tools overwrite earlier)."""
    if not args.dry_run:
        for a, file, _meta in items:
            install(a, file, build)
    for op in tool.get("post", []):
        log(f"  post: {' '.join(op)}")
    if not args.dry_run:
        run_post(tool.get("post", []), build)


# ---------------------------------------------------------------- finalize

def finalize(repos, build, dry):
    log(f"\nfinalize: {len(repos)} repos: {', '.join(repos)}")
    if dry:
        return
    pkg_root = build / "switch/appstore/.get/packages"
    pkg_root.mkdir(parents=True, exist_ok=True)
    try:
        with http_open(REPO_JSON, api=True) as r:
            packages = json.load(r)["packages"]
    except (BuildError, ValueError, KeyError) as e:
        log(f"finalize: could not fetch repo.json: {e}")
        return
    made = 0
    for repo in repos:
        pat = re.compile(r"github\.com/" + re.escape(repo) + r"($|/)", re.I)
        pkg = next((p for p in packages if pat.search(p.get("url") or "")), None)
        if not pkg:
            log(f"finalize: no match for {repo}")
            continue
        d = pkg_root / pkg["name"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "info.json").write_text(
            json.dumps({k: pkg.get(k) for k in APPSTORE_KEYS}, indent=2))
        log(f"finalize: {repo} -> {pkg['name']}")
        made += 1
    log(f"finalize: wrote {made} info.json files")


# ----------------------------------------------------------------- configs

IGNORED = {".DS_Store", ".gitkeep"}


def apply_configs(src, build, dry):
    """Last stage: copy the user's configs/ tree over the build, overwriting."""
    if not src.is_dir():
        log(f"\nconfigs: {src} not found, skipping")
        return []
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.name not in IGNORED]
    log(f"\nconfigs: {len(files)} files from {src}")
    for p in files:
        rel = p.relative_to(src)
        target = build / rel
        note = " (overwrites)" if target.exists() else ""
        log(f"  {rel}{note}")
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    return [p.relative_to(src).as_posix() for p in files]


# ---------------------------------------------------------- build manifest

VOLATILE = ("built", "commit")  # differ on every build, ignored when comparing


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root, skip=()):
    return {p.relative_to(root).as_posix(): sha256_file(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.resolve() not in skip}


def git_commit(here):
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True,
                              text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def tool_record(tool, items):
    metas = [m for _, _, m in items]
    return {"repo": tool.get("repo"),
            "section": tool.get("section"),
            "version": next((m["version"] for m in metas if m["version"]), None),
            "assets": [{"name": m["name"], "url": m["url"], "sha256": m["sha256"],
                        "content": m["content"]} for m in metas]}


def make_manifest(build, records, cfg_files, skip, here):
    files = hash_tree(build, skip)
    return {
        "schema": 1,
        "built": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": git_commit(here),
        "tools": records,
        "configs": {p: files[p] for p in cfg_files if p in files},
        "files": files,
    }


def load_build_manifest(path):
    p = Path(path)
    if not p.exists():
        return {}  # no previous build: everything shows up as new
    try:
        return json.loads(p.read_text())
    except ValueError as e:
        raise SystemExit(f"{path}: not valid JSON ({e})")


def update_manifest(here, build, records, cfg_files):
    """manifest.json = this build. If one already existed, changelog.md = what changed."""
    mpath, cpath = here / "manifest.json", here / "changelog.md"
    old = load_build_manifest(mpath)
    new = make_manifest(build, records, cfg_files, {mpath.resolve(), cpath.resolve()}, here)
    strip = lambda m: {k: v for k, v in m.items() if k not in VOLATILE}
    if old and strip(old) == strip(new):
        log("\nmanifest: no changes since the last build, manifest.json left as is")
        cpath.write_text(diff_manifests(old, new))
        return
    mpath.write_text(json.dumps(new, indent=2) + "\n")
    log(f"\nmanifest -> {mpath} ({len(records)} tools, {len(new['files'])} files)")
    if old:
        cpath.write_text(diff_manifests(old, new))
        log(f"changelog -> {cpath}")
    else:
        log("changelog: no previous manifest.json, so none written (first build)")


def _dict_diff(old, new):
    added = sorted(new.keys() - old.keys())
    removed = sorted(old.keys() - new.keys())
    changed = sorted(k for k in old.keys() & new.keys() if old[k] != new[k])
    return added, removed, changed


def _v(ver):
    return f"v{ver}" if ver and ver[0].isdigit() else ver


def _digest(t):
    # shown hash = sha256 of the downloaded file, so `sha256sum` on the cached file matches
    vals = sorted(a["sha256"] for a in t["assets"])
    d = vals[0] if len(vals) == 1 else hashlib.sha256("\n".join(vals).encode()).hexdigest()
    return f"`{d[:6]}...{d[-6:]}`"


def _label(t):
    return _v(t.get("version")) or _digest(t)


def _fingerprints(o, n):
    # compare extracted content when both manifests have it, else the archive hashes
    key = "content" if all(a.get("content") for a in o["assets"] + n["assets"]) else "sha256"
    return sorted(a[key] for a in o["assets"]), sorted(a[key] for a in n["assets"])


def diff_manifests(old, new):
    """Markdown changelog between two build manifests."""
    ot, nt = old.get("tools", {}), new.get("tools", {})
    out = []

    updated, added, removed = [], [], []
    for name in sorted(nt.keys() & ot.keys()):
        o, n = ot[name], nt[name]
        ov, nv = o.get("version"), n.get("version")
        of, nf = _fingerprints(o, n)
        if ov == nv and of == nf:
            continue
        if ov and ov == nv:
            text = f"{_label(n)} (asset changed)"
        else:
            text = f"{_label(o)} → {_label(n)}"
        updated.append(f"- {name} {text}")
    for name in sorted(nt.keys() - ot.keys()):
        added.append(f"- {name} {_label(nt[name])}")
    for name in sorted(ot.keys() - nt.keys()):
        removed.append(f"- {name} {_label(ot[name])}")

    chg = []
    for title, items in (("Added", added), ("Changed", updated), ("Removed", removed)):
        if items:
            chg += [f"### {title}", *items, ""]

    oc, nc = old.get("configs", {}), new.get("configs", {})
    ca, cr, cm = _dict_diff(oc, nc)
    if ca or cr or cm:
        chg += ["### Configs"]
        chg += [f"- `{p}` (added)" for p in ca] + [f"- `{p}` (modified)" for p in cm]
        chg += [f"- `{p}` (removed)" for p in cr] + [""]

    skip = oc.keys() | nc.keys()
    fa, fr, fm = _dict_diff(old.get("files", {}), new.get("files", {}))
    rows = ([f"- `{p}` (added)" for p in fa if p not in skip]
            + [f"- `{p}` (modified)" for p in fm if p not in skip]
            + [f"- `{p}` (removed)" for p in fr if p not in skip])
    if rows:
        chg += [f"<details><summary>Other file changes ({len(rows)})</summary>", ""] + rows + ["", "</details>", ""]

    out = ["## Changelog"] + (chg or ["No changes.", ""])

    # everything in this release, grouped by the sections in tools.toml
    groups = {}
    for name, t in nt.items():
        groups.setdefault(t.get("section") or "Other", []).append(f"- {name} {_label(t)}")
    out.append("## This release contains")
    for title in sorted(groups, key=lambda t: t == "Other"):  # "Other" last, rest in file order
        out += [f"### {title}", *groups[title], ""]

    return "\n".join(out).rstrip() + "\n"


# -------------------------------------------------------------------- main

SECTION_RE = re.compile(r"^#\s*-{3,}\s*(.+?)\s*-{3,}\s*$")
NAME_RE = re.compile(r'^name\s*=\s*"([^"]*)"')


def parse_sections(text):
    """Map tool name -> section title, from '# ---------- Title ----------' comments."""
    sections, current, pending = {}, None, False
    for line in text.splitlines():
        line = line.strip()
        m = SECTION_RE.match(line)
        if m:
            current = m.group(1)
        elif line.startswith("[["):
            pending = line == "[[tool]]"
        elif pending and (m := NAME_RE.match(line)):
            sections[m.group(1)] = current
            pending = False
    return sections


def load_tools(path):
    text = Path(path).read_text(encoding="utf-8")
    tools = tomllib.loads(text).get("tool", [])
    sections = parse_sections(text)
    seen = set()
    for t in tools:
        if "name" not in t or not t.get("asset"):
            raise SystemExit(f"tools.toml: every tool needs a name and >=1 asset: {t}")
        if t["name"].lower() in seen:
            raise SystemExit(f"tools.toml: duplicate tool name {t['name']}")
        seen.add(t["name"].lower())
        t["section"] = sections.get(t["name"])
    return tools


def csv_list(values):
    return {x.lower() for v in values or [] for x in v.split(",") if x}


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Build the SD card from tools.toml")
    ap.add_argument("build_dir", nargs="?", default="sdcard", help="output dir (default: sdcard)")
    ap.add_argument("--token", metavar="TOKEN",
                    help="GitHub token (or set GITHUB_TOKEN / GH_TOKEN; env is safer, "
                         "a flag shows up in shell history)")
    ap.add_argument("--only", action="append", metavar="A,B", help="run only these tools")
    ap.add_argument("--skip", action="append", metavar="A,B", help="skip these tools")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve all assets via the API, download/write nothing")
    ap.add_argument("--clean", action="store_true", help="wipe build dir first")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore the cache: re-fetch release info and re-download files")
    ap.add_argument("--cache-ttl", type=int, default=600, metavar="SEC",
                    help="how long cached release info stays fresh (default: 600)")
    ap.add_argument("--configs", default=str(here / "configs"), metavar="DIR",
                    help="folder copied over the build as the last step (default: ./configs)")
    ap.add_argument("--no-configs", action="store_true", help="skip the configs step")
    ap.add_argument("-j", "--jobs", type=int, default=1, metavar="N",
                    help="download N tools in parallel (default: 1 = one at a time)")
    ap.add_argument("--cache-dir", default=str(here / ".cache"))
    ap.add_argument("--fail-fast", action="store_true", help="stop at first failing tool")
    ap.add_argument("--zip", metavar="FILE", help="zip the result to FILE")
    ap.add_argument("--no-manifest", action="store_true",
                    help="don't write manifest.json / changelog.md")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"),
                    help="print a markdown changelog between two build manifests and exit")
    ap.add_argument("--list", action="store_true", help="list tools and exit")
    args = ap.parse_args()
    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.diff:
        print(diff_manifests(load_build_manifest(args.diff[0]), load_build_manifest(args.diff[1])), end="")
        return 0

    _token["value"] = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    log("auth: GitHub token (5000 req/h)" if _token["value"]
        else "auth: none (60 req/h; set GITHUB_TOKEN to raise)")
    tools = load_tools(here / "tools.toml")
    if args.list:
        for t in tools:
            log(f"{t['name']:22} {t.get('repo', '-'):40} {'' if t.get('enabled', True) else '(disabled)'}")
        return 0

    only, skip = csv_list(args.only), csv_list(args.skip)
    unknown = (only | skip) - {t["name"].lower() for t in tools}
    if unknown:
        raise SystemExit(f"unknown tool(s): {', '.join(sorted(unknown))} (see --list)")
    selected = [t for t in tools
                if t["name"].lower() not in skip
                and (t["name"].lower() in only if only else t.get("enabled", True))]

    build = Path(args.build_dir).resolve()
    cache_dir = Path(args.cache_dir)
    if not args.no_cache:
        _disk["dir"], _disk["ttl"] = cache_dir, args.cache_ttl
    if args.clean and not args.dry_run and build.exists():
        if build == Path.cwd().resolve() or build in (here / "tools.toml").resolve().parents:
            raise SystemExit(f"refusing to --clean {build}: it contains the manifest or cwd")
        shutil.rmtree(build)
    if not args.dry_run:
        build.mkdir(parents=True, exist_ok=True)

    ok, failed, records = [], [], {}
    pool = futures = None
    if args.jobs > 1:  # start every tool's network work up front; install stays in order
        pool = ThreadPoolExecutor(max_workers=args.jobs)
        futures = {t["name"]: pool.submit(fetch_tool, t, args, cache_dir) for t in selected}
    try:
        for t in selected:
            log(f"[{t['name']}]")
            items, lines, err = (futures[t["name"]].result() if futures
                                 else fetch_tool(t, args, cache_dir))
            for line in lines:
                log(line)
            try:
                if err:
                    raise err
                install_tool(t, items, args, build)
                ok.append(t)
                records[t["name"]] = tool_record(t, items)
            except (BuildError, zipfile.BadZipFile, OSError) as e:
                log(f"  FAILED: {e}")
                failed.append(t["name"])
                if args.fail_fast:
                    break
    finally:
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)

    finalize([t["repo"] for t in ok if t.get("repo")], build, args.dry_run)

    cfg_files = []
    if not args.no_configs:
        cfg_files = apply_configs(Path(args.configs), build, args.dry_run)

    if not args.dry_run and not args.no_manifest:
        if failed or only or skip:
            log("\nmanifest: incomplete build (failures or --only/--skip), manifest.json not updated")
        else:
            update_manifest(here, build, records, cfg_files)

    if args.zip and not args.dry_run:
        out = Path(args.zip)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.make_archive(str(out.with_suffix("")), "zip", build)
        log(f"zipped -> {out}")

    log(f"\n{len(ok)} ok, {len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())


