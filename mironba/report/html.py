"""One self-contained HTML file from a completed run.

    python -m mironba.report.html --out docs/example-run.html

No server, no build step, no external requests: the output is a single file
that opens from the filesystem. That is a deliberate ceiling on this phase —
a FastAPI surface is only worth adding once the static version is right, and
the static version is what a reader can check into a repo and open in a year.

## What it must not do

The surface is the first thing anyone sees, and a demo that reads as a working
predictor would misrepresent every measurement behind it. So the limitations
are not a footer:

- the headline band states the precision figure **before** any timeline
- the counterfactual branch is marked unfalsifiable in its own header, not in
  a caption underneath
- refusals are styled as first-class events, because refusal is the measured
  behaviour of this system
- ``LIMITATIONS`` from the report agent is rendered verbatim and a test asserts
  every line reaches the HTML

The generator escapes everything it interpolates. Event payloads contain model
output, and model output is untrusted text.
"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

from mironba.agents.report import LIMITATIONS, Report
from mironba.report import use_utf8_stdout
from mironba.report.timeline import Feed, load_run

CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --dim:#666; --line:#e2e2e2;
        --refuse:#b3261e; --refuse-bg:#fdf2f1; --ok:#1d6b3a; --accent:#25457a; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#15171a; --fg:#e8e8e8; --dim:#9aa0a6; --line:#2c3036;
          --refuse:#ff8a80; --refuse-bg:#2a1c1b; --ok:#7ddc9e; --accent:#8ab4f8; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:16px/1.6
       -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width:900px; margin:0 auto; padding:2rem 1.25rem 4rem; }
h1 { font-size:1.6rem; margin:0 0 .25rem; }
h2 { font-size:1.15rem; margin:2.5rem 0 .75rem; padding-bottom:.35rem;
     border-bottom:1px solid var(--line); }
h3 { font-size:1rem; margin:1.5rem 0 .5rem; }
.sub { color:var(--dim); margin:0 0 1.5rem; }
.band { border:1px solid var(--refuse); background:var(--refuse-bg);
        border-radius:8px; padding:1rem 1.15rem; margin:1.5rem 0; }
.band strong { color:var(--refuse); }
.band ul { margin:.5rem 0 0; padding-left:1.2rem; }
.band li { margin:.2rem 0; }
.cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr));
        gap:1.25rem; }
.branch { border:1px solid var(--line); border-radius:8px; padding:1rem; }
.branch.cf { border-style:dashed; }
.tag { display:inline-block; font-size:.7rem; letter-spacing:.06em;
       text-transform:uppercase; padding:.15rem .5rem; border-radius:4px;
       background:var(--line); color:var(--dim); margin-left:.4rem; }
.tag.cf { background:var(--refuse); color:#fff; }
.tag.actual { background:var(--ok); color:#fff; }
ol.feed { list-style:none; margin:0; padding:0; }
ol.feed li { border-left:2px solid var(--line); padding:.4rem 0 .4rem .9rem;
             margin:0; }
ol.feed li.refuse { border-left-color:var(--refuse); background:var(--refuse-bg); }
.meta { color:var(--dim); font-size:.8rem; font-variant-numeric:tabular-nums; }
.why { color:var(--dim); font-style:italic; font-size:.9rem;
       margin:.2rem 0 0; padding-left:.5rem; border-left:2px solid var(--line); }
table { border-collapse:collapse; width:100%; font-size:.9rem; }
th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); }
.scroll { overflow-x:auto; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em; }
footer { margin-top:3rem; padding-top:1rem; border-top:1px solid var(--line);
         color:var(--dim); font-size:.85rem; }
"""


def e(text) -> str:
    return html.escape(str(text), quote=True)


def _feed_html(feed: Feed) -> str:
    items = []
    for entry in feed.entries:
        cls = " class=\"refuse\"" if entry.notable else ""
        why = (
            f'<p class="why">&ldquo;{e(entry.reasoning.strip())}&rdquo;</p>'
            if entry.reasoning else ""
        )
        items.append(
            f'<li{cls}><span class="meta">{e(entry.clock)} &middot; '
            f'#{entry.seq} &middot; {e(entry.actor)}</span><br>'
            f"{e(entry.headline)}{why}</li>"
        )
    return '<ol class="feed">' + "".join(items) + "</ol>"


def _branch_html(name: str, feed: Feed, unfalsifiable: bool) -> str:
    tag = (
        '<span class="tag cf">counterfactual &mdash; unfalsifiable, never scored</span>'
        if unfalsifiable
        else '<span class="tag actual">actually happened</span>'
    )
    note = (
        "<p class=\"meta\">This branch has no ground truth and is not scored. "
        "It is what the simulation did, not evidence about the world.</p>"
        if unfalsifiable else ""
    )
    return (
        f'<div class="branch{" cf" if unfalsifiable else ""}">'
        f"<h3>{e(name)}{tag}</h3>{note}"
        f'<p class="meta">{len(feed.entries)} events, '
        f"<strong>{len(feed.refusals)}</strong> refusals or failures</p>"
        f"{_feed_html(feed)}</div>"
    )


def render_html(
    title: str,
    branches: dict[str, Feed],
    report: Report | None = None,
    unfalsifiable: tuple[str, ...] = (),
    headline: dict | None = None,
) -> str:
    numbers = headline or {
        "Deadline planner precision": "1 matched proposal in 421",
        "Predictive recall, non-stipulated signings": "0 of 1",
        "Validator legality on real trades": "5 of 5, on the 15% it can price",
        "Measured win-delta error": "10.48 wins (sd, n=180)",
    }
    rows = "".join(
        f"<tr><td>{e(k)}</td><td><strong>{e(v)}</strong></td></tr>"
        for k, v in numbers.items()
    )
    limits = "".join(f"<li>{e(item)}</li>" for item in LIMITATIONS)

    body = [
        '<div class="wrap">',
        f"<h1>{e(title)}</h1>",
        '<p class="sub">A simulation of a counterfactual NBA offseason. '
        "This page shows what the simulation did &mdash; not what will happen.</p>",
        '<div class="band"><strong>Read this first.</strong> This is not a '
        "predictor, and its measured accuracy is poor. The numbers below are "
        "the point of the project; the timeline underneath is an illustration "
        "of the machinery, not evidence that it works."
        f'<div class="scroll"><table>{rows}</table></div></div>',
        "<h2>Branches</h2>",
        '<div class="cols">',
    ]
    for name, feed in branches.items():
        body.append(_branch_html(name, feed, name in unfalsifiable))
    body.append("</div>")

    if report is not None:
        body.append("<h2>Report</h2>")
        for name, summary in report.branches.items():
            body.append(f"<h3>{e(name)}</h3><p>{e(summary.what_happened)}</p>")
            if summary.consequences:
                body.append(
                    "<p class=\"meta\">Consequences the event log supports:</p><ul>"
                    + "".join(f"<li>{e(c)}</li>" for c in summary.consequences)
                    + "</ul>"
                )
        if report.dropped:
            body.append(
                f'<p class="meta">{len(report.dropped)} sentence(s) were removed '
                "by the claim filter, which drops any sentence presenting a "
                "simulated outcome as a prediction or ranking options the value "
                "model cannot separate:</p><ul>"
                + "".join(f"<li><code>{e(d)}</code></li>" for d in report.dropped)
                + "</ul>"
            )

    body += [
        '<h2>Limitations</h2><div class="band"><ul>' + limits + "</ul></div>",
        "<footer>Generated by <code>python -m mironba.report.html</code> from a "
        "recorded run. Every line in the timeline traces to one event in that "
        "run&rsquo;s <code>events.jsonl</code> by sequence number.</footer>",
        "</div>",
    ]
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{e(title)}</title><style>{CSS}</style></head><body>"
        + "".join(body)
        + "</body></html>"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="*", help="run directories; default: latest two")
    parser.add_argument("--out", default="docs/example-run.html")
    parser.add_argument("--title", default="MiroNBA - LeBron 2026 scenario")
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    use_utf8_stdout()

    paths = [Path(p) for p in args.runs]
    if not paths:
        paths = sorted(
            (p.parent for p in Path("runs").glob("*/events.jsonl")),
            key=lambda p: p.name,
        )[-2:]
    if not paths:
        print("no runs found")
        return 1

    branches = {p.name: load_run(p) for p in paths}
    # The later run is treated as the counterfactual only when the caller says
    # so by naming it second; nothing here infers which branch is real.
    unfalsifiable = (paths[-1].name,) if len(paths) > 1 else ()

    report = None
    if not args.no_llm:
        try:
            from mironba.agents.report import build_report, report_client

            agent, _ = report_client()
            report = build_report(
                paths[0].name, branches, agent=agent, unfalsifiable=unfalsifiable
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  (no model available: {exc}; rendering without the report)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_html(args.title, branches, report, unfalsifiable), encoding="utf-8"
    )
    print(f"  wrote {out}  ({out.stat().st_size:,} bytes, self-contained)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
