import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import prepare_meeting_batch as batch


class BatchPreparationTests(unittest.TestCase):
    def test_dry_run_is_offline_and_excludes_answer_checklists(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'ROOT',Path(tmp)), patch.object(batch.urllib.request,'build_opener') as network:
            p=Path(tmp)/'A_原稿.md';p.write_text('客户：预算未批20万。',encoding='utf-8')
            result=batch.inspect([p]);self.assertEqual(result[0]['minimum_requests'],2)
            p.write_text('客户：预算未批20万。\n'*30,encoding='utf-8')
            result=batch.inspect([p])[0]
            self.assertGreater(result['chunks'],1)
            self.assertEqual(result['extract_batches'],1)
            self.assertEqual(result['minimum_requests'],result['chunks']+1)
            self.assertEqual(batch.inspect([p],('board',))[0]['minimum_requests'],1)
            self.assertEqual(result['review_requests'],0)
            self.assertEqual(batch.inspect([p],('clean',))[0]['minimum_requests'],result['chunks'])
            self.assertEqual(batch.inspect([p],('board','clean'))[0]['minimum_requests'],result['chunks']+1)
            bad=Path(tmp)/'核对清单.md';bad.write_text('answers')
            with self.assertRaises(RuntimeError):batch.inspect([bad])
            network.assert_not_called()

    def test_activation_uses_verified_local_contract_and_close_removes_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'RUNTIME',Path(tmp)), patch.object(batch,'read_state',return_value={}), patch.object(batch,'owned_process',return_value=True):
            root=Path(tmp);batch.private_json(root/'local-access.json',{'username':'admin','password':'offline-password'})
            login={'token':'offline-session','user':{'id':'system_default_user'}}
            providers={'data':[{'id':'54fffda3','enabled':True,'base_url':'https://api.deepseek.com/v1','api_key':'offline-model-key','models':['deepseek-flash']}]}
            with patch.object(batch.urllib.request,'build_opener') as factory:
                factory.return_value.open.side_effect=[io.BytesIO(json.dumps(login).encode()),io.BytesIO(b'{"processing_protocol":"sales-board-first-v1","workflow":"clean-then-board-v1"}'),io.BytesIO(json.dumps(providers).encode())]
                batch.activate([{'sha256':'a'*64}])
            grant=json.loads((root/'meeting-model-grant.json').read_text())
            self.assertEqual(grant['api_key'],'offline-model-key')
            self.assertEqual(grant['max_microyuan'],2000000)
            self.assertEqual(grant['products'],['board','clean'])
            self.assertEqual(grant['processing_protocol'],'sales-board-first-v1')
            self.assertEqual(grant['source_hashes'],['a'*64])
            with self.assertRaises(RuntimeError):batch.activate([{'sha256':'a'*64}])
            batch.close_batch()
            grant=json.loads((root/'meeting-model-grant.json').read_text())
            self.assertFalse(grant['enabled']);self.assertNotIn('api_key',grant)

    def test_pilot_new_batch_archives_closed_bytes_and_limits_exactly_one_source(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'RUNTIME',Path(tmp)), patch.object(batch,'read_state',return_value={}), patch.object(batch,'owned_process',return_value=True):
            root=Path(tmp);bid='b'*32
            batch.private_json(root/'local-access.json',{'username':'admin','password':'offline-password'})
            batch.private_json(root/'meeting-model-grant.json',{'batch_id':bid,'enabled':False})
            batch.private_json(root/'meeting-model-ledger.json',{'batch_id':bid,'halted':True,'requests':[{'number':1}]})
            before=(root/'meeting-model-ledger.json').read_bytes()
            login={'token':'offline-session','user':{'id':'system_default_user'}}
            providers={'data':[{'id':'54fffda3','enabled':True,'base_url':'https://api.deepseek.com/v1','api_key':'offline-model-key','models':['deepseek-flash']}]}
            with patch.object(batch.urllib.request,'build_opener') as factory:
                with self.assertRaises(RuntimeError):batch.activate([{'sha256':'a'*64},{'sha256':'c'*64}],profile='pilot',new_batch=True)
                factory.assert_not_called()
                factory.return_value.open.side_effect=[io.BytesIO(json.dumps(login).encode()),io.BytesIO(b'{"processing_protocol":"sales-board-first-v1","workflow":"clean-then-board-v1"}'),io.BytesIO(json.dumps(providers).encode())]
                batch.activate([{'sha256':'a'*64}],profile='pilot',new_batch=True)
            self.assertEqual((root/'meeting-model-batches'/bid/'ledger.json').read_bytes(),before)
            grant=json.loads((root/'meeting-model-grant.json').read_text())
            self.assertEqual(grant['max_microyuan'],800000);self.assertEqual(grant['max_requests'],24)
            self.assertEqual(grant['source_hashes'],['a'*64]);self.assertNotEqual(grant['batch_id'],bid)
            self.assertFalse((root/'meeting-model-ledger.json').exists())

    def test_active_or_key_bearing_old_batch_cannot_be_archived(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'RUNTIME',Path(tmp)):
            root=Path(tmp);bid='c'*32
            batch.private_json(root/'meeting-model-grant.json',{'batch_id':bid,'enabled':True,'api_key':'offline-only'})
            batch.private_json(root/'meeting-model-ledger.json',{'batch_id':bid,'halted':False,'requests':[]})
            before=(root/'meeting-model-ledger.json').read_bytes()
            with self.assertRaises(RuntimeError):batch.archive_closed_batch()
            self.assertEqual((root/'meeting-model-ledger.json').read_bytes(),before)
            self.assertFalse((root/'meeting-model-batches').exists())

    def test_old_worker_is_blocked_before_provider_key_lookup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'RUNTIME',Path(tmp)), patch.object(batch,'read_state',return_value={}), patch.object(batch,'owned_process',return_value=True):
            root=Path(tmp);batch.private_json(root/'local-access.json',{'username':'admin','password':'offline-password'})
            with patch.object(batch.urllib.request,'build_opener') as factory:
                factory.return_value.open.side_effect=[io.BytesIO(b'{"token":"offline-session"}'),io.BytesIO(b'{"mode":"text-only"}')]
                with self.assertRaises(RuntimeError):batch.activate([{'sha256':'a'*64}],profile='pilot')
                self.assertEqual(factory.return_value.open.call_count,2)
                self.assertFalse((root/'meeting-model-grant.json').exists())

    def test_no_project_process_means_no_credential_request(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(batch,'RUNTIME',Path(tmp)), patch.object(batch,'read_state',return_value={}), patch.object(batch,'owned_process',return_value=False), patch.object(batch.urllib.request,'build_opener') as network:
            with self.assertRaises(RuntimeError):batch.activate([{'sha256':'a'*64}])
            network.assert_not_called()

if __name__=='__main__':unittest.main()
