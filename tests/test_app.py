import hashlib
import hmac
import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch
import app

class RagnarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'RAILWAY_VOLUME_MOUNT_PATH': self.tmp.name, 'META_APP_SECRET': 'test-secret', 'WEBHOOK_VERIFY_TOKEN': 'test-verify', 'INSTAGRAM_ACCOUNT_ID': '123', 'INSTAGRAM_ACCESS_TOKEN': 'test-token', 'META_API_VERSION': 'v23.0', 'WHATSAPP_CONFIRMED': 'true', 'AUTOMATION_ENABLED': 'true'}, clear=True)
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()
    def payload(self, text='QUERO!', account='123'):
        return {'object':'instagram','entry':[{'id':account,'changes':[{'field':'comments','value':{'id':'456','text':text,'from':{'id':'789'}}}]}]}
    def call(self, path, method='GET', data=None, signature=None, query=''):
        raw = json.dumps(data).encode() if data is not None else b''
        if signature is None:
            signature = 'sha256=' + hmac.new(b'test-secret',raw,hashlib.sha256).hexdigest()
        result = []
        output = app.application({'PATH_INFO':path,'REQUEST_METHOD':method,'QUERY_STRING':query,'CONTENT_LENGTH':str(len(raw)),'wsgi.input':io.BytesIO(raw),'HTTP_X_HUB_SIGNATURE_256':signature},lambda status, headers: result.append(status))
        return result[0], b''.join(output)
    def test_health_without_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(self.call('/healthz')[0], '200 OK')
            self.assertEqual(self.call('/readyz')[0], '503 Service Unavailable')
    def test_verify_token(self):
        self.assertEqual(self.call('/webhook',query='hub.mode=subscribe&hub.verify_token=test-verify&hub.challenge=hello'), ('200 OK',b'hello'))
        self.assertEqual(self.call('/webhook',query='hub.mode=subscribe&hub.verify_token=wrong')[0], '403 Forbidden')
    def test_reject_forged_signature(self):
        self.assertEqual(self.call('/webhook','POST',self.payload(),signature='sha256=wrong')[0], '403 Forbidden')
    def test_account_and_keyword_boundaries(self):
        self.assertEqual(len(app.collect(self.payload('eu quero'))),1)
        self.assertEqual(app.collect(self.payload('queroalgo')),[])
        self.assertEqual(app.collect(self.payload(account='999')),[])
    def test_webhook_duplicate_sends_once(self):
        for _ in range(2):
            self.assertEqual(self.call('/webhook','POST',self.payload())[0], '200 OK')
        with patch('app.send', return_value='message-1') as send:
            app.process_one(); app.process_one()
            send.assert_called_once_with('456','comment')
        with app.db() as c:
            self.assertEqual(c.execute('SELECT status FROM jobs').fetchone()[0],'sent')
    def test_timeout_never_blindly_retries(self):
        app.enqueue(app.collect(self.payload()))
        with patch('app.send', side_effect=TimeoutError) as send:
            app.process_one(); app.process_one()
            self.assertEqual(send.call_count,1)
        with app.db() as c:
            self.assertEqual(c.execute('SELECT status FROM jobs').fetchone()[0],'uncertain')
    def test_pause_no_send(self):
        app.enqueue(app.collect(self.payload()))
        with patch.dict(os.environ, {'AUTOMATION_ENABLED':'false'}),patch('app.send') as send:
            app.process_one(); send.assert_not_called()
    def test_dm_echo_and_old_messages_ignored(self):
        event={'sender':{'id':'789'},'recipient':{'id':'123'},'timestamp':time.time()*1000,'message':{'mid':'m1','text':'Oi'}}
        payload={'object':'instagram','entry':[{'id':'123','messaging':[event]}]}
        self.assertEqual(len(app.collect(payload)),1)
        event['message']['is_echo']=True
        self.assertEqual(app.collect(payload),[])
        event['message']['is_echo']=False; event['timestamp']=0
        self.assertEqual(app.collect(payload),[])
    def test_daily_limit(self):
        app.enqueue(app.collect(self.payload()))
        with patch.dict(os.environ, {'DAILY_ACTION_LIMIT':'0'}),patch('app.send') as send:
            app.process_one(); send.assert_not_called()
    def test_greeting_has_correct_offer_and_links(self):
        text=app.greeting()
        for required in ['https://ragnarplay.online/','https://wa.me/553491341688','25,00','60,00','110,00','190,00','2 dispositivos']:
            self.assertIn(required,text)

if __name__ == '__main__': unittest.main()
