import tempfile,unittest
from pathlib import Path
from scripts.gates.change_context import ChangeContext
class T(unittest.TestCase):
 def test_optional_and_evolvable(self):
  with tempfile.TemporaryDirectory() as x:
   c=ChangeContext(x); self.assertRaises(Exception,c.read); c.update(['src']); d=c.update(['docs'],reason='analysis'); self.assertEqual(d['expected_roots'],['docs','src']); self.assertEqual(d['revision'],1)
if __name__=='__main__': unittest.main()
