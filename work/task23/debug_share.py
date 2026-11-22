import os, sys, asyncio, io, logging
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
logging.basicConfig(level=logging.DEBUG)
import test_photo_event_binding as T
import clock

out = io.TextIOWrapper(open(os.path.join(ROOT, "work", "task23", "debug_share.txt"), "wb"), encoding="utf-8")
for name, act, kv in [("mall", T._mall(), T._pending_shopping()), ("home", T._home(), None)]:
    plugin = T.FakePlugin(act, T.FakeDB and None) if False else None
    plugin = T.FakePlugin(act)
    if kv: plugin.db.kv.update(kv)
    engine = T.Engine(plugin)
    profile = {"uid": "u1", "last_seen": clock.now_ts()}
    print("===", name, file=out)
    try:
        ok = asyncio.run(engine._share("daily", "", profile))
        print("ok=", ok, "prompts=", engine.prompts, file=out)
        print("context.sent=", len(plugin.context.sent), file=out)
        import amo_media
        print("ledger=", amo_media.rows(plugin), file=out)
        print("meta=", getattr(engine, "_photo_send_meta", None), file=out)
    except Exception as exc:
        import traceback; traceback.print_exc(file=out)
out.close()
print("done")
