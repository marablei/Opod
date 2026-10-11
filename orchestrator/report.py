"""
End-of-run report: <OUT>/_run_report.md (+ _run_report.json). Built only from files the run really wrote.

It reports what can be MEASURED by code (counts, timings, rejections, how varied the Director output is).
It does not say the training data is "good": whether the labels are RIGHT still needs a person to compare a
sample against the video (see the 'Spot-check' list at the end).
"""
import json
import os
import time
from pathlib import Path

from orchestrator import run_view


def _fmt_sec(x) -> str:
    return "-" if x is None else (f"{x/60:.1f} min" if x >= 90 else f"{x:.0f} s")


def build(out: Path, video_ids: list[str]) -> dict:
    vids = [run_view.video_summary(Path(out) / v) for v in video_ids if (Path(out) / v / "_state.json").exists()]
    totals = {"videos": len(vids), "fully_done": sum(v["fully_done"] for v in vids),
              "gpu_pipeline_sec": round(sum(v["total_sec"] for v in vids), 1), "rows_kept": {}, "rows_total": {}}
    for v in vids:
        for shard, kt in (v["qa"] or {}).items():
            totals["rows_kept"][shard] = totals["rows_kept"].get(shard, 0) + kt.get("kept", 0)
            totals["rows_total"][shard] = totals["rows_total"].get(shard, 0) + kt.get("total", 0)
    rate = os.environ.get("ORION_POD_PRICE_PER_HR")
    if rate:
        totals["estimated_cost_usd"] = round(totals["gpu_pipeline_sec"] / 3600 * float(rate), 2)
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "totals": totals, "videos": vids}


def _flags(v: dict) -> list[str]:
    f = []
    t = v["treatments"]
    n = sum(t.values())
    if n >= 4 and t.get("talking_head", 0) / n >= 0.9 and (v["quality"].get("shots") or 0) / max(1, n) > 4:
        f.append("Director: >=90% talking_head although the video has many cuts per chunk - check the beats")
    if v["beats_per_chunk"] is not None and v["beats_per_chunk"] < 1.5:
        f.append(f"Director: only {v['beats_per_chunk']} visual beats per chunk on average")
    dq = v["distinct_footage_queries"]
    if dq and dq[1] >= 4 and dq[0] / dq[1] < 0.6:
        f.append(f"Director: only {dq[0]} distinct footage queries in {dq[1]} - answers repeat")
    rp = v.get("repairs") or {}
    if rp.get("director_chunks") and rp.get("repaired", 0) / rp["director_chunks"] > 0.30:
        f.append(f"Director: {rp['repaired']} of {rp['director_chunks']} chunks needed code repairs ({rp.get('repair_types')}); too many to trust without a spot-check")
    rj = sum(v["rejected"].values())
    if rj:
        f.append(f"Judge rejected {rj} answer(s): " + ", ".join(f"{k} {c}" for k, c in v["rejected"].items()))
    p = v.get("stage_c_progress") or {}
    if p.get("gave_up"):
        f.append(f"Stage C gave up on {p['gave_up']} call(s) after retries (see rejected_claims.log)")
    f += v["warnings"]
    return f


def to_markdown(rep: dict) -> str:
    t = rep["totals"]
    L = [f"# Orion run report", f"_generated {rep['generated_at']}_", "",
         f"- Videos: **{t['fully_done']}/{t['videos']}** fully done",
         f"- Pipeline time (sum of stage times): **{_fmt_sec(t['gpu_pipeline_sec'])}**"
         + (f"  | estimated GPU cost at ${os.environ.get('ORION_POD_PRICE_PER_HR')}/h: **${t['estimated_cost_usd']}** (model load/setup time NOT included)" if "estimated_cost_usd" in t else "")]
    if t["rows_total"]:
        L.append("- SFT rows kept / produced: " + ", ".join(f"{k.replace('_sft','')} {t['rows_kept'][k]}/{t['rows_total'][k]}" for k in sorted(t["rows_total"])))
    L += ["", "## Per video", "",
          "| video | length | lang | chunks (min-max s) | shots | director | style | story | beats/chunk | time |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for v in rep["videos"]:
        q, qa = v["quality"], (v["qa"] or {})
        g = lambda k: f"{qa[k]['kept']}/{qa[k]['total']}" if k in qa else "-"
        cs = v["chunk_sec"]
        L.append(f"| {(v.get('title') or v['name'])[:48]} | {_fmt_sec(q.get('duration_sec'))} | {q.get('language') or '-'} | "
                 f"{v['chunks'] or '-'} ({cs['min']}-{cs['max']}) | {q.get('shots') or '-'} | {g('director_sft')} | {g('style_sft')} | "
                 f"{g('story_architect_sft')} | {v['beats_per_chunk'] or '-'} | {_fmt_sec(v['total_sec'])} |" if cs else
                 f"| {(v.get('title') or v['name'])[:48]} | {_fmt_sec(q.get('duration_sec'))} | {q.get('language') or '-'} | - | - | - | - | - | - | {_fmt_sec(v['total_sec'])} |")
    L += ["", "## Director variety (is the output generic?)", ""]
    for v in rep["videos"]:
        t_ = ", ".join(f"{k} {c}" for k, c in sorted(v["treatments"].items(), key=lambda x: -x[1])) or "none"
        dq = v["distinct_footage_queries"]
        L.append(f"- **{(v.get('title') or v['name'])[:48]}**: treatments: {t_}" + (f"; distinct footage queries {dq[0]}/{dq[1]}" if dq else ""))
    L += ["", "## Things to look at", ""]
    any_flag = False
    for v in rep["videos"]:
        for f in _flags(v):
            L.append(f"- {(v.get('title') or v['name'])[:48]}: {f}"); any_flag = True
    if not any_flag:
        L.append("- nothing flagged by the automatic checks")
    L += ["", "## What these numbers do NOT tell you", "",
          "- Whether the beats/labels are actually RIGHT. The checks prove the format, the timings, the quotes and the segment ids; "
          "they cannot see the video.",
          "- Spot-check: open 5 chunks per video in `stage_d_judge/director_validated.json`, play that time range, and compare the "
          "`visual_beats` with what you see. Write down how many are wrong; that number is the real accuracy.",
          "- Footage rows are held back (no retrieval layer yet). Arabic / mixed-language quality is untested.", ""]
    return "\n".join(L)


def write(out: Path, video_ids: list[str]) -> Path:
    rep = build(out, video_ids)
    (Path(out) / "_run_report.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    p = Path(out) / "_run_report.md"
    p.write_text(to_markdown(rep), encoding="utf-8")
    return p
