"""A persistent local publisher identity, separate from each user's node identity."""
from __future__ import annotations
import json
from pathlib import Path
import os
from cryptography.hazmat.primitives import serialization
from a2n_p2p import Identity
from a2n_sdk.experience import signed,unsigned
from .protection import system_protector
from .feedback_identity import signer_for,verifier_for
from .upgrades import file_digest

class ReleasePublisher:
    def __init__(self,directory,*,protector=None):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.protector=protector or system_protector();key=self.directory/"publisher.sealed"
        if key.exists():self.identity=Identity.from_private_bytes(self.protector.open(key.read_bytes()))
        else:
            self.identity=Identity.generate();raw=self.identity._sk.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
            with key.open("xb") as stream:stream.write(self.protector.seal(raw))
            if os.name!="nt":key.chmod(0o600)
        (self.directory/"publisher-public.json").write_text(json.dumps({"author_did":self.identity.did,"pub":signer_for(self.identity)({})["pub"]},indent=2),encoding="utf-8")

    def sign(self,artifact,*,sequence,platform="Windows"):
        path=Path(artifact)
        if type(sequence) is not int or not 1<=sequence<2**128:raise ValueError("INVALID_RELEASE_SEQUENCE")
        before=path.stat();checksum=file_digest(path);after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError("RELEASE_CHANGED_WHILE_SIGNING")
        manifest=signed({"v":"a2n-release/1","author_did":self.identity.did,"release_sequence":sequence if sequence<=2**53-1 else str(sequence),
            "platform":platform,"artifact":path.name,"size":after.st_size,"sha256":checksum,"database_schema":2},signer_for(self.identity))
        if not verifier_for()(manifest["proof"],unsigned(manifest)):raise ValueError("RELEASE_SIGNATURE_SELF_CHECK_FAILED")
        destination=path.with_name(path.name+".release.json")
        destination.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
        return manifest
