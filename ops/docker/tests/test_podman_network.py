"""验证发布网络修复与直连探测的失败边界，不代表真实 Podman 运行。"""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


class NetworkTest(unittest.TestCase):
    def node(self, body):
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', body], cwd=ROOT, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_repair_resume_and_tamper_matrix(self):
        self.node(r'''
import {repairNetwork,networkTemplates} from './ops/podman/network-repair.mjs';
import fs from 'node:fs';import os from 'node:os';import path from 'node:path';import crypto from 'node:crypto';import assert from 'node:assert/strict';
const template=fs.readFileSync('ops/podman/compose.validation.yaml','utf8');
const {current,legacy}=networkTemplates(template,18080,15432);
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
for(const mode of ['normal','before-rename','after-rename','tamper','other-file','read-only','source','bad-journal']) {
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lf-network-'));
 try {
 const state={phase:mode==='source'?'source':'initialized',apiPort:18080,dbPort:15432,digests:{'compose.yaml':hash(legacy),secret:'unchanged'}};
 if(['before-rename','after-rename','bad-journal'].includes(mode)) state.networkRepair={from:hash(legacy),to:mode==='bad-journal'?'bad':hash(current)};
 const file=path.join(dir,'compose.yaml');fs.writeFileSync(file,mode==='after-rename'?current:mode==='tamper'?'foreign':legacy);
 const initial=fs.readFileSync(file,'utf8');let writes=0;
 const options={dir,state,template,enabled:mode!=='read-only',save:()=>writes++,actualDigests:()=>({'compose.yaml':hash(fs.readFileSync(file)),secret:mode==='other-file'?'changed':'unchanged'})};
 if(['tamper','other-file','bad-journal'].includes(mode)) {assert.throws(()=>repairNetwork(options));assert.equal(fs.readFileSync(file,'utf8'),initial);assert.equal(writes,0);}
 else if(['read-only','source'].includes(mode)) {assert.equal(repairNetwork(options),false);assert.equal(writes,0);assert.equal(fs.readFileSync(file,'utf8'),legacy);}
 else {assert.equal(repairNetwork(options),true);assert.equal(fs.readFileSync(file,'utf8'),current);assert.equal(state.digests.secret,'unchanged');assert.equal(state.networkRepair,undefined);assert.equal(repairNetwork(options),false);}
 } finally {fs.rmSync(dir,{recursive:true});}
}
''')

    def test_runtime_total_budget_and_environment_proxy_bypass(self):
        self.node(r'''
import {runtime} from './ops/podman/doctor.mjs';import http from 'node:http';import assert from 'node:assert/strict';
process.env.HTTP_PROXY='http://127.0.0.1:1';process.env.HTTPS_PROXY=process.env.HTTP_PROXY;process.env.ALL_PROXY=process.env.HTTP_PROXY;process.env.NODE_USE_ENV_PROXY='1';
let hang=false;const server=http.createServer((req,res)=>{
 if(hang)return;
 res.end(JSON.stringify(req.url.endsWith('readiness')?{status:'UP'}:{softwareVersion:'0.1.0',apiContract:'caption-hints.v1',mode:'formal',ready:true,reason:'OK'}));
});await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{
 assert.equal((await runtime(server.address().port,'0.1.0',1000))[0],'PASS');hang=true;
 const start=performance.now();await assert.rejects(runtime(server.address().port,'0.1.0',40));
 assert.ok(performance.now()-start<1000);
}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
''')

    def test_malformed_or_aborted_response_is_rejected(self):
        self.node(r'''
import {runtime} from './ops/podman/doctor.mjs';import http from 'node:http';import assert from 'node:assert/strict';
const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Length':'100'});res.write('{');setTimeout(()=>res.destroy(),10);});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{await assert.rejects(runtime(server.address().port,'0.1.0',300));}
finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
''')

if __name__ == '__main__': unittest.main()
