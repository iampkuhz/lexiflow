import subprocess,tempfile,unittest
from pathlib import Path
from unittest import mock
from scripts.gates import change_verify
class T(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(); self.r=Path(self.t.name); subprocess.run(['git','init','-q'],cwd=self.r,check=True); subprocess.run(['git','config','user.email','a@b'],cwd=self.r,check=True); subprocess.run(['git','config','user.name','a'],cwd=self.r,check=True); (self.r/'a.txt').write_text('a'); subprocess.run(['git','add','.'],cwd=self.r,check=True); subprocess.run(['git','commit','-qm','i'],cwd=self.r,check=True); (self.r/'a.txt').write_text('b'); (self.r/'extra.txt').write_text('x')
 def tearDown(self): self.t.cleanup()
 @mock.patch('scripts.gates.change_verify.execute_checks',return_value={'run_status':'PASS','checks':[]})
 @mock.patch('scripts.gates.change_verify.compile_local_plan',return_value={'checks':[{'check_id':'x'}]})
 def test_no_context_and_unexpected_are_advisory(self,p,e):
  v=change_verify.verify(self.r); self.assertEqual(v['execution_result'],'PASS'); self.assertEqual(v['scope_review']['kind'],'no-context'); self.assertTrue(v['scope_review']['unexpected'])
 @mock.patch('scripts.gates.change_verify.compile_local_plan',return_value={'checks':[]})
 def test_no_selected_is_blocked_not_exception(self,p): self.assertEqual(change_verify.verify(self.r)['result'],'BLOCKED')
if __name__=='__main__': unittest.main()
