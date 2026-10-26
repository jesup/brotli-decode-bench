#!/usr/bin/env python3
"""Download corpus files listed in corpus/manifest.tsv and verify their sha256.

Usage: fetch_corpus.py [--set web|silesia|all] [--corpus-dir DIR]
"""
import argparse
import hashlib
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_manifest(path):
    entries = []
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            cols = line.split("\t")
            cols += [""] * (8 - len(cols))
            entries.append(dict(zip(
                ["set", "label", "path", "size", "sha256", "url", "zip_member", "zip_sha256"],
                cols)))
    return entries


def download(url, dest):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    subprocess.run(["curl", "-fL", "--retry", "3", "-sS", "-o", tmp, url], check=True)
    os.replace(tmp, dest)


def fetch(entry, corpus_dir):
    dest = os.path.join(corpus_dir, entry["path"])
    if os.path.exists(dest) and sha256_file(dest) == entry["sha256"]:
        return "cached"
    if entry["zip_member"]:
        zip_path = os.path.join(corpus_dir, "silesia", ".downloads", entry["zip_member"] + ".zip")
        if not (os.path.exists(zip_path) and sha256_file(zip_path) == entry["zip_sha256"]):
            download(entry["url"], zip_path)
            actual = sha256_file(zip_path)
            if actual != entry["zip_sha256"]:
                raise SystemExit(f"{zip_path}: sha256 mismatch {actual}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        tmp = dest + ".tmp"
        with open(tmp, "wb") as out:
            subprocess.run(["unzip", "-p", zip_path, entry["zip_member"]], stdout=out, check=True)
        os.replace(tmp, dest)
    else:
        download(entry["url"], dest)
    actual = sha256_file(dest)
    if actual != entry["sha256"]:
        os.remove(dest)
        raise SystemExit(f"{dest}: sha256 mismatch {actual}")
    if os.path.getsize(dest) != int(entry["size"]):
        raise SystemExit(f"{dest}: size mismatch")
    return "fetched"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="web", choices=["web", "silesia", "all"])
    ap.add_argument("--corpus-dir", default=os.path.join(ROOT, "corpus"))
    args = ap.parse_args()
    entries = read_manifest(os.path.join(ROOT, "corpus", "manifest.tsv"))
    for e in entries:
        if args.set != "all" and e["set"] != args.set:
            continue
        status = fetch(e, args.corpus_dir)
        print(f"{status:8} {e['label']}", file=sys.stderr)


if __name__ == "__main__":
    main()
