import os, sys, asyncio, io
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import test_photo_event_binding as T
import clock, amo_media, photo_share
import logging
log = logging.getLogger("dbg"); log.addHandler(logging.StreamHandler(io.TextIOWrapper(open(os.path.join(ROOT,"work","task23","debug3.txt"),"wb"), encoding="utf-8"))); log.setLevel(logging.DEBUG)
photo_share.logger = log
plugin = T.FakePlugin(T._home())
engine = T.Engine(plugin)
async def main():
    engine._photo_send_meta = {"origin": "auto", "kind": "daily", "event_id": "ev_home_1",
        "scene": {"event_id": "ev_home_1", "zone": "home_bed", "place": "家"},
        "pose": None, "outfit": "x", "test": False, "claim_event": True,
        "dedupe": True, "enforce_cap": True, "count": True}
    item = {"file_path": "fake.jpg"}
    res = await amo_media.send_photo(plugin, "umo-test", item, origin="auto", kind="daily",
        event_id="ev_home_1", scene={"zone": "home_bed"}, claim_event=True, dedupe=True,
        enforce_cap=True, count=True)
    print("gateway result:", res, file=log.handlers[0].stream)
asyncio.run(main())
print("done")
