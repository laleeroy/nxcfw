#!/usr/bin/env python3
"""Manifest-driven SD card builder.

Reads tools.toml, downloads the latest GitHub release assets and assembles
them into a build directory. Needs Python 3.11+, no third-party packages.

Examples:
  ./build.py                         # build everything into ./sdcard
  ./build.py --jobs 8                # download 8 tools at a time (default: 1)
  ./build.py --dry-run               # resolve every asset, write nothing
  ./build.py --only hekate,DBIPatcher       # iterate on a subset
  ./build.py --clean --zip out/sd.zip
"""
import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import shutil
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


_tls = threading.local()


def log(msg=""):
    # inside a fetch worker, buffer lines so parallel tools don't interleave output
    buf = getattr(_tls, "buf", None)
    if buf is not None:
        buf.append(msg)
    else:
        print(msg, flush=True)


# ----------------------------------------------------------------- network

def http_open(url, api=False):
    headers = {"User-Agent": "sdcard-builder"}
    # only ever send the token to GitHub's API, never to other hosts
    if _token["value"] and url.startswith(API + "/"):
        headers["Authorization"] = f"Bearer {_token['value']}"
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)
    except urllib.error.HTTPError as e:
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


def download(url, name, cache_dir, cacheable):
    folder = cache_dir / hashlib.sha256(url.encode()).hexdigest()[:12]
    target = folder / name
    if cacheable and target.exists():
        return target
    folder.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    with http_open(url) as r, open(part, "wb") as f:
        shutil.copyfileobj(r, f)
    part.replace(target)
    return target


# ----------------------------------------------------------------- install

def unzip(zpath, dest, prefix=None):
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    pp = prefix.strip("/").split("/") if prefix else []
    count = 0
    with zipfile.ZipFile(zpath) as z:
        for m in z.infolist():
            parts = list(PurePosixPath(m.filename).parts)
            if pp:
                if len(parts) <= len(pp) or not all(
                        fnmatch.fnmatchcase(a, b) for a, b in zip(parts, pp)):
                    continue
                parts = parts[len(pp):]
            if not parts:
                continue
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


def install(asset, file, build):
    dest = build / asset.get("dest", ".")
    mode = asset.get("mode") or ("unzip" if file.name.lower().endswith(".zip") else "copy")
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
            if "url" in a:
                url = a["url"]
                fname, cacheable = a.get("name") or url.rstrip("/").split("/")[-1], False
            else:
                if not repo:
                    raise BuildError("asset has no 'url' and tool has no 'repo'")
                gh = resolve_asset(repo, a.get("match"))
                url, fname, cacheable = gh["browser_download_url"], gh["name"], not args.no_cache
            log(f"  {fname} -> {a.get('dest', '.')}"
                + (f"  (rename {a['rename']})" if "rename" in a else ""))
            items.append((a, None if args.dry_run else download(url, fname, cache_dir, cacheable)))
    except (BuildError, OSError) as e:
        err = e
    finally:
        lines, _tls.buf = _tls.buf, None
    return items, lines, err


def install_tool(tool, items, args, build):
    """Disk half: always sequential, in manifest order (later tools overwrite earlier)."""
    if not args.dry_run:
        for a, file in items:
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
        return
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


# -------------------------------------------------------------------- main

def load_manifest(path):
    with open(path, "rb") as f:
        tools = tomllib.load(f).get("tool", [])
    seen = set()
    for t in tools:
        if "name" not in t or not t.get("asset"):
            raise SystemExit(f"manifest: every tool needs a name and >=1 asset: {t}")
        if t["name"].lower() in seen:
            raise SystemExit(f"manifest: duplicate tool name {t['name']}")
        seen.add(t["name"].lower())
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
    ap.add_argument("--manifest", default=str(here / "tools.toml"))
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
    ap.add_argument("--list", action="store_true", help="list tools and exit")
    args = ap.parse_args()
    if args.jobs < 1:
        ap.error("--jobs must be >= 1")

    _token["value"] = args.token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    log("auth: GitHub token (5000 req/h)" if _token["value"]
        else "auth: none (60 req/h; set GITHUB_TOKEN to raise)")
    tools = load_manifest(args.manifest)
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
        if build == Path.cwd().resolve() or build in Path(args.manifest).resolve().parents:
            raise SystemExit(f"refusing to --clean {build}: it contains the manifest or cwd")
        shutil.rmtree(build)
    if not args.dry_run:
        build.mkdir(parents=True, exist_ok=True)

    ok, failed = [], []
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
            except (BuildError, zipfile.BadZipFile, OSError) as e:
                log(f"  FAILED: {e}")
                failed.append(t["name"])
                if args.fail_fast:
                    break
    finally:
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)

    finalize([t["repo"] for t in ok if t.get("repo")], build, args.dry_run)

    if not args.no_configs:
        apply_configs(Path(args.configs), build, args.dry_run)

    if args.zip and not args.dry_run:
        out = Path(args.zip)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.make_archive(str(out.with_suffix("")), "zip", build)
        log(f"zipped -> {out}")

    log(f"\n{len(ok)} ok, {len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

