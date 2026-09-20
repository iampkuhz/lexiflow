"""Final-diff self-review and targeted local verification; never a commit gate."""
from __future__ import annotations
import argparse,json,subprocess,sys,uuid
from pathlib import Path, PurePosixPath
if __package__ in (None,""): sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.gates.change_context import ChangeContext,ChangeContextError
from scripts.gates.local_verify import LocalVerifyError,compile_local_plan
from scripts.gates.executor import execute_checks
from scripts.gates.planner import canonical_json_bytes
class ChangeVerifyError(ValueError): pass
def _git(root,*args):
 r=subprocess.run(["git",*args],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if r.returncode: raise ChangeVerifyError("git-unavailable: "+r.stderr.strip())
 return r.stdout
def resolve_base(root,explicit=None):
 if explicit:return explicit
 r=subprocess.run(["git","rev-parse","--verify","@{upstream}"],cwd=root,text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 if r.returncode==0:
  m=subprocess.run(["git","merge-base","HEAD",r.stdout.strip()],cwd=root,text=True,stdout=subprocess.PIPE)
  if m.returncode==0:return m.stdout.strip()
 return _git(root,"rev-parse","HEAD").strip()
def changed_paths(root,base):
 out=_git(root,"diff","--name-only","-z","--diff-filter=ACMRD",base,"--")+_git(root,"ls-files","--others","--exclude-standard","-z")
 vals=[x for x in out.split("\0") if x]
 if any(x.startswith("/") or ".." in PurePosixPath(x).parts for x in vals): raise ChangeVerifyError("unsafe changed path")
 return sorted(set(vals))
def _in_expected(path,roots): return any(path==r or path.startswith(r.rstrip("/")+"/") for r in roots)
def verify(root=".",base=None,mode="incremental"):
 repo=Path(root).resolve(); base=resolve_base(repo,base); changed=changed_paths(repo,base)
 try: ctx=ChangeContext(repo).read(); roots=ctx["expected_roots"]; kind="expected"
 except ChangeContextError: roots=[]; kind="no-context"
 expected=[p for p in changed if roots and _in_expected(p,roots)]
 unexpected=[p for p in changed if not roots or not _in_expected(p,roots)]
 review={"kind":kind,"changed_files":changed,"expected":expected,"unexpected":unexpected,"advisories":["review unexpected files"] if unexpected else []}
 if not changed:return {"result":"PASS","execution_result":"PASS","base":base,"scope_review":review}
 try: plan=compile_local_plan(repo,changed,mode)
 except LocalVerifyError as e:return {"result":e.result,"execution_result":e.result,"reasons":[e.code],"detail":e.detail,"base":base,"scope_review":review}
 execution=execute_checks(plan,repo_root=str(repo)) if plan["checks"] else {"run_status":"BLOCKED","reason":"no-selected-checks"}
 result=execution.get("run_status","BLOCKED"); run_id=str(uuid.uuid4()); d=repo/"tmp/quality/change-verification"/run_id; d.mkdir(parents=True,exist_ok=False)
 (d/"plan.json").write_bytes(canonical_json_bytes(plan)); (d/"execution.json").write_text(json.dumps(execution,ensure_ascii=False,indent=2))
 summary={"schema_version":"lexiflow.change-verification.v1","result":result,"execution_result":result,"base":base,"scope_review":review,"run_id":run_id}
 (d/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)); return summary
def main(argv=None):
 p=argparse.ArgumentParser(); p.add_argument("--repo-root",default="."); p.add_argument("--base"); p.add_argument("--mode",choices=["incremental","full"],default="incremental"); a=p.parse_args(argv)
 try: out=verify(a.repo_root,a.base,a.mode)
 except ChangeVerifyError as e: out={"result":"BLOCKED","execution_result":"BLOCKED","detail":str(e)}
 print(json.dumps(out,ensure_ascii=False,sort_keys=True)); return {"PASS":0,"BLOCKED":2,"FAIL":1}.get(out.get("result"),2)
if __name__=="__main__": raise SystemExit(main())
