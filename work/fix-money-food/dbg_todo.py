import sys, datetime as dt
sys.path.insert(0,'.')
sys.path.insert(0,'tests')
import astrbot_stub; astrbot_stub.install()
import amo_food_cost as fc
from amo_human_state import HumanState
from amo_wallet import Wallet
from plugin_fakes import WalletFakeDB
from amo_state_common import _now
print("now:", _now())
print("slot:", fc.meal_slot_at(_now()))

class FakeDB:
    def __init__(self): self.kv={}
    def kv_get(self,k,d=None): return self.kv.get(k,d)
    def kv_set(self,k,v): self.kv[k]=v
class FakeState:
    def __init__(self): self.schedule={}
    def commit_schedule_change(self, day, new, old_items=None, source="", reason=""):
        self.schedule[day]=[dict(r) for r in new]; return {"changed":True,"saved":True}
class P:
    def __init__(self):
        self.db=FakeDB(); self.wallet=Wallet(WalletFakeDB()); self.wallet.ensure_schema()
        self.wallet.credit(50000,"init","t"); self.state=FakeState()

p=P()
state=HumanState.__new__(HumanState); state.p=p
plan, cost, paid = state._todo_plan({"id":9,"day":"2026-11-17","start":"12:00","activity":"和柳如烟吃饭","place":"餐厅","category":"social","cost_cents":10000})
print("plan",plan,"cost",cost,"paid",paid)
for r in p.wallet.ledger(10): print({k:r[k] for k in ("idem","delta_cents","event_id","sub_category")})
