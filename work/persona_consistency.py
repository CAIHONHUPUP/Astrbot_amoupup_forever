# -*- coding: utf-8 -*-
"""扫描代码提示词里与“同城不同住/各回各家”人格冲突的说法。"""
import ast, json, os, sqlite3, sys
from typing import Any
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get('AMO_DB') or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), 'data_v4.db')
FORBIDDEN = ('名义上同居', '住在一起', '一起回家', '同居')
ALLOWED = ('坐在对面', '他就在你对面', '面对面')
hits: list[dict[str, Any]] = []
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in ('__pycache__', '.test-tmp', '.pytest_cache')]
    for fn in fns:
        if not fn.endswith('.py'): continue
        p = os.path.join(dp, fn); rel = os.path.relpath(p, ROOT).replace(chr(92), '/')
        try: src = open(p, encoding='utf-8').read()
        except Exception: continue
        try: tree = ast.parse(src)
        except SyntaxError: continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                for word in FORBIDDEN:
                    if word in text and not any(a in text for a in ALLOWED):
                        hits.append({'file': rel, 'line': getattr(node, 'lineno', 0), 'word': word, 'text': text[:100]})
                        break
con = sqlite3.connect('file:%s?mode=ro' % DB, uri=True)
persona = con.execute("SELECT system_prompt FROM personas WHERE persona_id='大肥鱼'").fetchone()
con.close()
ptext = persona[0] if persona else ''
persona_hits = [w for w in FORBIDDEN if w in ptext]
report = {'prompt_hits': hits, 'count': len(hits), 'persona_len': len(ptext), 'persona_hits': persona_hits}
report_path = os.environ.get('AMO_PERSONA_REPORT') or ''
if report_path:
    open(report_path, 'w', encoding='utf-8').write(
        json.dumps(report, ensure_ascii=False, indent=1))
print('prompt_hits', len(hits), 'persona_hits', persona_hits)
for h in hits[:15]: print(' ', h['file'], h['line'], h['word'], '|', h['text'][:70])