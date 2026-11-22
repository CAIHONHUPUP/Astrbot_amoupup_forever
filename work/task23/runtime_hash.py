import sys, os, hashlib
root = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
sys.path.insert(0, root); sys.path.insert(0, os.path.join(root, "tests"))
import astrbot_stub; astrbot_stub.install()
import amo_runtime, test_runtime_split as T
func = getattr(amo_runtime.RuntimeMixin, "on_decorating_result")
print("co_code", hashlib.md5(func.__code__.co_code).hexdigest())
print("fingerprint", T._fingerprint(func.__code__))
func2 = getattr(amo_runtime.RuntimeMixin, "on_message")
print("on_message co_code now", hashlib.md5(func2.__code__.co_code).hexdigest())
print("on_message fingerprint now", T._fingerprint(func2.__code__))
