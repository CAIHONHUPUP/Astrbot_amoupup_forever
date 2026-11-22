import os, sys, asyncio, io, logging
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import test_photo_event_binding as T
import clock, photo_share, amo_media

buf = io.StringIO()
handler = logging.StreamHandler(buf)
handler.setLevel(logging.DEBUG)
log = logging.getLogger("dbg")
log.addHandler(handler); log.setLevel(logging.DEBUG)
photo_share.logger = log

plugin = T.FakePlugin(T._home())
engine = T.Engine(plugin)
profile = {"uid": "u1", "last_seen": clock.now_ts()}
orig_send = engine._send
calls = []
async def wrapped_send(umo, item, caption=""):
    r = await orig_send(umo, item, caption=caption)
    calls.append(r)
    return r
engine._send = wrapped_send
ok = asyncio.run(engine._share("daily", "", profile))
out = io.TextIOWrapper(open(os.path.join(ROOT, "work", "task23", "debug_share2.txt"), "wb"), encoding="utf-8")
print("ok", ok, "send_calls", calls, file=out)
print("log:", buf.getvalue(), file=out)
print("ledger", amo_media.rows(plugin), file=out)
out.close()
print("done")
