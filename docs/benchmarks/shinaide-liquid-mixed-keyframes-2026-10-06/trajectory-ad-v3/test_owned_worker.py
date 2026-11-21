import copy
import unittest
from pathlib import Path
from types import SimpleNamespace
from owned_worker import Identity, check_observation, request_stop

class SentinelGateTest(unittest.TestCase):
    def setUp(self):
        self.identity=Identity(123,1002,99,"987","/owned","/python",("/python","/probe","--worker"))
        self.observation={"identity":self.identity,"sentinel":{"regular":True,"symlink":False,"mode":0o600,"uid":1002,"deviceInode":(1,2)},
                          "expectedDeviceInode":(1,2),"heldFdDeviceInode":(1,2),"heldFdPath":"/owned/sentinel",
                          "expectedPath":"/owned/sentinel","owners":[123],"children":[]}
    def run_signal(self,observation):
        calls=[]
        def observer(*unused):return observation
        def runner(argv,**unused):
            calls.append(argv)
            return SimpleNamespace(returncode=0)
        result=request_stop(self.identity,Path("/owned/sentinel"),7,(1,2),"TERM",observer,runner)
        return result,calls
    def test_exact_owner_uses_only_fuser(self):
        result,calls=self.run_signal(self.observation)
        self.assertTrue(result["sent"])
        self.assertEqual(calls,[["fuser","-k","-TERM","/owned/sentinel"]])
    def test_foreign_holder_refused(self):
        self.observation["owners"]=[456]
        result,calls=self.run_signal(self.observation)
        self.assertFalse(result["sent"]);self.assertEqual(calls,[])
    def test_split_holders_refused(self):
        self.observation["owners"]=[123,456]
        result,calls=self.run_signal(self.observation)
        self.assertFalse(result["sent"]);self.assertEqual(calls,[])
    def test_inode_replacement_refused(self):
        self.observation["sentinel"]["deviceInode"]=(1,3)
        self.assertTrue(check_observation(self.identity,self.observation))
    def test_stale_held_fd_refused(self):
        self.observation["heldFdDeviceInode"]=(1,3)
        self.assertTrue(check_observation(self.identity,self.observation))
    def test_startticks_reuse_refused(self):
        self.observation["identity"]=Identity(123,1002,99,"999","/owned","/python",self.identity.argv)
        result,calls=self.run_signal(self.observation)
        self.assertFalse(result["sent"]);self.assertEqual(calls,[])
    def test_process_identity_fields_all_checked(self):
        for field,value in (("pid",124),("uid",1003),("ppid",98),("cwd","/other"),("executable","/other/python"),("argv",("/python","different"))):
            with self.subTest(field=field):
                obs=copy.deepcopy(self.observation)
                values=dict(self.identity.__dict__);values[field]=value;obs["identity"]=Identity(**values)
                self.assertTrue(check_observation(self.identity,obs))
    def test_descendants_refused(self):
        self.observation["children"]=[124]
        result,calls=self.run_signal(self.observation)
        self.assertFalse(result["sent"]);self.assertEqual(calls,[])
    def test_symlink_and_permissions_refused(self):
        for key,value in (("symlink",True),("regular",False),("mode",0o644),("uid",1003)):
            with self.subTest(key=key):
                obs=copy.deepcopy(self.observation);obs["sentinel"][key]=value
                self.assertTrue(check_observation(self.identity,obs))
    def test_missing_query_refused(self):
        self.observation["queryError"]="OSError"
        result,calls=self.run_signal(self.observation)
        self.assertFalse(result["sent"]);self.assertEqual(calls,[])
    def test_second_signal_revalidates(self):
        calls=[];observations=[self.observation,dict(self.observation,owners=[456])]
        def observer(*unused):return observations.pop(0)
        def runner(argv,**unused):calls.append(argv);return SimpleNamespace(returncode=0)
        first=request_stop(self.identity,Path("/owned/sentinel"),7,(1,2),"TERM",observer,runner)
        second=request_stop(self.identity,Path("/owned/sentinel"),7,(1,2),"KILL",observer,runner)
        self.assertTrue(first["sent"]);self.assertFalse(second["sent"])
        self.assertEqual(calls,[["fuser","-k","-TERM","/owned/sentinel"]])
    def test_unknown_signal_rejected(self):
        with self.assertRaises(ValueError):request_stop(self.identity,Path("/owned/sentinel"),7,(1,2),"INT")
if __name__=="__main__":unittest.main()
