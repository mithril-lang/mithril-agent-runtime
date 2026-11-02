"""Profile isolation and optional metadata transmission are tested without live credentials."""
import argparse
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hermes_cli import posthog_analytics as analytics
class Response(io.BytesIO):
    status=202
class Opener:
    def __init__(self,error=False): self.calls=[];self.error=error
    def open(self,request,timeout):
        self.calls.append(request)
        if self.error: raise TimeoutError("private exception")
        event=json.loads(request.data)["event"]
        return Response(json.dumps({"eventId":event["id"],"state":"accepted"}).encode())
class Analytics(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{"MITHRIL_AGENT":"1"});self.env.start()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def test_no_choice_does_not_read_a_token_or_send(self):
        with patch.object(analytics,"read_consent",return_value=None):
            token=lambda: self.fail("Credential read before consent")
            self.assertEqual("disabled",analytics.record("product","completed","command",token_reader=token)["state"])
    def test_choice_is_per_profile_and_file_is_private(self):
        a=self.home/"a";b=self.home/"b";a.mkdir();b.mkdir()
        analytics.set_consent(True,a)
        self.assertIsNone(analytics.read_consent(b))
        self.assertEqual(0o600,(a/".posthog-consent.json").stat().st_mode&0o777)
        analytics.set_consent(False,a);self.assertIsNone(analytics.read_consent(a))
        with self.assertRaises(ValueError):analytics.set_consent(True,self.home/"missing")
    def test_fixed_origin_closed_dimensions_and_no_arguments_or_profile_name(self):
        analytics.set_consent(True,self.home)
        opener=Opener()
        result=analytics.record("bot","completed","command",home=self.home,token_reader=lambda:"synthetic-profile-token",opener=opener)
        self.assertEqual("accepted",result["state"]);self.assertFalse(result["indexingVerified"])
        request=opener.calls[0]
        self.assertEqual(analytics.ENDPOINT,request.full_url)
        payload=json.loads(request.data)
        self.assertEqual("cli",payload["event"]["client"])
        self.assertNotIn(str(self.home),request.data.decode())
        self.assertEqual({"id","client","channel","outcome","feature","at","profileRef"},set(payload["event"]))
        with self.assertRaises(ValueError):
            analytics.record("bot","completed","private-prompt",home=self.home)
    def test_timeout_is_unknown_with_one_attempt(self):
        analytics.set_consent(True,self.home);opener=Opener(error=True)
        result=analytics.record("x","completed","campaign",home=self.home,token_reader=lambda:"synthetic",opener=opener)
        self.assertEqual("unknown",result["state"]);self.assertEqual(1,len(opener.calls))
        self.assertNotIn("private",json.dumps(result))
    def test_withdrawal_prevents_next_command_and_does_not_change_exit_codes(self):
        analytics.set_consent(False,self.home)
        with patch.object(analytics,"read_consent",return_value=None):
            self.assertEqual(7,analytics.reported_command(lambda args:7,object()))
            with self.assertRaisesRegex(ValueError,"original"):
                analytics.reported_command(lambda args:(_ for _ in ()).throw(ValueError("original")),object())
    def test_enable_requires_explicit_usage_consent(self):
        parser=argparse.ArgumentParser();subs=parser.add_subparsers(dest="command")
        analytics.build_analytics_parser(subs)
        with self.assertRaises(SystemExit):parser.parse_args(["analytics","enable"])
        self.assertTrue(parser.parse_args(["analytics","enable","--usage-consent"]).usage_consent)
if __name__=="__main__":unittest.main()
