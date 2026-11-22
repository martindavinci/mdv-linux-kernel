#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Where the work stands, computed from recorded state, not from memory.

  mlk.py next   [--repo DIR] [--series NAME] [--upstream REF] [--netdev | --no-netdev] [--json]
  mlk.py report [--repo DIR] [--series NAME] [--upstream REF] [--netdev | --no-netdev]
                [--out PATH]

`next` walks the gates of the phase map in order (setup, find-work,
develop, revise, validate, gate, submit) over the evidence store, the
policy snapshot, the commits of the series, b4's series info and the review
tracker, and stops at the first gate with blocking items: that is the phase.
It prints what blocks and the next step, with who runs it. Anything that
signs, sends or authenticates is always `who: human`.

A warning-level item (a failed or stale check, a policy finding, a missing
note) stops blocking once a `human_confirmations` note names its id, as
written by `evidence.py note --key human_confirmations --value "<ID>: ..."`.
DOC_CHANGED_UPSTREAM and DOC_MODIFIED also clear when the `policy` note
names them (the agent read the right version of the document).
A missing required check, a dirty working copy or a missing human
sign-off cannot be confirmed away.

`report` writes one self-contained HTML page (no scripts, no external
resources, everything escaped) to <series dir>/report.html, or --out: the
state, the next step, every check, the human's confirmations, the review
comments and each patch with its diff. It is a view of the same state; it
decides nothing on its own. Keep it local: a patch under embargo must not
leave the machine.

Exit: 0 ready for the human to send (next) / report written, 1 something
blocks, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys

import evidence
import mlk_common
import policy_snapshot

SCHEMA = 1
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
KERNEL_MARKERS = ["MAINTAINERS", "Makefile", "scripts/checkpatch.pl"]
NETDEV_PATHS = ("net/", "drivers/net/", "include/net/", "tools/testing/selftests/net/",
                "tools/testing/selftests/drivers/net/", "include/linux/netdevice.h",
                "include/linux/skbuff.h")
# Commands only the human runs (non-overridable rules); a step matching one of
# these is forced to who=human whatever the gate said.
HUMAN_ONLY = re.compile(r"\bb4 send\b|\bgit send-email\b|--signoff\b|\bb4 prep (?:-n|--enroll)\b|"
                        r"\bb4 ty\b|\bsendmail\b|\bmsmtp\b|\bpatatt genkey\b|--web-auth", re.I)
NOTE_GATES = [
    ("validate", "security_screen", "references/security.md",
     "screen the change for security impact before any external review"),
    ("gate", "comprehension", "references/comprehension.md",
     "the human answers the comprehension questions about the patch"),
    ("gate", "disclosure", "references/disclosure.md",
     "the AI-use disclosure the target tree asks for is written"),
]
MAX_DIFF_LINES = 3000
DOC_FINDINGS = {"DOC_CHANGED_UPSTREAM", "DOC_MODIFIED"}


def item(id_: str, state: str, why: str, *, do: str, cmd: str | None = None,
         who: str = "agent", doc: str | None = None, confirmable: bool = True) -> dict:
    if cmd and HUMAN_ONLY.search(cmd):
        who = "human"
    return {"id": id_, "state": state, "why": why, "doc": doc, "confirmable": confirmable,
            "next": {"who": who, "do": do, "cmd": cmd}}


class Context:
    def __init__(self, repo: str, series: str | None, upstream: str | None,
                 netdev: bool | None):
        self.repo = repo
        self.branch = mlk_common.current_branch(repo)
        self.series = series or os.environ.get("MLK_SERIES") or self.branch
        self.upstream = upstream
        self.store = evidence.Store(repo, self.series) if self.series else None
        self.data = self.store.load() if self.store and self.store.exists() else None
        self.b4 = self._b4_info()
        self.base = (self.data or {}).get("base") or self.b4.get("base-commit") \
            or self._merge_base()
        self.cover: str | None = None  # b4's cover-letter commit, set by _commits()
        self.commits = self._commits()
        self.netdev = self._netdev() if netdev is None else netdev
        self.state = evidence.current_state(repo, self.base) if self.base else None
        self.entries = evidence.evaluated(self.store)[2] if self.data else []
        self.notes = (self.data or {}).get("notes", {})
        self.confirmations = " ".join(n["value"] for n in self.notes.get("human_confirmations", []))

    def _b4_info(self) -> dict:
        if not (self.branch or "").startswith("b4/") or not shutil.which("b4"):
            return {}
        try:
            res = subprocess.run(["b4", "prep", "--show-info"], cwd=self.repo,
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return {}
        info = {}
        for line in res.stdout.splitlines() if res.returncode == 0 else []:
            key, sep, value = line.partition(":")
            if sep:
                info[key.strip()] = value.strip()
        return info

    def _merge_base(self) -> str | None:
        ref = self.upstream or "@{upstream}"
        res = mlk_common.run_git(["merge-base", "HEAD", ref], self.repo)
        return res.stdout.strip() if res.returncode == 0 and res.stdout.strip() else None

    def _commits(self) -> list[str]:
        if not self.base:
            return []
        shas = mlk_common.commits_in_range(self.repo, f"{self.base}..HEAD")
        # b4's cover-letter commit is not a patch.
        covers = [s for s in shas
                  if mlk_common.B4_TRACKING in mlk_common.commit_message(self.repo, s)]
        self.cover = covers[0] if covers else None
        return [s for s in shas if s not in covers]

    def _netdev(self) -> bool:
        if "net" in self.b4.get("prefixes", "").split() or \
                "net-next" in self.b4.get("prefixes", "").split():
            return True
        if not self.base:
            return False
        names = mlk_common.git(["diff", "--name-only", f"{self.base}..HEAD"], self.repo)
        return any(n.startswith(NETDEV_PATHS) for n in names.splitlines())

    def confirmed(self, id_: str) -> bool:
        text = self.confirmations
        if id_ in DOC_FINDINGS:
            # Reading the right version of a document is the agent's job: the
            # policy note that records it ("findings: DOC_CHANGED_UPSTREAM ...") counts.
            text += " " + " ".join(n["value"] for n in self.notes.get("policy", []))
        return re.search(rf"(?<![\w-]){re.escape(id_)}(?![\w-])", text) is not None


def skill_cmd(script: str, args: str) -> str:
    return f"python3 <skill-dir>/scripts/{script} {args}".rstrip()


def gate_setup(ctx: Context) -> list[dict]:
    missing = [m for m in KERNEL_MARKERS if not os.path.exists(os.path.join(ctx.repo, m))]
    if missing:
        return [item("kernel-tree", "missing", f"not a kernel checkout (no {', '.join(missing)})",
                     do="work inside a kernel checkout; set one up with references/setup.md",
                     cmd=skill_cmd("env_check.py", ""), doc="references/setup.md",
                     confirmable=False)]
    out = []
    try:
        snap = policy_snapshot.snapshot(ctx.repo, ctx.upstream)
    except (mlk_common.GitError, ValueError) as exc:
        return [item("policy-snapshot", "fail", str(exc), do="fix the git state",
                     confirmable=False)]
    fixes = {"UPSTREAM_MISSING": ("say which remote is the target tree, then fetch it",
                                  "git fetch <remote>"),
             "STALE_UPSTREAM": ("fetch the target tree (read-only)", "git fetch <remote>"),
             "BEHIND_UPSTREAM": ("suggest rebasing onto the fresh upstream", None),
             "STALE_CHECKOUT": ("suggest rebasing onto the fresh upstream", None),
             "DOC_MODIFIED": ("read the committed version of the document", None),
             "DOC_CHANGED_UPSTREAM": ("read the upstream version: that rule applies", None)}
    for f in snap["findings"]:
        if f["severity"] == "info":
            continue
        do, cmd = fixes.get(f["code"], ("act on the policy finding", None))
        out.append(item(f["code"], "warn", f["message"], do=do, cmd=cmd,
                        doc="SKILL.md, Follow the tree"))
    return out


def gate_find_work(ctx: Context) -> list[dict]:
    if not ctx.series:
        return [item("branch", "missing", "detached HEAD and no --series",
                     do="switch to the series branch (b4 prep -n is the human's) or pass --series",
                     confirmable=False)]
    if not ctx.base:
        return [item("base", "missing", "no evidence store, no b4 base and no upstream to "
                     "measure the series from", do="initialise the evidence store on the base",
                     cmd=skill_cmd("evidence.py", "init --base <base>"),
                     doc="references/develop.md", confirmable=False)]
    if not ctx.commits:
        return [item("no-commits", "missing", f"no patches on top of {ctx.base[:12]}",
                     do="pick work (find-work) or write the change (develop)",
                     doc="references/find-work.md", confirmable=False)]
    return []


def gate_develop(ctx: Context) -> list[dict]:
    out = []
    if ctx.state and ctx.state["dirty"]:
        out.append(item("dirty", "fail", "uncommitted or untracked changes: every recorded "
                        "result is stale", do="commit or stash the changes", cmd="git status",
                        confirmable=False))
    if not ctx.data:
        out.append(item("evidence-store", "missing", "no evidence store for this series",
                        do="initialise it on the series base",
                        cmd=skill_cmd("evidence.py", f"init --base {ctx.base[:12]}"),
                        doc="references/develop.md", confirmable=False))
    elif "policy" not in ctx.notes:
        out.append(item("policy", "missing", "the process documents read are not recorded",
                        do="read them from the tree and record what was read",
                        cmd=skill_cmd("evidence.py", 'note --key policy --value "<head>; docs '
                                      'read: ...; findings: ..."'),
                        doc="SKILL.md, Follow the tree"))
    return out


def gate_revise(ctx: Context) -> list[dict]:
    payload = review_status(ctx)
    if not payload:
        return []
    out = []
    for f in payload.get("findings", []):
        if f.get("severity") == "info" or f.get("code") == "POSTED_LESS_THAN_24H":
            continue
        if f.get("code") == "APPLIED":
            out.append(item("review:applied", "applied", f.get("message", ""),
                            do="no new version: send later fixes as new patches on top, "
                               "with Fixes:",
                            doc="references/revise.md", confirmable=False))
            continue
        # Review comments are resolved in the tracker (propose/confirm), not here.
        cid = (f.get("message") or "").partition(": ")[0] or str(len(out) + 1)
        proposed = f.get("code") == "PROPOSED"
        out.append(item(f"review:{cid}", "proposed" if proposed else "open",
                        f.get("message", "") + (f" ({f['detail']})" if f.get("detail") else ""),
                        do=("the human confirms or rejects the proposal" if proposed else
                            "address it in the code, or reply; then propose its state"),
                        cmd=skill_cmd("review_tracker.py", f"confirm {cid} --words \"...\""
                                      if proposed else "list --status open"),
                        who="human" if proposed else "agent",
                        doc="references/revise.md", confirmable=False))
    return out


def review_status(ctx: Context) -> dict | None:
    script = os.path.join(SCRIPTS, "review_tracker.py")
    if not os.path.exists(script) or not ctx.series:
        return None
    cmd = [sys.executable, script, "status", "--repo", ctx.repo, "--series", ctx.series,
           "--json"] + (["--netdev"] if ctx.netdev else [])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return json.loads(res.stdout) if res.returncode in (0, 1) else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


SHA_RANGE_RE = re.compile(r"\b([0-9a-f]{7,40})\.\.\.?(?:[0-9a-f]{7,40}\b|HEAD\b)")


def fresh_ranges(cmd: str, ctx: Context) -> str:
    """Rewrite commit ranges recorded as literal shas to the current series.

    The shell expands "$RANGE" before evidence.py sees it, so a recorded
    command names the commits of that moment. After any rewrite (cover edit,
    trailers, sign-off) re-running it as recorded would check the old commits
    and record a pass for the new HEAD.
    """
    if not ctx.base:
        return cmd

    def repl(m: re.Match) -> str:
        start = ctx.base if ctx.base.startswith(m.group(1)) else (ctx.cover or ctx.base)
        return f"{start[:12]}..HEAD"

    return SHA_RANGE_RE.sub(repl, cmd)


def rerun_cmd(entry: dict, ctx: Context | None = None) -> str | None:
    if not entry.get("cmd"):
        return None
    flags = [f"--name {entry['name']}", f"--category {entry['category']}"]
    flags += [f"--input {path}" for path in entry.get("inputs") or {}]
    cmd = fresh_ranges(entry["cmd"], ctx) if ctx else entry["cmd"]
    return skill_cmd("evidence.py", "run " + " ".join(flags) + " -- " + cmd)


def gate_validate(ctx: Context) -> list[dict]:
    if not ctx.data:
        return []
    out = []
    if not ctx.data.get("required"):
        preset = "netdev" if ctx.netdev else "generic"
        out.append(item("required", "missing", "no required checks declared",
                        do="declare the checks this tree expects",
                        cmd=skill_cmd("evidence.py", f"require --preset {preset}"),
                        doc="references/validate.md", confirmable=False))
    for e in ctx.entries:
        st = e["effective_status"]
        if st == "missing":
            out.append(item(e["name"], "missing", f"required: {e.get('why', '')}",
                            do="run the check as references/validate.md describes",
                            doc="references/validate.md", confirmable=False))
        elif st in ("fail", "stale"):
            why = e.get("reason") or ("only commit messages changed since it ran"
                                      if e.get("code_unchanged") else "")
            out.append(item(e["name"], st, why or f"{st} result",
                            do="fix it and re-run" if st == "fail" else "re-run it",
                            cmd=rerun_cmd(e, ctx), doc="references/validate.md"))
    out += note_items(ctx, "validate")
    return out


def note_items(ctx: Context, phase: str) -> list[dict]:
    out = []
    for gate, key, doc, why in NOTE_GATES:
        if gate == phase and key not in ctx.notes:
            out.append(item(key, "missing", why, do=f"follow {doc} and record the outcome",
                            cmd=skill_cmd("evidence.py", f'note --key {key} --value "..."'),
                            doc=doc))
    return out


def gate_gate(ctx: Context) -> list[dict]:
    return note_items(ctx, "gate") if ctx.data else []


def gate_submit(ctx: Context) -> list[dict]:
    if not ctx.data:
        return []
    out = []
    unsigned = [s for s in ctx.commits
                if not any(k.lower() == "signed-off-by" for k, _ in
                           mlk_common.trailers(mlk_common.commit_message(ctx.repo, s)))]
    if unsigned:
        # From the b4 cover commit, never the base: a sign-off appended to the
        # cover lands after b4's tracking data and b4 can no longer read it.
        start = '"$(b4 prep --show-info start-commit)"' if ctx.cover else ctx.base[:12]
        out.append(item("signoff", "missing",
                        f"{len(unsigned)} patch(es) without the human's Signed-off-by",
                        do="the human certifies the DCO and signs off; the agent never does",
                        cmd=f"git rebase --signoff {start}", who="human",
                        doc="references/signoff-dco.md", confirmable=False))
    names = {e["name"]: e["effective_status"] for e in ctx.entries}
    if names.get("preflight") != "pass":
        flag = " --netdev" if ctx.netdev else ""
        out.append(item("preflight", names.get("preflight", "missing"),
                        "the outgoing mails were not checked on this exact state",
                        do="export the series (b4 prep -p sends nothing) and check the mails",
                        cmd=skill_cmd("evidence.py", "run --name preflight --category style -- "
                                      f"python3 <skill-dir>/scripts/preflight.py "
                                      f"\"$SERIES/patches\"{flag}"),
                        doc="references/submit.md"))
    payload = review_status(ctx) or {}
    for f in payload.get("findings", []):
        if f.get("code") == "POSTED_LESS_THAN_24H":
            out.append(item("POSTED_LESS_THAN_24H", "warn", f.get("message", ""),
                            do="wait: netdev wants at least 24 hours between postings",
                            doc="references/netdev.md"))
    return out


# The phase map as data: gates are checked in this order, the first one with
# blocking items is the phase.
GATES = [("setup", gate_setup), ("find-work", gate_find_work), ("develop", gate_develop),
         ("revise", gate_revise), ("validate", gate_validate), ("gate", gate_gate),
         ("submit", gate_submit)]
READY_STEP = {"who": "human", "cmd": "b4 send --dry-run; b4 send --reflect; b4 send",
              "do": "everything recorded is current: the human reviews the evidence summary "
                    "and sends (references/submit.md); the agent never sends"}


def compute(ctx: Context) -> dict:
    blocking: list[dict] = []
    confirmed: list[dict] = []
    phase = "ready"
    for name, gate in GATES:
        for it in gate(ctx):
            (confirmed if it["confirmable"] and ctx.confirmed(it["id"]) else blocking).append(it)
        if blocking:
            phase = name
            break
    info = []
    if phase in ("gate", "submit", "ready") and "external_review" not in ctx.notes:
        info.append("optional external review not recorded (references/external-review.md)"
                    + ("; netdev strongly encourages an LLM review before posting"
                       if ctx.netdev else ""))
    if phase in ("submit", "ready"):
        info.append("before any handover: signoff_guard.py check \"$BASE..HEAD\" "
                    "(SKILL.md, Self-check)")
    return {"schema": SCHEMA, "series": ctx.series, "phase": phase, "netdev": ctx.netdev,
            "base": ctx.base, "state": ctx.state, "patches": len(ctx.commits),
            "blocking": blocking, "confirmed": confirmed, "info": info,
            "next": blocking[0]["next"] if blocking else READY_STEP}


def human_next(p: dict) -> str:
    head = f"series {p['series'] or '-'}: phase {p['phase']}"
    if p["state"]:
        head += f", HEAD {p['state']['head'][:12]}{' (dirty)' if p['state']['dirty'] else ''}"
    lines = [head + f", {p['patches']} patch(es)" + (", netdev" if p["netdev"] else "")]
    for it in p["blocking"]:
        lines.append(f"  BLOCK {it['state'].upper():7} {it['id']}: {it['why']}")
    for it in p["confirmed"]:
        lines.append(f"  CONFIRMED          {it['id']}: {it['why']}")
    for text in p["info"]:
        lines.append(f"  info: {text}")
    nxt = p["next"]
    lines.append(f"next ({nxt['who']}): {nxt['do']}")
    if nxt.get("cmd"):
        lines.append(f"  {'[HUMAN RUNS] ' if nxt['who'] == 'human' else ''}{nxt['cmd']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- report

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a64;--line:#dcd9d0;--card:#fff;
--pass:#1f7a3d;--fail:#b3261e;--stale:#9a6200;--missing:#7a3fb3;--code:#f2f0ea;--human:#fff4d6}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--muted:#a3a198;
--line:#3a3934;--card:#1f1f1c;--pass:#5cc27f;--fail:#ff8a80;--stale:#e0a64a;--missing:#c49bf0;
--code:#262622;--human:#3b3220}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.1rem;margin:32px 0 8px;
border-bottom:1px solid var(--line);padding-bottom:4px}
.muted{color:var(--muted)}table{width:100%;border-collapse:collapse;font-size:.92rem}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.wrap{overflow-x:auto}code,pre{font:12.5px/1.45 ui-monospace,Consolas,monospace}
pre{background:var(--code);padding:10px;overflow-x:auto;margin:6px 0;white-space:pre}
.st{font-weight:600}.pass{color:var(--pass)}.fail{color:var(--fail)}
.stale,.warn,.open{color:var(--stale)}.missing{color:var(--missing)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.human{background:var(--human)}details{margin:10px 0}summary{cursor:pointer;font-weight:600}
.add{color:var(--pass)}.del{color:var(--fail)}.hunk{color:var(--muted)}
"""


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def diff_html(text: str) -> str:
    lines = text.splitlines()
    cut = len(lines) > MAX_DIFF_LINES
    out = []
    for line in lines[:MAX_DIFF_LINES]:
        cls = ("add" if line.startswith("+") and not line.startswith("+++") else
               "del" if line.startswith("-") and not line.startswith("---") else
               "hunk" if line.startswith("@@") else "")
        out.append(f'<span class="{cls}">{esc(line)}</span>' if cls else esc(line))
    if cut:
        out.append(esc(f"[... {len(lines) - MAX_DIFF_LINES} more lines not shown]"))
    return "<pre>" + "\n".join(out) + "</pre>"


def render_report(ctx: Context, p: dict) -> str:
    st = p["state"] or {}
    nxt = p["next"]
    h = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
         "style-src 'unsafe-inline'\">",
         f"<title>Evidence {esc(p['series'])}</title><style>{CSS}</style></head><body><main>",
         f"<h1>{esc(p['series'])}</h1>",
         f'<p class="muted">phase <b>{esc(p["phase"])}</b> · {p["patches"]} patch(es) · base '
         f"<code>{esc((p['base'] or '-')[:12])}</code> · HEAD <code>{esc(st.get('head', '-')[:12])}"
         f"</code>{' · <b class=fail>working copy modified</b>' if st.get('dirty') else ''}"
         f"{' · netdev' if p['netdev'] else ''} · generated {esc(evidence.now_iso())}</p>",
         "<p class=\"muted\">Local page: do not upload or share it while the patch is not "
         "public.</p>",
         f'<h2>Next</h2><div class="card{" human" if nxt["who"] == "human" else ""}">',
         f"<p><b>{'The human runs this' if nxt['who'] == 'human' else 'Agent'}:</b> "
         f"{esc(nxt['do'])}</p>"]
    if nxt.get("cmd"):
        h.append(f"<pre>{esc(nxt['cmd'])}</pre>")
    h.append("</div>")
    if p["blocking"] or p["confirmed"]:
        h.append('<h2>Blocking</h2><div class="wrap"><table><tr><th>Item</th><th>State</th>'
                 "<th>Why</th><th>Next</th></tr>")
        for it in p["blocking"]:
            h.append(f"<tr><td><code>{esc(it['id'])}</code></td><td class=\"st "
                     f"{esc(it['state'])}\">{esc(it['state'])}</td><td>{esc(it['why'])}</td>"
                     f"<td>{esc(it['next']['who'])}: {esc(it['next']['do'])}</td></tr>")
        for it in p["confirmed"]:
            h.append(f"<tr><td><code>{esc(it['id'])}</code></td><td class=\"st\">confirmed"
                     f"</td><td>{esc(it['why'])}</td><td>accepted by the human</td></tr>")
        h.append("</table></div>")
    h.append("<h2>Checks</h2>")
    if ctx.entries:
        h.append('<div class="wrap"><table><tr><th>Check</th><th>Category</th><th>Status</th>'
                 "<th>Command</th></tr>")
        for e in ctx.entries:
            s = e["effective_status"]
            h.append(f"<tr><td>{esc(e['name'])}</td><td>{esc(e['category'])}</td>"
                     f"<td class=\"st {esc(s)}\">{esc(evidence.status_label(e))}</td>"
                     f"<td><code>{esc(e.get('cmd') or '-')}</code></td></tr>")
        h.append("</table></div>")
    else:
        h.append("<p>No checks recorded: nothing has been validated.</p>")
    h.append("<h2>Human confirmations and notes</h2>")
    if ctx.notes:
        for key, values in ctx.notes.items():
            for note in values:
                h.append(f"<p><b>{esc(key)}</b> <span class=\"muted\">{esc(note['time'])}"
                         f"</span><br>{esc(note['value'])}</p>")
    else:
        h.append("<p>No notes recorded.</p>")
    review = review_status(ctx)
    if review is not None:
        h.append("<h2>Review comments</h2>")
        findings = review.get("findings", [])
        if findings:
            h.append("<ul>" + "".join(f"<li><code>{esc(f.get('code'))}</code> "
                                      f"{esc(f.get('message'))}</li>" for f in findings)
                     + "</ul>")
        else:
            h.append("<p>Every comment has a confirmed state.</p>")
    for text in p["info"]:
        h.append(f'<p class="muted">{esc(text)}</p>')
    h.append("<h2>Patches</h2>")
    total = len(ctx.commits)
    for n, sha in enumerate(ctx.commits, start=1):
        msg = mlk_common.commit_message(ctx.repo, sha)
        diff = mlk_common.git(["show", "--format=", "--patch", "--stat", sha, "--"], ctx.repo)
        h.append(f"<details{' open' if total == 1 else ''}><summary>[{n}/{total}] "
                 f"{esc(msg.splitlines()[0] if msg else sha)} <span class=\"muted\">"
                 f"{esc(sha[:12])}</span></summary><pre>{esc(msg)}</pre>{diff_html(diff)}"
                 "</details>")
    if nxt["who"] == "human" or p["phase"] in ("submit", "ready"):
        h.append('<h2>Commands for the human</h2><div class="card human"><p>Only the human '
                 "runs these. The agent prepared them and ran none of them.</p>"
                 f"<pre>{esc(READY_STEP['cmd'].replace('; ', chr(10)))}</pre></div>")
    h.append("</main></body></html>\n")
    return "\n".join(h)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase, blockers and next step of a series.")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("next", "report"):
        p = sub.add_parser(name)
        p.add_argument("--repo", default=".")
        p.add_argument("--series")
        p.add_argument("--upstream", metavar="REF")
        g = p.add_mutually_exclusive_group()
        g.add_argument("--netdev", dest="netdev", action="store_true", default=None)
        g.add_argument("--no-netdev", dest="netdev", action="store_false")
        if name == "next":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--out", metavar="PATH")
    args = ap.parse_args(argv)
    try:
        ctx = Context(mlk_common.repo_root(args.repo), args.series, args.upstream, args.netdev)
        payload = compute(ctx)
        if args.command == "next":
            mlk_common.emit(payload, args.json, human_next)
            return mlk_common.EXIT_OK if payload["phase"] == "ready" else mlk_common.EXIT_FINDINGS
        if not ctx.store:
            raise ValueError("detached HEAD: pass --series NAME")
        out = args.out or os.path.join(ctx.store.dir, "report.html")
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(render_report(ctx, payload))
        print(out)
        return mlk_common.EXIT_OK
    except (evidence.UsageError, mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
