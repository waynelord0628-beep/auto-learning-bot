import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from utils.app_update import release_offer, download, replace_with_backup, helper_main


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = b'MZ' + b'valid fixture' * 10
        self.offer = {'url':'https://github.com/test/file','size':len(self.data),'sha256':hashlib.sha256(self.data).hexdigest()}

    def test_release_must_have_one_verified_matching_asset(self):
        asset={'name':'app.exe','state':'uploaded','size':100,'digest':'sha256:'+'a'*64,'browser_download_url':'https://github.com/waynelord0628-beep/auto-learning-bot/releases/download/v2.1.9/app.exe'}
        release={'tag_name':'v2.1.9','assets':[asset]}
        self.assertEqual(release_offer(release,'V2.1.8')['version'],'V2.1.9')
        for bad in [dict(release,prerelease=True),dict(release,draft=True),dict(release,assets=[asset,asset]),dict(release,assets=[dict(asset,digest='')]),dict(release,assets=[dict(asset,browser_download_url='https://example.invalid/app.exe')])]:
            self.assertIsNone(release_offer(bad,'V2.1.8'))
        self.assertIsNone(release_offer(release,'V2.1.9'))

    def response(self, data):
        response=Mock();response.url='https://release-assets.githubusercontent.com/test'
        response.iter_content.return_value=[data]
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        return Mock(return_value=response)

    def test_download_checks_hash_before_exposing_complete_file(self):
        get=self.response(self.data)
        result=download(self.offer,self.root,get,lambda:False,Mock())
        self.assertEqual(Path(result).read_bytes(),self.data)
        self.assertFalse((self.root/'download.part').exists())

    def test_bad_or_cancelled_download_leaves_no_installable_file(self):
        for data,cancel in [(b'MZwrong',False),(self.data,True),(b'NZ'+self.data[2:],False)]:
            with self.assertRaises(ValueError):download(self.offer,self.root,self.response(data),lambda:cancel,Mock())
            self.assertFalse((self.root/'incoming.exe').exists())
            self.assertFalse((self.root/'download.part').exists())

    def test_move_failure_restores_original_and_preserves_settings(self):
        target=self.root/'app.exe';incoming=self.root/'incoming.exe';backup=self.root/'backup.exe';config=self.root/'config.json'
        target.write_bytes(b'old');incoming.write_bytes(b'new');config.write_text('keep')
        import os
        actual=os.replace
        def move(a,b):
            if Path(a)==incoming:raise PermissionError('locked')
            return actual(a,b)
        with patch('utils.app_update.os.replace',side_effect=move):
            with self.assertRaises(PermissionError):replace_with_backup(target,incoming,backup)
        self.assertEqual(target.read_bytes(),b'old');self.assertEqual(config.read_text(),'keep')

    def test_launch_failure_rolls_back(self):
        import psutil
        target=self.root/'app.exe';target.write_bytes(b'old')
        stage=self.root/'.updates'/'test';stage.mkdir(parents=True)
        (stage/'incoming.exe').write_bytes(self.data)
        manifest=stage/'transaction.json';manifest.write_text(json.dumps({'target':str(target),'stage':str(stage),'version':'V2.1.9','size':len(self.data),'sha256':self.offer['sha256'],'pid':123,'created':0}))
        with patch('psutil.Process',side_effect=psutil.NoSuchProcess(123)),patch('utils.app_update.subprocess.Popen',side_effect=[OSError('bad executable'),Mock()]):
            self.assertEqual(helper_main(manifest),1)
        self.assertEqual(target.read_bytes(),b'old')
        self.assertEqual(json.loads(manifest.read_text())['state'],'rolled_back')

    def test_helper_success_retains_backup_and_acknowledges_version(self):
        import psutil
        target=self.root/'app.exe';target.write_bytes(b'old')
        stage=self.root/'.updates'/'success';stage.mkdir(parents=True)
        (stage/'incoming.exe').write_bytes(self.data)
        manifest=stage/'transaction.json';manifest.write_text(json.dumps({'target':str(target),'stage':str(stage),'version':'V2.1.9','size':len(self.data),'sha256':self.offer['sha256'],'pid':123,'created':0}))
        def launch(*a,**kw):
            (stage/'ready.json').write_text(json.dumps({'version':'V2.1.9','target':str(target)}))
            return Mock()
        with patch('psutil.Process',side_effect=psutil.NoSuchProcess(123)),patch('utils.app_update.subprocess.Popen',side_effect=launch):
            self.assertEqual(helper_main(manifest),0)
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual((stage/'previous.exe').read_bytes(),b'old')
        self.assertEqual(json.loads(manifest.read_text())['state'],'complete')

    def test_active_original_times_out_without_replacement(self):
        import psutil
        target=self.root/'app.exe';target.write_bytes(b'old')
        stage=self.root/'.updates'/'wait';stage.mkdir(parents=True)
        (stage/'incoming.exe').write_bytes(self.data)
        manifest=stage/'transaction.json';manifest.write_text(json.dumps({'target':str(target),'stage':str(stage),'version':'V2.1.9','size':len(self.data),'sha256':self.offer['sha256'],'pid':123,'created':1}))
        proc=Mock();proc.create_time.return_value=1;proc.wait.side_effect=psutil.TimeoutExpired(60)
        with patch('psutil.Process',return_value=proc):self.assertEqual(helper_main(manifest),1)
        self.assertEqual(target.read_bytes(),b'old')
        self.assertFalse((stage/'previous.exe').exists())


if __name__=='__main__':unittest.main()
