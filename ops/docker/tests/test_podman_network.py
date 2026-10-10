"""验证本机直连探测的预算与失败边界，不代表真实 Podman 运行。"""
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]


class NetworkTest(unittest.TestCase):
    def node(self, body):
        result = subprocess.run([shutil.which('node'), '--input-type=module', '-e', body], cwd=ROOT, capture_output=True, text=True, timeout=12)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_total_budget_and_environment_proxy_bypass(self):
        self.node(r'''
import {runtime} from './ops/podman/commands/diagnose-podman-installation.mjs';import http from 'node:http';import assert from 'node:assert/strict';
process.env.HTTP_PROXY='http://127.0.0.1:1';process.env.HTTPS_PROXY=process.env.HTTP_PROXY;process.env.ALL_PROXY=process.env.HTTP_PROXY;process.env.NODE_USE_ENV_PROXY='1';
let hang=false;const server=http.createServer((req,res)=>{
 if(hang)return;
 res.end(JSON.stringify(req.url.endsWith('readiness')?{status:'UP'}:{softwareVersion:'0.1.0',apiContract:'caption-hints.v2',mode:'formal',ready:true,reason:'OK'}));
});await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{
 assert.equal((await runtime(server.address().port,'0.1.0',1000))[0],'PASS');hang=true;
 const start=performance.now();await assert.rejects(runtime(server.address().port,'0.1.0',40));
 assert.ok(performance.now()-start<1000);
}finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
''')

    def test_malformed_or_aborted_response_is_rejected(self):
        self.node(r'''
import {runtime} from './ops/podman/commands/diagnose-podman-installation.mjs';import http from 'node:http';import assert from 'node:assert/strict';
const server=http.createServer((req,res)=>{res.writeHead(200,{'Content-Length':'100'});res.write('{');setTimeout(()=>res.destroy(),10);});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
try{await assert.rejects(runtime(server.address().port,'0.1.0',300));}
finally{server.closeAllConnections();await new Promise(r=>server.close(r));}
''')

if __name__ == '__main__': unittest.main()
