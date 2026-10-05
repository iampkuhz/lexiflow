#!/bin/sh
set -eu

usage() { printf '%s\n' 'usage: fetch-ecdict.sh ABSOLUTE_KIT_ROOT ABSOLUTE_OUTPUT_PARENT' >&2; exit 2; }
[ "$#" -eq 2 ] || usage
KIT_ROOT=$1
OUTPUT_PARENT=$2
case "$KIT_ROOT" in /*) ;; *) usage ;; esac
case "$OUTPUT_PARENT" in /*) ;; *) usage ;; esac
[ -d "$KIT_ROOT" ] && [ -d "$OUTPUT_PARENT" ] || usage
KIT_ROOT=$(CDPATH= cd -- "$KIT_ROOT" && pwd -P)
OUTPUT_PARENT=$(CDPATH= cd -- "$OUTPUT_PARENT" && pwd -P)
LOCK=$KIT_ROOT/ops/dataset/ecdict-source.lock.json
GENERATOR=$KIT_ROOT/scripts/environment/ecdict_bundle.py
[ -f "$LOCK" ] && [ ! -L "$LOCK" ] && [ -f "$GENERATOR" ] && [ ! -L "$GENERATOR" ] || exit 1

# The script owns only this newly-created directory. A signal/failure can never
# remove caller data or a previously successful output.
OUT=$(mktemp -d "$OUTPUT_PARENT/ecdict-source.XXXXXXXX")
chmod 755 "$OUT"
SUCCESS=0
cleanup() { if [ "$SUCCESS" -ne 1 ]; then rm -rf -- "$OUT"; fi; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
REPO=$OUT/.source-repo
mkdir "$REPO"

# Validate the public source identity and exact lock shape before invoking git.
python3 -I - "$LOCK" <<'PY'
import json, re, sys
from pathlib import Path
d=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert d.get("schema_version")==1
assert d.get("repository_url")=="https://github.com/skywind3000/ECDICT.git"
assert d.get("commit")=="bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
assert set(d.get("files",{}))=={"ecdict.csv","stardict.7z","LICENSE"}
assert all(re.fullmatch(r"[0-9a-f]{64}", x) for x in d["files"].values())
assert d.get("archive_member")=={"archive":"stardict.7z","member":"stardict.csv","sha256":"88fce01e0a30524192a62e363d47eeb036fa17820d5826121b3b419fd67a3996","bytes":232668349}
PY

git -C "$REPO" init -q
git -C "$REPO" remote add origin https://github.com/skywind3000/ECDICT.git
git -C "$REPO" fetch --depth=1 origin bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b
git -C "$REPO" checkout --detach FETCH_HEAD
[ "$(git -C "$REPO" remote get-url origin)" = 'https://github.com/skywind3000/ECDICT.git' ]
[ "$(git -C "$REPO" rev-parse HEAD)" = 'bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b' ]

# Verify the locked archive first, then extract exactly one bounded member.
python3 -I - "$LOCK" "$REPO" <<'PY'
import hashlib,json,pathlib,shutil,subprocess,sys
lock=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")); repo=pathlib.Path(sys.argv[2])
archive=repo/"stardict.7z"
h=hashlib.sha256()
with archive.open("rb") as f:
    for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
if h.hexdigest()!=lock["files"]["stardict.7z"]: raise SystemExit("locked archive hash mismatch")
member=lock["archive_member"]
extractor=shutil.which("7z") or shutil.which("7zz")
if not extractor: raise SystemExit("7z/7zz is required")
proc=subprocess.Popen([extractor,"x","-so",str(archive),member["member"]],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
assert proc.stdout is not None
data=proc.stdout.read(member["bytes"]+1)
if len(data)>member["bytes"]:
    proc.kill(); proc.wait(); raise SystemExit("archive member exceeds locked size")
if proc.wait()!=0 or len(data)!=member["bytes"] or hashlib.sha256(data).hexdigest()!=member["sha256"]:
    raise SystemExit("locked archive member verification failed")
target=repo/member["member"]
with target.open("xb") as f: f.write(data)
target.chmod(0o644)
PY

python3 -I "$GENERATOR" --source-repo "$REPO" --output "$OUT/ecdict-core-source.zip"

# Verify every archive member and manifest digest before publishing the CSV.
python3 -I - "$OUT" <<'PY'
import csv, hashlib, io, json, pathlib, stat, sys, zipfile
root=pathlib.Path(sys.argv[1]); archive=root/"ecdict-core-source.zip"
expected={"stardict.csv","source-rows.csv","LICENSE","manifest.json"}
with zipfile.ZipFile(archive) as z:
    infos=z.infolist()
    assert len(infos)==len(expected) and {i.filename for i in infos}==expected
    assert all(not i.is_dir() and not (i.external_attr >> 16 & 0o170000)==stat.S_IFLNK for i in infos)
    raw={n:z.read(n) for n in expected}
manifest=json.loads(raw["manifest.json"])
assert manifest.get("schema_version")==2
assert manifest.get("upstream",{}).get("repository_url")=="https://github.com/skywind3000/ECDICT.git"
assert manifest.get("upstream",{}).get("commit")=="bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b"
assert manifest.get("members")=={n:{"bytes":len(raw[n]),"sha256":hashlib.sha256(raw[n]).hexdigest()} for n in expected-{"manifest.json"}}
assert manifest.get("csv_columns")==["word","translation","oxford","tag","bnc","frq","exchange"]
rows=csv.reader(io.TextIOWrapper(io.BytesIO(raw["stardict.csv"]),encoding="utf-8-sig",newline=""))
assert tuple(next(rows))==tuple(manifest["csv_columns"])
assert next(rows, None) is not None
# Publish through private staging; rename the CSV only after all checks pass.
(root/".stardict.csv.tmp").write_bytes(raw["stardict.csv"])
(root/"source-summary.json").write_text(json.dumps({"source":"public","upstream":manifest["upstream"],"selection":manifest["selection"],"csv_sha256":hashlib.sha256(raw["stardict.csv"]).hexdigest()},sort_keys=True,indent=2)+"\n",encoding="utf-8")
(root/".stardict.csv.tmp").replace(root/"stardict.csv")
for name in ("ecdict-core-source.zip", "source-summary.json", "stardict.csv"):
    (root/name).chmod(0o644)
(root/"source-summary.json").chmod(0o644)
root.chmod(0o755)
PY
rm -rf -- "$REPO" "$OUT/ecdict-core-source.report"
printf 'dataset_dir=%s\nsource=public\n' "$OUT"
python3 -I - "$OUT/stardict.csv" <<'PY'
import hashlib, pathlib, sys
p=pathlib.Path(sys.argv[1]); h=hashlib.sha256(p.read_bytes()).hexdigest()
print(f"stardict.csv_sha256={h}")
PY
SUCCESS=1
