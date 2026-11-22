import os, sys, asyncio, io
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import test_photo_event_binding as T
import clock, amo_media, photo_share
import amo_scene_ledger

out = io.TextIOWrapper(open(os.path.join(ROOT,"work","task23","debug4.txt"),"wb"), encoding="utf-8")
calls = {}
orig_send_photo = amo_media.send_photo
async def spy_send_photo(*args, **kwargs):
    r = await orig_send_photo(*args, **kwargs)
    calls["send_photo"] = dict(kwargs)
    calls["result"] = r
    print("send_photo args:", {k:v for k,v in kwargs.items() if k!='transport'}, file=out)
    print("result:", r, file=out)
    return r
amo_media.send_photo = spy_send_photo
orig_verify = amo_scene_ledger.verify_photo_scene
def spy_verify(plugin, expected_zone="", expected_event_id="", now=None):
    r = orig_verify(plugin, expected_zone=expected_zone, expected_event_id=expected_event_id, now=now)
    print("verify_scene:", r, file=out)
    return r
amo_scene_ledger.verify_photo_scene = spy_verify

plugin = T.FakePlugin(T._home())
engine = T.Engine(plugin)
profile = {"uid": "u1", "last_seen": clock.now_ts()}
ok = asyncio.run(engine._share("daily", "", profile))
print("ok", ok, "sent", len(plugin.context.sent), "ledger", amo_media.rows(plugin), file=out)
out.close()
print("done")
