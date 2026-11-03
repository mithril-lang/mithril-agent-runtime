import base64
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from email.message import Message

spec=importlib.util.spec_from_file_location("cli_publisher",Path(__file__).resolve().parents[1]/"scripts/publish-mithril-release.py")
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class PublicationBoundaries(unittest.TestCase):
    def test_scope_requires_exact_single_repository_and_actual_write_permission(self):
        repo={"id":m.REPO_ID,"full_name":m.REPO,"permissions":{"push":True}}
        install={"total_count":1,"repositories":[{"id":m.REPO_ID,"full_name":m.REPO}]}
        m.scoped_authority(repo,install)
        for bad in ({}, {"total_count":2,"repositories":install["repositories"]},
                    {"total_count":1,"repositories":[{"id":1,"full_name":"other/repo"}]}):
            with self.assertRaises(m.Held):m.scoped_authority(repo,bad)
        for bad in ({**repo,"id":1},{**repo,"permissions":{"push":False}}):
            with self.assertRaises(m.Held):m.scoped_authority(bad,install)

    def test_versions_and_sequences_must_advance_and_keep_exact_upgrade_source(self):
        p={"sequence":12,"version":"0.1.0-preview.20261103.t0510","previous_revisions":["a"*40]}
        prior={"sequence":11,"version":"0.1.0-preview.20261101.t1220","runtime_revision":"a"*40}
        m.next_release(p,prior)
        for bad in ({**p,"sequence":11},{**p,"version":prior["version"]},{**p,"previous_revisions":["b"*40]}):
            with self.assertRaises(m.Held):m.next_release(bad,prior)

    def test_no_arbitrary_origin_or_mutation_and_no_secret_in_provider_errors(self):
        api=m.GitHub("SECRET_FIXTURE")
        for method,path in (("GET","https://other.example"),("GET","/user"),("DELETE",m.PREFIX+"/releases/1"),
                            ("PATCH",m.PREFIX),("POST","/installation/repositories?per_page=100")):
            with self.assertRaises(m.Held):api.request(method,path)
        api.opener=unittest.mock.Mock()
        api.opener.open.side_effect=HTTPError("https://api.github.com",503,"SECRET_FIXTURE",{},None)
        with self.assertRaises(m.Held) as error:api.request("GET",m.PREFIX)
        self.assertNotIn("SECRET",str(error.exception))
        self.assertNotEqual(str(error.exception),"provider_not_found")
        api.opener.open.side_effect=HTTPError("https://api.github.com",404,"not found",{},None)
        with self.assertRaisesRegex(m.Held,"^provider_not_found$"):api.request("GET",m.PREFIX+"/git/ref/tags/v0.1.0")

    def test_artifact_redirect_never_receives_the_github_token(self):
        api=m.GitHub("SECRET_FIXTURE");api.opener=unittest.mock.Mock()
        headers=Message();headers["Location"]="https://release-assets.githubusercontent.com/fixture?signature=PRIVATE"
        api.opener.open.side_effect=[HTTPError("https://api.github.com",302,"redirect",headers,None),io.BytesIO(b"asset")]
        raw=api.request("GET",m.PREFIX+"/releases/assets/1",raw=True)
        self.assertEqual(raw,b"asset")
        first,second=[call.args[0] for call in api.opener.open.call_args_list]
        self.assertEqual(first.get_header("Authorization"),"Bearer SECRET_FIXTURE")
        self.assertIsNone(second.get_header("Authorization"))
        headers.replace_header("Location","https://other.example/fixture")
        api.opener.open.side_effect=HTTPError("https://api.github.com",302,"redirect",headers,None)
        with self.assertRaisesRegex(m.Held,"artifact_redirect_rejected"):
            api.request("GET",m.PREFIX+"/releases/assets/1",raw=True)

    def fixture(self, root):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        key=Ed25519PrivateKey.generate();public=key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
        trust={"keys":{"fixture":{"revoked":False,"public_key":base64.b64encode(public).decode()}}}
        sha="b"*40;workflow="33333333-3333-4333-8333-333333333333";pipeline="22222222-2222-4222-8222-222222222222"
        version="0.1.0-preview.20261103.t0510";now=datetime.now(timezone.utc)
        provenance=b'{"fixture":true}\n';(root/".mithril-runtime-provenance.json").write_bytes(provenance)
        assets=root/"assets";assets.mkdir();data={"install-mithril.sh":b"#!/bin/sh\n","mithril-cli-runtime.tar.gz":b"source fixture"}
        artifact=lambda name:{"url":"https://github.com/"+m.REPO+"/releases/download/v"+version+"/"+name,
                              "size":len(data[name]),"sha256":m.digest(data[name])}
        payload={"schema":1,"product":"mithril-cli","repository":m.REPO,"channel":"preview","version":version,
                 "sequence":12,"runtime_revision":sha,"previous_revisions":["a"*40],
                 "issued_at":now.isoformat(),"expires_at":(now+timedelta(days=1)).isoformat(),
                 "installer":artifact("install-mithril.sh"),"source_archive":artifact("mithril-cli-runtime.tar.gz"),
                 "provenance":{"source_repository":"mithril-lang/mithril-fund","source_revision":"c"*40,"manifest_sha256":m.digest(provenance)}}
        envelope={"key_id":"fixture","signed":payload,"signature":base64.b64encode(key.sign(m.updates.canonical(payload))).decode()}
        data["mithril-update.json"]=m.updates.canonical(envelope)+b"\n"
        data["release-evidence.json"]=json.dumps({"schema":1,"key_id":"fixture","public_key_sha256":m.digest(public),"provider":"circleci","runtime_revision":sha,"version":version,"workflow_id":workflow,
             "pipeline_id":pipeline,"job_id":"44444444-4444-4444-8444-444444444444"}).encode()
        for name,raw in data.items():(assets/name).write_bytes(raw)
        self.sums(assets)
        return assets,sha,workflow,version,pipeline,trust
    def sums(self,assets):
        (assets/"SHA256SUMS").write_text("".join(m.digest(p.read_bytes())+"  "+p.name+"\n" for p in sorted(assets.iterdir()) if p.name!="SHA256SUMS"))

    def test_signed_inventory_rejects_corruption_wrong_source_and_unrelated_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);assets,sha,wf,version,pipeline,trust=self.fixture(root)
            with patch.object(m,"ROOT",root),patch.object(m.updates,"load_trust",return_value=trust),patch.dict(os.environ,{"CIRCLE_PIPELINE_ID":pipeline}):
                p,inventory,data=m.verified_assets(assets,sha,wf,version,12)
                self.assertEqual(set(inventory),m.NAMES)
                with self.assertRaises(m.Held):m.verified_assets(assets,"a"*40,wf,version,12)
                with self.assertRaises(m.Held):m.verified_assets(assets,sha,"other",version,12)
                (assets/"install-mithril.sh").write_bytes(b"changed source")
                with self.assertRaises(m.Held):m.verified_assets(assets,sha,wf,version,12)
                self.sums(assets)
                with self.assertRaisesRegex(m.Held,"signed_release_asset_mismatch"):m.verified_assets(assets,sha,wf,version,12)

    def test_unknown_draft_creation_is_journaled_without_retry_or_secret_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);assets,sha,wf,version,pipeline,trust=self.fixture(root)
            api=unittest.mock.Mock();api.request.side_effect=TimeoutError("SECRET_FIXTURE")
            prior={"priorReleaseId":123,"priorManifestSha256":"d"*64}
            with patch.object(m,"ROOT",root),patch.object(m,"OUTPUT",root/"out"),patch.object(m,"identity",return_value=sha),\
                 patch.object(m,"observe",return_value=prior),patch.object(m,"GitHub",return_value=api),patch.object(m,"release_token",return_value="FIXTURE"),\
                 patch.object(m.updates,"load_trust",return_value=trust),patch.dict(os.environ,{"CIRCLE_PIPELINE_ID":pipeline,"CIRCLE_WORKFLOW_ID":wf}),\
                 patch.object(m.sys,"argv",["publisher","publish","--assets",str(assets),"--state",str(root/"state.json"),"--version",version,"--sequence","12"]):
                payload,inventory,_=m.verified_assets(assets,sha,wf,version,12)
                state={"sourceSha":sha,"pipelineId":pipeline,"workflowId":wf,"version":version,"sequence":12,"assets":inventory,**prior}
                m.write_state(root/"state.json",state)
                with patch("sys.stdout",new_callable=io.StringIO) as output:
                    self.assertEqual(m.main(),1)
                journal=json.loads((root/"out/journal.json").read_bytes())
                self.assertEqual(journal["status"],"draft-create-attempted")
                self.assertFalse(journal["publicationCompleted"])
                self.assertEqual(api.request.call_count,1)
                self.assertNotIn("SECRET",output.getvalue())
                original_journal=(root/"out/journal.json").read_bytes()
                with patch("sys.stdout",new_callable=io.StringIO):
                    self.assertEqual(m.main(),1)
                self.assertEqual(api.request.call_count,1)
                self.assertEqual((root/"out/journal.json").read_bytes(),original_journal)


class CloudCredentialMint(unittest.TestCase):
    def test_mints_only_contents_write_for_the_one_runtime_repository(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,NoEncryption
        private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        env={"MITHRIL_CLI_RELEASE_APP_ID":"123","MITHRIL_CLI_RELEASE_INSTALLATION_ID":"456",
             "MITHRIL_CLI_RELEASE_APP_KEY":private.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()).decode()}
        client=unittest.mock.Mock()
        value={"token":"SCOPED_FIXTURE","permissions":{"contents":"write"},"repositories":[{"id":m.REPO_ID,"full_name":m.REPO}]}
        client.open.return_value=io.BytesIO(json.dumps(value).encode())
        self.assertEqual(m.release_token(env,client),"SCOPED_FIXTURE")
        req=client.open.call_args.args[0]
        self.assertEqual(req.full_url,"https://api.github.com/app/installations/456/access_tokens")
        self.assertEqual(json.loads(req.data),{"repository_ids":[m.REPO_ID],"permissions":{"contents":"write"}})
        value["repositories"].append({"id":1,"full_name":"other/repo"})
        client.open.return_value=io.BytesIO(json.dumps(value).encode())
        with self.assertRaisesRegex(m.Held,"minted_credential_scope_mismatch"):m.release_token(env,client)

    def test_missing_or_bad_app_credentials_do_not_fall_back_or_disclose_secrets(self):
        with self.assertRaisesRegex(m.Held,"scoped_release_credential_missing"):m.release_token({})
        env={"MITHRIL_CLI_RELEASE_APP_ID":"123","MITHRIL_CLI_RELEASE_INSTALLATION_ID":"456","MITHRIL_CLI_RELEASE_APP_KEY":"PRIVATE_FIXTURE"}
        with self.assertRaises(m.Held) as error:m.release_token(env)
        self.assertNotIn("PRIVATE",str(error.exception))
        self.assertEqual(m.release_token({"MITHRIL_CLI_RELEASE_TOKEN":"SCOPED_FIXTURE"}),"SCOPED_FIXTURE")
if __name__=="__main__":unittest.main()

