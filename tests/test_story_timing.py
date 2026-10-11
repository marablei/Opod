import sys; sys.path.insert(0, ".")
from omni_client import validators as V
tr = [{"id": i, "start_sec": i * 10.0, "end_sec": i * 10.0 + 9, "text": f"line {i}"} for i in range(1, 61)]   # 600 s video
si = {"video_id": "x", "duration_sec": 600.0, "transcript": tr}
pt = lambda n: {"segment_id": f"seg_{n}", "function": "a step", "note": "A sentence."}
good = {"act_1_hook": pt(2), "act_1_anchor": pt(10), "act_2_escalation": [pt(25), pt(40)], "act_3_payoff": pt(50), "act_3_closing": pt(58)}
assert V.validate_story(good, si) == [], V.validate_story(good, si)
early = dict(good, act_3_payoff=pt(7), act_3_closing=pt(8), act_2_escalation=[pt(5)])    # the BETRAYAL pattern: everything in the first ~70 s
p = V.validate_story(early, si); print(p)
assert any("act_3_closing" in x for x in p) and any("act_3_payoff" in x for x in p)
short = {"video_id": "x", "duration_sec": 60.0, "transcript": tr[:6]}
assert V.validate_story({"act_1_hook": pt(1), "act_1_anchor": pt(2), "act_2_escalation": [pt(3)], "act_3_payoff": pt(4), "act_3_closing": pt(5)}, short) == []
print("PASS story timing")
