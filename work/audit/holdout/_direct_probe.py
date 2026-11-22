# -*- coding: utf-8 -*-
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(ROOT, "work", "sim")); sys.path.insert(0, os.path.join(ROOT, "tests")); sys.path.insert(0, ROOT)
os.chdir(ROOT)
import sim_env as E
import amo_purchase_guard as PG
p = E.build_plugin([], kv={}, health={})
cases = [
 "老公，下午茶又发奶茶了 也不知道谁定的，我上午自己还买了一杯，一天两杯这谁顶得住啊",
 "我回来啦！今天买了米白色针织开衫，花了649元，穿上真的超好看！",
 "（拎着购物袋，往电梯口走，笑着摇摇头） 行，那我不给你买了。柳如烟还在里面试，我等等她。",
]
for c in cases:
    print(repr(c[:40]), "->", repr(PG.rewrite(c, p)))
print("claim", [(PG._CLAIM.search(c).group(0) if PG._CLAIM.search(c) else None) for c in cases])
import amo_question_guard as QG
for user, reply in [("今天光在摸鱼", "（伸了个懒腰，靠着椅背） 那咱俩半斤八两，我这也是一下午没干出什么正事。 不过白拿一杯奶茶，也算没白来。"),
                    ("哈哈哈", "我还在家呢，快到了。")]:
    print("Q", user, "->", repr(QG.answer(user, p, reply)))
import amo_state_consistency as SC
print("scene", SC._scene(p))
print("attach", repr(SC.rewrite("我还在家呢，快到了。", p, "哈哈哈")))
print("cook", repr(SC.rewrite("老公你快看这个视频 这狗子傻得跟我有得一拼 我等会儿弄点吃的自己煮的面，差点笑喷出来", p, "没问题，老婆，说句我爱你给我听听")))
print("BADCOOK", SC._BAD_COOK.pattern)
import amo_persona_guard as PERS
for t in ["（把睡衣领口往上拢了拢，笑得有点得意） 少来，我这身材也就中看。犯错误了可得自己负责啊。",
          "（窝在沙发上，把手机举高一点看着） 又是bug啊，这次卡哪儿了？我这边刚洗完澡，趴着呢。",
          "（把睡衣袖子往上撸了撸，笑着哼一声） 少来，就这一回。你bug修完了没？",
          "（夹了一筷子菜，抬头笑了一下） 嗯，我跟我妈先吃。 你那边忙完了没？"]:
    print("P", repr(t[:30]), "->", PERS.prepare_outbound(p, "umo", t, "chat"))
