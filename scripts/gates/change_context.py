"""Optional, evolvable intent context for change self-review."""
from __future__ import annotations
import argparse, json, os, tempfile
from datetime import UTC, datetime
from pathlib import Path
SCHEMA="lexiflow.change-context.v1"
def _now(): return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00","Z")
class ChangeContextError(ValueError): pass
class ChangeContext:
 def __init__(self,root): self.root=Path(root).resolve(); self.path=self.root/"tmp/quality/change-context/active.json"
 def read(self):
  if not self.path.is_file(): raise ChangeContextError("no change context")
  try: d=json.loads(self.path.read_text())
  except (OSError,json.JSONDecodeError) as e: raise ChangeContextError("invalid change context") from e
  if d.get("schema")!=SCHEMA or not isinstance(d.get("expected_roots"),list) or not isinstance(d.get("events"),list): raise ChangeContextError("invalid change context")
  return d
 def update(self,paths,reason="update"):
  roots=sorted(set(str(Path(p)) for p in paths))
  if not roots: raise ChangeContextError("at least one expected path is required")
  try: d=self.read(); d["expected_roots"]=sorted(set(d["expected_roots"])|set(roots)); d["revision"]+=1
  except ChangeContextError:
   d={"schema":SCHEMA,"context_id":os.urandom(8).hex(),"expected_roots":roots,"revision":0,"events":[]}
  d["events"].append({"type":"begin" if not d["events"] else "update","at":_now(),"paths":roots,"reason":reason})
  self.path.parent.mkdir(parents=True,exist_ok=True); fd,n=tempfile.mkstemp(dir=self.path.parent,prefix=".context.")
  with os.fdopen(fd,"w") as f: json.dump(d,f,indent=2); f.write("\n")
  os.replace(n,self.path); return d


def main(argv=None):
 p=argparse.ArgumentParser(prog="scripts/gates/change_context.py")
 p.add_argument("command",choices=("begin","update","show")); p.add_argument("--repo-root",default=".")
 p.add_argument("--paths",nargs="*"); p.add_argument("--reason",default="update")
 a=p.parse_args(argv); context=ChangeContext(a.repo_root)
 try:
  value=context.read() if a.command=="show" else context.update(a.paths or [],a.reason)
  result={"result":"PASS","context":value}
 except ChangeContextError as e: result={"result":"BLOCKED","reason":"change-context-unavailable","detail":str(e)}
 print(json.dumps(result,ensure_ascii=False,sort_keys=True)); return 0 if result["result"]=="PASS" else 2


if __name__=="__main__": raise SystemExit(main())
