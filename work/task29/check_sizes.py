import os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
B = os.path.join(ROOT, "work", "task29", "before")
A = os.path.join(ROOT, "work", "task29", "after")
for name in ("amo_reply_pipeline.py", "amo_state_consistency.py", "amo_question_guard.py", "amo_grounding_critic.py"):
    print(name, "before", os.path.getsize(os.path.join(B, name)), "after", os.path.getsize(os.path.join(A, name)))
