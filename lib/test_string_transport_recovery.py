"""Explicit transport recovery must not become a general retry allowance."""
import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from string_usage import Ledger,UsageBlocked
from string_capture import recover_retained_transport,CaptureStopped

class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=[1000.0];self.l=Ledger(self.root/'ledger.sqlite3',clock=lambda:self.now[0]);self.url='https://www.instagram.com/reel/Source123/'
        self.direction='Please investigate and retry this exact failed source once'
    def prepare(self,mode):
        self.l.create_run('original','0.05',max_requests=1,min_gap_seconds=0)
        parent=self.l.reserve('original','Source123')
        if mode=='paid':self.l.complete(parent,0,None,error='provider_transport_error')
        else:
            self.l.complete(parent,200,'request_premium');self.l.hold('media_capture_or_validation_failed')
        self.l.record_access_check(200,run_id='original');self.l.resume_unattended('original','Reviewed failed Cloud transport; never retry automatically')
        if mode=='retained':self.l.settle_from_price_list('original','Cloud successful fetch settled at published list price')
        self.l.create_run('retry','0.006',max_requests=1,min_gap_seconds=0)
        self.l.record_access_check(200,run_id='retry')
        page=self.root/'Source123.source.json';page.write_text(json.dumps({'data':{'code':'Source123','video_url':'https://scontent.cdninstagram.com/test.mp4'}}))
        evidence={'attempt_id':parent,'source_url':self.url,'user_direction':self.direction,'transport_fix_reviewed':True,'transport_fix_commit':'a'*40,'mode':mode,'retained_page_sha256':hashlib.sha256(page.read_bytes()).hexdigest()}
        p=self.root/'evidence.json';p.write_text(json.dumps(evidence));return parent,p
    def test_paid_exact_once_preserves_original_and_normal_guards(self):
        parent,p=self.prepare('paid');before=self.l.report('original')['attempt_records'][0]
        with self.assertRaises(ValueError):self.l.authorize_transport_recovery('retry',parent,self.url,'',p)
        with self.assertRaisesRegex(UsageBlocked,'evidence_mismatch'):self.l.authorize_transport_recovery('retry',parent,self.url.replace('Source123','Other123'),self.direction,p)
        with self.assertRaisesRegex(UsageBlocked,'source_already_attempted'):self.l.reserve('retry','Source123')
        self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        with self.assertRaisesRegex(UsageBlocked,'already_authorized'):self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        with self.assertRaisesRegex(UsageBlocked,'exact_source'):self.l.reserve('retry','Other123')
        child=self.l.reserve('retry','Source123',recovery_of=parent,recovery_source_url=self.url)
        self.assertEqual(self.l.report('retry')['attempt_records'][0]['recovery_of'],parent)
        with self.assertRaises(UsageBlocked):self.l.reserve('retry','Source123',recovery_of=parent,recovery_source_url=self.url)
        self.l.complete(child,200,'request_premium',usable_media=True)
        self.assertEqual(self.l.report('original')['attempt_records'][0],before)
        with self.assertRaises(UsageBlocked):self.l.reserve('retry','Other123')
    def test_hold_unknown_charge_stale_check_and_billing_anomaly_refuse(self):
        parent,p=self.prepare('paid');self.now[0]+=3601
        with self.assertRaisesRegex(UsageBlocked,'fresh_passing'):self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        self.l.record_access_check(200,run_id='retry');self.l.hold('rate_jump')
        with self.assertRaisesRegex(UsageBlocked,'global_hold'):self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        self.assertEqual(self.l.report()['hold_reason'],'rate_jump')
        with tempfile.TemporaryDirectory() as d:
            l=Ledger(Path(d)/'u.sqlite3',clock=lambda:1000);l.create_run('original','0.05',max_requests=1);l.create_run('retry','0.05',max_requests=1)
            aid=l.reserve('original','Source123')
            with self.assertRaisesRegex(UsageBlocked,'unreconciled'):l.authorize_transport_recovery('retry',aid,self.url,self.direction,p)
            l.complete(aid,200,None)
            with self.assertRaisesRegex(UsageBlocked,'global_hold'):l.authorize_transport_recovery('retry',aid,self.url,self.direction,p)
    def test_retained_completion_calls_no_provider_and_consumes_before_media(self):
        parent,p=self.prepare('retained');before=self.l.report('original')['attempt_records'][0]['actual_micro'];self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        def download(url,dest,**kw):dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(b'validated media fixture')
        with patch.dict(os.environ,{'STRING_MEDIA_USE_HTTPS_PROXY':'1'}),patch('string_capture.bounded_fetch_page') as provider,patch('string_capture.download_asset',side_effect=download),patch('string_capture.validate_media',return_value={'has_audio':True}) as validate:
            r=recover_retained_transport('retry',parent,self.url,p,self.l,self.root)
            self.assertEqual(r['additional_string_requests'],0);provider.assert_not_called();self.assertTrue(validate.call_args.kwargs['full_decode'])
            with self.assertRaisesRegex(UsageBlocked,'not_authorized_or_used'):recover_retained_transport('retry',parent,self.url,p,self.l,self.root)
        self.assertEqual(self.l.report('original')['attempt_records'][0]['actual_micro'],before)
        self.assertEqual(self.l.report()['attempt_count'],1)
    def test_retained_changed_page_and_failed_media_never_silently_retry(self):
        parent,p=self.prepare('retained');self.l.authorize_transport_recovery('retry',parent,self.url,self.direction,p)
        page=self.root/'Source123.source.json';raw=page.read_bytes();page.write_bytes(raw+b' ')
        with self.assertRaisesRegex(CaptureStopped,'hash_mismatch'):recover_retained_transport('retry',parent,self.url,p,self.l,self.root)
        page.write_bytes(raw)
        with patch.dict(os.environ,{'STRING_MEDIA_USE_HTTPS_PROXY':'1'}),patch('string_capture.download_asset',side_effect=OSError('network unavailable')):
            with self.assertRaisesRegex(CaptureStopped,'media_capture'):recover_retained_transport('retry',parent,self.url,p,self.l,self.root)
        self.assertEqual(self.l.report()['hold_reason'],'media_capture_or_validation_failed')
        with self.assertRaises(UsageBlocked):self.l.consume_retained_transport_recovery('retry',parent,self.url,p)

if __name__=='__main__':unittest.main()
