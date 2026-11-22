
import json, glob, io, sys
out = io.StringIO()
for p in sorted(glob.glob('work/audit/holdout/daily_seed2026*.json')):
    d = json.load(open(p, encoding='utf-8'))
    t=d['totals']; dr=d['drift']; c=d['control']; cp=d['clean_probe_turns']; cpu=d['clean_probe_unanswered']
    rep,res=dr['repaired'],dr['residual']
    out.write('SEED %s groups %d/%d turn %.2f%% clean_probes %d clean_unanswered %d ctrl_failed %d res_rate %.1f%% gi %d %s prompt_bad %d ger %d\n' % (
        d['seed'], t['groups_passed'], t['groups'], 100*t['turn_pass_rate'], cp, cpu, c['failed'],
        100.0*res/max(1,rep+res), dr['guard_introduced'], dr['guard_introduced_breakdown'],
        d['context']['prompt_mismatch'], d['context']['guard_errors']))
    for f in d['failed_group_details']:
        for s in f['samples']:
            if (s['drift'] is None) or f['control']:
                out.write('  CLEANFAIL %s %s %s t%s %s probe=%s cand=%r sent=%r codes=%s\n' % (
                    f['gid'], f['plan'], 'ctrl' if f['control'] else '', s['turn'], s['time'], s['probe'],
                    s['candidate'], s['sent'], s['sent_codes']))
open('work/audit/holdout/daily_analysis.txt','w',encoding='utf-8').write(out.getvalue())
print('ok')
