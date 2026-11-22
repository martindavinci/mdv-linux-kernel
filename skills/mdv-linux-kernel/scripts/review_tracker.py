#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Review tracker: every comment received on a posted series gets a state.

One file per series: <git-common-dir>/mdv-linux-kernel/series/<series>/
review.json. The series is --series, else $MLK_SERIES, else the current
branch. Unresolved comments block the next version (Gerrit style).

  review_tracker.py import MBOX [--me ADDR]... [--json]
  review_tracker.py add --patch N --text TEXT [--file F] [--author NAME]
                        [--source human|bot] [--version V]
  review_tracker.py list [--status S]... [--json]
  review_tracker.py propose ID --status addressed|replied|wontfix --why TEXT [--commit SHA]
  review_tracker.py confirm ID --words TEXT [--status S] [--commit SHA]
  review_tracker.py reopen ID --why TEXT
  review_tracker.py status [--netdev] [--now ISO] [--json]
  review_tracker.py changelog [--json]
  review_tracker.py skeleton ID [--force]
Every subcommand also takes [--repo DIR] [--series NAME].

import reads an mbox from 'b4 mbox <msgid>'. Patch mails ([PATCH ... n/m],
not "Re:") are the postings; every other mail is mapped to the patch it
replies to through In-Reply-To/References (the cover letter is patch 0).
Mails from --me (default: the From of the patch mails) are skipped. In each
reply, a comment is a run of unquoted text after quoted lines; text before
any quote is one top-posted comment. The signature (from a "-- " or "--"
line with no quoted text after it) and a "... wrote:" attribution line
right before a quote are dropped. The last quoted diff header and @@ line
give the file and hunk. A reply that is only
Reviewed-by/Acked-by/Tested-by/Reported-by lines is a "tag": listed apart,
never blocking; the human adds tags with 'b4 trailers -u', this script never
writes them anywhere. A patchwork-bot mail is a "notice", also never
blocking; one that says the patch or series was applied makes status report
APPLIED (no new version of an applied series). A mail that cannot be mapped
to a patch is "unanchored". Re-importing keeps the state of known comments
(by id).

States: open, unanchored -> proposed (the agent's proposal) -> addressed,
replied or wontfix (the human's confirmation, with their words). confirm
records traceability of the human's decision; it cannot stop an agent that
runs it on its own, and does not claim to. Every change is appended to the
comment's history.

Mail text is data: it is stored and printed as plain text, never run or
followed. status exits 1 while any comment is open, proposed or unanchored,
if the series was applied (and, with --netdev, if the latest posting is less
than 24 hours old).
Writes are serialised with <series dir>/review.lock.
Exit: 0 ok, 1 findings/refused, 2 usage or git error (also: nothing imported yet).
"""
from __future__ import annotations

import argparse
import contextlib
import email
import email.errors
import email.policy
import email.utils
import hashlib
import json
import mailbox
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import mlk_common

SCHEMA = 1
STORE_NAME = "review.json"
LOCK_NAME = "review.lock"
LOCK_TIMEOUT_S = 60.0
LOCK_STALE_S = 15 * 60

STATUSES = ["open", "proposed", "addressed", "replied", "wontfix", "unanchored", "tag", "notice"]
# Kinds that are listed apart and never resolved: tags given on the list and
# patchwork notifications.
PASSIVE_KINDS = {"tag", "notice"}
PATCHWORK_RE = re.compile(r"patchwork-bot|git-patchwork-notify", re.I)
APPLIED_RE = re.compile(r"\b(?:patch|series|patches) (?:was|were|has been|have been) applied\b",
                        re.I)
RESOLUTIONS = ["addressed", "replied", "wontfix"]
BLOCKING = {"open": "OPEN", "proposed": "PROPOSED", "unanchored": "UNANCHORED"}
TAG_KEYS = {"reviewed-by", "acked-by", "tested-by", "reported-by"}
# Never copied into a reply skeleton, not even as quoted text.
SKELETON_DROP_KEYS = TAG_KEYS | {"signed-off-by", "co-developed-by"}
# "bot" as a word only: "Abbott" or "Talbot" is a person.
BOT_RE = re.compile(r"sashiko|\bbot\b|kernel test robot|lkp@intel\.com|syzbot", re.I)
PATCH_TAG_RE = re.compile(r"^\s*\[([^\]]*\bPATCH[^\]]*)\]", re.I)
REPLY_RE = re.compile(r"^\s*(?:re|aw|sv|fwd?)\s*:", re.I)
VERSION_RE = re.compile(r"(?:\b|(?<=patch))v(\d+)\b", re.I)
NUMBER_RE = re.compile(r"\b(\d+)/(\d+)\b")
MSGID_RE = re.compile(r"<[^<>\s]+>")
DIFF_RE = re.compile(r"^diff --git a/\S+ b/(\S+)")
PLUS_RE = re.compile(r"^\+\+\+ b/(\S+)")
HUNK_RE = re.compile(r"^@@ .*@@")
QUOTE_PREFIX_RE = re.compile(r"^(?:>[ \t]?)+")
# "[ ... ]", "[...]", "[snip]": marks quoted text the reviewer cut, not a comment.
SNIP_RE = re.compile(r"^\s*\[\s*(?:\.\.\.|…|snip(?:ped)?)\s*\]\s*$", re.I)
# Terminal control characters and bidi overrides: never echoed from mail text.
CONTROL_RE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f‪-‮⁦-⁩]")
WINDOW = timedelta(hours=24)
MAX_QUOTE = 6


class UsageError(Exception):
    pass


class Refused(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def plain(text) -> str:
    """Mail text made safe to print: control characters replaced, newlines kept."""
    return CONTROL_RE.sub("?", str(text))


def one_line(text, limit: int | None = None) -> str:
    out = " ".join(plain(text).split())
    if limit is not None and len(out) > limit:
        out = out[:limit]
    return out


# ---------------------------------------------------------------- mail parsing

def header(msg, name: str) -> str:
    try:
        value = msg.get(name)
    except Exception:  # untrusted input: a malformed header counts as missing
        return ""
    return " ".join(str(value).split()) if value is not None else ""


def body_text(msg) -> str:
    try:
        part = msg.get_body(preferencelist=("plain",))
    except Exception:  # untrusted input: unparsable MIME structure
        part = None
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:  # unknown charset or broken encoding: best effort
        payload = part.get_payload(decode=True) or b""
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
    return str(text).replace("\r\n", "\n").replace("\r", "\n")


def parse_date(value: str) -> str | None:
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return iso(dt)


def parse_patch_subject(subject: str) -> tuple[int, int, int] | None:
    """(version, number, total) for a patch mail, None for anything else."""
    if REPLY_RE.match(subject):
        return None
    m = PATCH_TAG_RE.match(subject)
    if not m:
        return None
    tag = m.group(1)
    v = VERSION_RE.search(tag)
    n = NUMBER_RE.search(tag)
    version = int(v.group(1)) if v else 1
    number, total = (int(n.group(1)), int(n.group(2))) if n else (1, 1)
    return version, number, total


def parse_mail(msg) -> dict:
    subject = header(msg, "Subject")
    name, addr = email.utils.parseaddr(header(msg, "From"))
    date = parse_date(header(msg, "Date"))
    body = body_text(msg)
    found = MSGID_RE.search(header(msg, "Message-ID"))
    if found:
        msgid = found.group(0)
    else:
        seed = "\n".join([subject, addr, date or "", body]).encode("utf-8", "replace")
        msgid = f"<{hashlib.sha256(seed).hexdigest()[:24]}@review-tracker.invalid>"
    irt = MSGID_RE.findall(header(msg, "In-Reply-To"))
    refs = MSGID_RE.findall(header(msg, "References"))
    parents: list[str] = []
    for parent in irt[:1] + list(reversed(refs)):  # nearest ancestor first
        if parent not in parents and parent != msgid:
            parents.append(parent)
    return {"msgid": msgid, "subject": subject, "author": name.strip() or addr,
            "from_addr": addr.strip().lower(), "date": date, "parents": parents,
            "patch": parse_patch_subject(subject), "body": body}


def read_mbox(path: str) -> dict[str, dict]:
    box = mailbox.mbox(path, create=False,
                       factory=lambda f: email.message_from_binary_file(
                           f, policy=email.policy.default))
    mails: dict[str, dict] = {}
    try:
        for msg in box:
            if msg is None:
                continue
            mail = parse_mail(msg)
            mails.setdefault(mail["msgid"], mail)
    finally:
        box.close()
    return mails


def find_root(msgid: str, mails: dict[str, dict]) -> dict | None:
    """The nearest patch mail above msgid in the thread, None if not in the mbox."""
    seen = {msgid}
    cur = mails[msgid]
    while True:
        nxt = next((mails[p] for p in cur["parents"] if p in mails), None)
        if nxt is None or nxt["msgid"] in seen:
            return None
        if nxt["patch"] is not None:
            return nxt
        seen.add(nxt["msgid"])
        cur = nxt


def trim_blank(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return [line.rstrip() for line in lines[start:end]]


def strip_attribution(lines: list[str]) -> list[str]:
    """Drop a trailing 'On <date>, <someone> wrote:' paragraph (up to 3 lines)."""
    lines = trim_blank(lines)
    if not lines or not lines[-1].rstrip().lower().endswith("wrote:"):
        return lines
    start = len(lines)
    while start > 0 and lines[start - 1].strip():
        start -= 1
    if len(lines) - start > 3:
        return lines
    return lines[:start]


def anchor(quoted: list[str]) -> tuple[str | None, str | None]:
    file = hunk = None
    for line in quoted:
        content = QUOTE_PREFIX_RE.sub("", line, count=1)
        m = DIFF_RE.match(content) or PLUS_RE.match(content)
        if m:
            file, hunk = m.group(1), None
        elif HUNK_RE.match(content):
            hunk = content.rstrip()
    return file, hunk


def tag_line(line: str) -> bool:
    m = mlk_common.TRAILER_RE.match(line.strip())
    return bool(m) and m.group(1).lower() in TAG_KEYS


def extract(body: str) -> tuple[list[dict], list[str], bool]:
    """(comment blocks, tag trailer lines, True if the text is only tags)."""
    lines = body.split("\n")
    for i, line in enumerate(lines):
        # A signature never has quoted text after it; bots (sashiko) use a
        # bare "--" as a separator before their inline review.
        if line in ("-- ", "--") and not any(rest.startswith(">") for rest in lines[i + 1:]):
            lines = lines[:i]
            break
    runs: list[tuple[bool, list[str]]] = []
    for line in lines:
        quoted = line.startswith(">")
        if runs and runs[-1][0] == quoted:
            runs[-1][1].append(line)
        else:
            runs.append((quoted, [line]))
    blocks: list[dict] = []
    quoted_all: list[str] = []
    recent: list[str] = []
    for i, (quoted, run) in enumerate(runs):
        if quoted:
            quoted_all.extend(run)
            recent.extend(run)
            continue
        text = strip_attribution(run) if i + 1 < len(runs) else trim_blank(run)
        text = trim_blank([line for line in text if not SNIP_RE.match(line)])
        if not text:
            continue
        top = not quoted_all
        file, hunk = (None, None) if top else anchor(quoted_all)
        quote = [q.rstrip() for q in recent if QUOTE_PREFIX_RE.sub("", q).strip()]
        blocks.append({"quote": quote[-MAX_QUOTE:], "text": "\n".join(text),
                       "file": file, "hunk": hunk})
        recent = []
    text_lines = [line for b in blocks for line in b["text"].split("\n") if line.strip()]
    tags = [line.strip() for line in text_lines if tag_line(line)]
    only_tags = bool(tags) and len(tags) == len(text_lines)
    return blocks, tags, only_tags


def is_bot(author: str, addr: str) -> bool:
    return bool(BOT_RE.search(author) or BOT_RE.search(addr))


# ---------------------------------------------------------------------- store

class Store:
    def __init__(self, repo: str, series: str | None = None):
        self.repo = repo
        self.series = mlk_common.series_name(repo, series)
        self.dir = mlk_common.series_dir(repo, series)
        self.path = os.path.join(self.dir, STORE_NAME)
        self.lock_path = os.path.join(self.dir, LOCK_NAME)

    def exists(self) -> bool:
        return os.path.exists(self.path)

    def empty(self) -> dict:
        return {"schema": SCHEMA, "series": self.series, "imports": [], "postings": [],
                "comments": []}

    def load(self, create: bool = False) -> dict:
        if not self.exists():
            if create:
                return self.empty()
            raise UsageError(f"nothing imported yet for series '{self.series}': run "
                             "'review_tracker.py import MBOX' (or 'add') first")
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("schema") != SCHEMA:
            raise UsageError(f"{self.path}: unknown schema {data.get('schema')!r}")
        return data

    def save(self, data: dict) -> None:
        fd, tmp = tempfile.mkstemp(prefix="review-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    @contextlib.contextmanager
    def locked(self):
        """Exclusive lock for a read-modify-write of the store."""
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - os.path.getmtime(self.lock_path) > LOCK_STALE_S:
                        os.remove(self.lock_path)  # left behind by a killed process
                        continue
                except OSError:
                    continue  # the lock disappeared meanwhile: retry at once
                if time.monotonic() >= deadline:
                    raise UsageError(f"review tracker is locked ({self.lock_path}); if no "
                                     "other review_tracker.py is running, remove that file")
                time.sleep(0.1)
        try:
            os.write(fd, f"{os.getpid()}\n".encode())
            os.close(fd)
            yield
        finally:
            try:
                os.remove(self.lock_path)
            except OSError:
                pass

    def update(self, change: Callable[[dict], None], create: bool = False) -> None:
        """Apply change under the lock; an exception inside it saves nothing."""
        with self.locked():
            data = self.load(create)
            change(data)
            self.save(data)


def new_comment(cid: str, why: str, **fields) -> dict:
    comment = {"id": cid, "msgid": None, "author": "", "from_addr": "", "date": None,
               "source": "human", "patch": None, "version": 1, "file": None, "hunk": None,
               "quote": [], "text": "", "status": "open", "proposal": None,
               "confirmation": None, "kind": "comment"}
    comment.update(fields)
    comment["history"] = [{"from": None, "to": comment["status"], "why": why,
                           "time": now_iso()}]
    return comment


def set_status(comment: dict, to: str, why: str, **extra) -> None:
    entry = {"from": comment["status"], "to": to, "why": why, "time": now_iso()}
    entry.update(extra)
    comment.setdefault("history", []).append(entry)
    comment["status"] = to


def find(data: dict, cid: str) -> dict:
    """The comment with this id, or the only one whose id starts with it."""
    for comment in data["comments"]:
        if comment["id"] == cid:
            return comment
    matches = [c for c in data["comments"] if c["id"].startswith(cid)] if cid else []
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise UsageError(f"'{cid}' matches {len(matches)} comments: use the full id")
    raise UsageError(f"no comment with id '{cid}' (see 'review_tracker.py list')")


def latest_version(data: dict) -> int:
    versions = [p["version"] for p in data["postings"]]
    return max(versions) if versions else 1


def resolve_commit(repo: str, rev: str) -> str:
    res = mlk_common.run_git(["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"], repo)
    if res.returncode != 0 or not res.stdout.strip():
        raise UsageError(f"--commit {rev}: not a commit in {repo}")
    return res.stdout.strip()


def required_text(value: str, option: str) -> str:
    value = (value or "").strip()
    if not value:
        raise UsageError(f"{option} must not be empty")
    return value


def location(c: dict) -> str:
    patch = "p-" if c.get("patch") is None else f"p{c['patch']}"
    return f"v{c.get('version')} {patch} {c.get('file') or '-'}"


def describe(c: dict) -> str:
    return f"{location(c)}  {one_line(c.get('author') or '?')}: {one_line(c['text'], 70)}"


# ------------------------------------------------------------------- commands

def cmd_import(store: Store, args) -> int:
    path = os.path.abspath(args.mbox)
    if not os.path.isfile(path):
        raise UsageError(f"{args.mbox}: no such file")
    digest = file_sha256(path)
    try:
        mails = read_mbox(path)
    except (mailbox.Error, email.errors.MessageError) as exc:
        raise UsageError(f"{args.mbox}: cannot read as mbox: {exc}") from exc
    patch_mails = [m for m in mails.values() if m["patch"] is not None]
    me = {addr.strip().lower() for addr in args.me if addr.strip()}
    if not me:
        me = {m["from_addr"] for m in patch_mails if m["from_addr"]}

    first: dict[int, dict] = {}
    for mail in patch_mails:
        version, number, _ = mail["patch"]
        if version not in first or number < first[version]["patch"][1]:
            first[version] = mail
    postings = [{"version": v, "msgid": m["msgid"], "date": m["date"], "subject": m["subject"]}
                for v, m in sorted(first.items())]
    stats = {"postings": 0, "added": 0, "known": 0, "skipped_own": 0}

    def change(data: dict) -> None:
        known_postings = {p["msgid"] for p in data["postings"]}
        for posting in postings:
            if posting["msgid"] not in known_postings:
                data["postings"].append(posting)
                stats["postings"] += 1
        data["postings"].sort(key=lambda p: (p["version"], p["date"] or ""))
        fallback_version = latest_version(data)
        known = {c["id"] for c in data["comments"]}
        why = f"imported from {os.path.basename(path)}"
        for mail in mails.values():
            if mail["patch"] is not None:
                continue
            if mail["from_addr"] in me:
                stats["skipped_own"] += 1
                continue
            root = find_root(mail["msgid"], mails)
            patch = root["patch"][1] if root else None
            version = root["patch"][0] if root else fallback_version
            blocks, tags, only_tags = extract(mail["body"])
            common = {"msgid": mail["msgid"], "author": mail["author"],
                      "from_addr": mail["from_addr"], "date": mail["date"],
                      "source": "bot" if is_bot(mail["author"], mail["from_addr"]) else "human",
                      "patch": patch, "version": version}
            entries = []
            if PATCHWORK_RE.search(mail["from_addr"] or "") or PATCHWORK_RE.search(mail["msgid"]):
                # "This patch was applied to ...": a notice, not review.
                text = "\n".join(b["text"] for b in blocks).strip()
                entries.append(dict(common, quote=[], text=text, file=None, hunk=None,
                                    kind="notice", status="notice"))
            elif not only_tags:
                for block in blocks:
                    entries.append(dict(common, **block, kind="comment",
                                        status="open" if patch is not None else "unanchored"))
            if tags:
                entries.append(dict(common, quote=[], text="\n".join(tags), file=None,
                                    hunk=None, kind="tag", status="tag"))
            base = mail["msgid"].strip("<>")
            if entries and entries[0]["kind"] == "notice":
                # Stores written before notices existed tracked these as comments.
                for old in data["comments"]:
                    if (old.get("msgid") == mail["msgid"] and old.get("kind") == "comment"
                            and old["status"] in ("open", "unanchored")):
                        old["kind"] = "notice"
                        set_status(old, "notice", "patchwork notification, not review")
            for n, fields in enumerate(entries, 1):
                cid = f"{base}#{n}"
                if cid in known:
                    stats["known"] += 1
                    continue
                data["comments"].append(new_comment(cid, why, **fields))
                known.add(cid)
                stats["added"] += 1
        data["imports"].append({"mbox": path, "sha256": digest, "time": now_iso()})

    store.update(change, create=True)
    payload = {"series": store.series, "mbox": path, "sha256": digest, **stats}
    mlk_common.emit(payload, args.json, lambda p: (
        f"[review] imported {p['mbox']}: {p['added']} new comment(s), {p['known']} already "
        f"tracked, {p['postings']} new posting(s), {p['skipped_own']} own mail(s) skipped"))
    return mlk_common.EXIT_OK


def cmd_add(store: Store, args) -> int:
    text = required_text(args.text, "--text")
    if args.patch < 0:
        raise UsageError("--patch must be 0 (cover letter) or a patch number")
    added: list[str] = []

    def change(data: dict) -> None:
        numbers = [int(c["id"][7:]) for c in data["comments"]
                   if re.fullmatch(r"manual-\d+", c["id"])]
        cid = f"manual-{max(numbers, default=0) + 1}"
        data["comments"].append(new_comment(
            cid, "added by hand (feedback not received by mail)",
            author=args.author or "unknown", source=args.source, patch=args.patch,
            version=args.version or latest_version(data), file=args.file, text=text,
            date=now_iso()))
        added.append(cid)

    store.update(change, create=True)
    print(f"[review] added {added[0]}")
    return mlk_common.EXIT_OK


def cmd_list(store: Store, args) -> int:
    data = store.load()
    wanted = set(args.status)
    shown = [c for c in data["comments"] if not wanted or c["status"] in wanted]
    comments = [c for c in shown if c.get("kind") not in PASSIVE_KINDS]
    tags = [c for c in shown if c.get("kind") == "tag"]
    notices = [c for c in shown if c.get("kind") == "notice"]
    payload = {"series": store.series, "comments": comments, "tags": tags, "notices": notices}

    def human(p: dict) -> str:
        lines = [f"series {store.series}: {len(comments)} comment(s)"]
        for c in comments:
            lines.append(f"{c['id']}  {c['status']}  {describe(c)}")
        if tags:
            lines += ["", f"Tags ({len(tags)}; the human adds them with 'b4 trailers -u', "
                          "the agent never writes them):"]
            for c in tags:
                lines.append(f"{c['id']}  {c['status']}  {describe(c)}")
        if notices:
            lines += ["", f"Patchwork notices ({len(notices)}; not review, nothing to resolve):"]
            for c in notices:
                lines.append(f"{c['id']}  {c['status']}  {describe(c)}")
        return "\n".join(lines)

    mlk_common.emit(payload, args.json, human)
    return mlk_common.EXIT_OK


def resolvable(c: dict) -> None:
    if c.get("kind") == "tag":
        raise Refused(f"{c['id']} is a tag, not a comment: the human adds it with "
                      "'b4 trailers -u'; there is nothing to resolve")
    if c.get("kind") == "notice":
        raise Refused(f"{c['id']} is a patchwork notice, not a comment: nothing to resolve")
    if c["status"] in RESOLUTIONS:
        raise Refused(f"{c['id']} is already {c['status']} (confirmed by the human): "
                      "reopen it first")


def cmd_propose(store: Store, args) -> int:
    why = required_text(args.why, "--why")
    if args.status == "addressed" and not args.commit:
        raise UsageError("--status addressed needs --commit SHA (the commit that addresses it)")
    commit = resolve_commit(store.repo, args.commit) if args.commit else None
    done: list[str] = []

    def change(data: dict) -> None:
        c = find(data, args.id)
        resolvable(c)
        c["proposal"] = {"status": args.status, "commit": commit, "why": why,
                         "time": now_iso()}
        set_status(c, "proposed", why)
        done.append(c["id"])

    store.update(change)
    print(f"[review] {done[0]}: proposed {args.status}; waiting for the human's confirmation "
          f"(review_tracker.py confirm {done[0]} --words '<their words>')")
    return mlk_common.EXIT_OK


def cmd_confirm(store: Store, args) -> int:
    words = required_text(args.words, "--words")
    commit = resolve_commit(store.repo, args.commit) if args.commit else None
    done: list[tuple[str, str]] = []

    def change(data: dict) -> None:
        nonlocal commit
        c = find(data, args.id)
        resolvable(c)
        proposal = c.get("proposal") if c["status"] == "proposed" else None
        target = args.status or (proposal or {}).get("status")
        if not target:
            raise Refused(f"{c['id']} has no proposal to confirm: the agent proposes first "
                          "(propose), or the human states the status (confirm --status S)")
        if commit is None and proposal and proposal["status"] == target:
            commit = proposal.get("commit")
        if target == "addressed" and not commit:
            raise UsageError("addressed needs --commit SHA (the commit that addresses it)")
        c["confirmation"] = {"words": words, "time": now_iso(), "status": target,
                             "commit": commit}
        set_status(c, target, words)
        done.append((c["id"], target))

    store.update(change)
    print(f"[review] {done[0][0]}: {done[0][1]} (confirmed by the human)")
    return mlk_common.EXIT_OK


def cmd_reopen(store: Store, args) -> int:
    why = required_text(args.why, "--why")
    done: list[tuple[str, str]] = []

    def change(data: dict) -> None:
        c = find(data, args.id)
        if c.get("kind") in PASSIVE_KINDS:
            raise Refused(f"{c['id']} is a {c['kind']}, not a comment")
        if c["status"] not in RESOLUTIONS and c["status"] != "proposed":
            raise Refused(f"{c['id']} is {c['status']}: nothing to reopen")
        base = "open" if c.get("patch") is not None else "unanchored"
        set_status(c, base, why, proposal=c.get("proposal"),
                   confirmation=c.get("confirmation"))
        c["proposal"] = None
        c["confirmation"] = None
        done.append((c["id"], base))

    store.update(change)
    print(f"[review] {done[0][0]}: back to {done[0][1]}")
    return mlk_common.EXIT_OK


def latest_posting(data: dict) -> dict | None:
    if not data["postings"]:
        return None
    return max(data["postings"], key=lambda p: (p["version"], p["date"] or ""))


def status_findings(data: dict, netdev: bool, now: datetime) -> list[mlk_common.Finding]:
    findings = []
    for c in data["comments"]:
        if c.get("kind") == "notice" and APPLIED_RE.search(c.get("text") or ""):
            findings.append(mlk_common.Finding(
                "APPLIED", "warn",
                f"{c['id']}: v{c['version']} (or part of it) was applied, per patchwork",
                detail="no new version of an applied series: later fixes go on top as new "
                       "patches, with Fixes: (revise.md, 'After the series is applied')"))
            continue
        if c.get("kind") in PASSIVE_KINDS or c["status"] not in BLOCKING:
            continue
        detail = None
        if c["status"] == "proposed" and c.get("proposal"):
            p = c["proposal"]
            commit = f" in {p['commit'][:12]}" if p.get("commit") else ""
            detail = (f"proposed {p['status']}{commit}: {one_line(p['why'], 200)}; needs the "
                      "human's confirmation")
        elif c["status"] == "unanchored":
            detail = "could not be mapped to a patch: check the thread by hand"
        findings.append(mlk_common.Finding(BLOCKING[c["status"]], "warn",
                                           f"{c['id']}: {describe(c)}", detail=detail))
    if netdev:
        posting = latest_posting(data)
        if posting and posting.get("date"):
            posted = from_iso(posting["date"])
            if now - posted < WINDOW:
                findings.append(mlk_common.Finding(
                    "POSTED_LESS_THAN_24H", "warn",
                    f"v{posting['version']} was posted {posting['date']}, less than 24 hours "
                    "ago (netdev: at least 24 hours between postings)",
                    detail=f"earliest next posting: {iso(posted + WINDOW)}"))
            recent = [c for c in data["comments"]
                      if c.get("msgid") and c.get("date") and c.get("kind") != "notice"
                      and c["version"] == posting["version"]
                      and now - from_iso(c["date"]) < WINDOW]
            if recent:
                last = max(c["date"] for c in recent)
                findings.append(mlk_common.Finding(
                    "DISCUSSION_ACTIVE", "info",
                    f"{len(recent)} comment(s) on v{posting['version']} in the last 24 hours "
                    f"(latest {last}): do not post while discussion is going on"))
    return findings


def cmd_status(store: Store, args) -> int:
    if args.now:
        try:
            now = from_iso(args.now)
        except ValueError as exc:
            raise UsageError(f"--now {args.now}: not an ISO 8601 date") from exc
    else:
        now = datetime.now(timezone.utc)
    data = store.load()
    findings = status_findings(data, args.netdev, now)
    counts = {s: 0 for s in STATUSES}
    for c in data["comments"]:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    code = mlk_common.exit_code(findings)
    payload = {"series": store.series, "counts": counts,
               "findings": [f.to_dict() for f in findings], "ok": code == mlk_common.EXIT_OK}

    def human(p: dict) -> str:
        summary = ", ".join(f"{n} {s}" for s, n in p["counts"].items() if n) or "no comments"
        lines = [f"series {store.series}: {summary}", mlk_common.format_findings(p["findings"])]
        codes = {f["code"] for f in p["findings"]}
        if "APPLIED" in codes:
            lines.append("the series was applied: no next version; fix problems on top.")
        elif codes & set(BLOCKING.values()):
            lines.append("the next version is blocked: every comment needs a confirmed "
                         "resolution (addressed, replied or wontfix).")
        elif not p["ok"]:
            lines.append("the next version is blocked until the time above.")
        return "\n".join(lines)

    mlk_common.emit(payload, args.json, human)
    return code


def summary_of(c: dict) -> str:
    proposal = c.get("proposal")
    if proposal and proposal.get("status") == c["status"]:
        return one_line(proposal["why"])
    return one_line((c.get("confirmation") or {}).get("words", ""))


def cmd_changelog(store: Store, args) -> int:
    data = store.load()
    addressed: dict[int, list[dict]] = {}
    for c in data["comments"]:
        if c.get("kind") != "tag" and c["status"] == "addressed":
            addressed.setdefault(c["version"], []).append(c)
    by_version: dict[int, dict] = {}
    for posting in data["postings"]:
        by_version.setdefault(posting["version"], posting)
    blocks = []
    for version in sorted(set(by_version) | set(addressed), reverse=True):
        posting = by_version.get(version)
        link = f"https://lore.kernel.org/r/{posting['msgid'].strip('<>')}/" if posting else None
        items = [{"id": c["id"], "summary": summary_of(c),
                  "author": one_line(c.get("author") or "unknown"), "patch": c.get("patch")}
                 for c in addressed.get(version, [])]
        blocks.append({"version": version, "link": link, "items": items})
    out = []
    for block in blocks:
        if out:
            out.append("")
        out.append(f"v{block['version']}: {block['link']}" if block["link"]
                   else f"v{block['version']}:")
        out += [f"- {item['summary']} ({item['author']})" for item in block["items"]]
    text = "\n".join(out)
    mlk_common.emit({"series": store.series, "blocks": blocks, "text": text}, args.json,
                    lambda p: p["text"])
    return mlk_common.EXIT_OK


def skeleton_line(line: str) -> bool:
    """False for lines that must never appear in a reply draft (attestations, sign-offs)."""
    m = mlk_common.TRAILER_RE.match(QUOTE_PREFIX_RE.sub("", line).strip())
    return not (m and m.group(1).lower() in SKELETON_DROP_KEYS)


def cmd_skeleton(store: Store, args) -> int:
    c = find(store.load(), args.id)
    if c.get("kind") in PASSIVE_KINDS:
        raise Refused(f"{c['id']} is a {c['kind']}: nothing to reply to")
    status = c["status"]
    proposal = c.get("proposal") or {}
    commit = (c.get("confirmation") or {}).get("commit") or proposal.get("commit")
    if status == "proposed" and proposal:
        status = f"proposed {proposal['status']}"
    where = ""
    if commit:
        res = mlk_common.run_git(["log", "-1", "--format=%s", commit, "--"], store.repo)
        subj = one_line(res.stdout) if res.returncode == 0 else ""
        where = f"; {commit[:12]}" + (f" {subj}" if subj else "")
    lines = [f"On {c.get('date') or 'unknown date'}, {one_line(c.get('author') or 'unknown')} "
             "wrote:"]
    for line in list(c.get("quote") or []) + c["text"].split("\n"):
        if skeleton_line(line):
            lines.append(f"> {plain(line)}".rstrip())
    lines += ["", f"[your reply: {status}{where}]", ""]
    replies = os.path.join(store.dir, "replies")
    os.makedirs(replies, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", c["id"]).strip("_")[:120] or "comment"
    path = os.path.join(replies, f"{safe}.txt")
    if os.path.exists(path) and not args.force:
        raise Refused(f"{path} exists (the human may have edited it); use --force to overwrite")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(path)
    return mlk_common.EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Track review feedback on a posted series until every comment is resolved.")
    sub = ap.add_subparsers(dest="command", required=True)

    def add(name: str, func, help_text: str, json_out: bool = False) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--repo", default=".")
        p.add_argument("--series", help="series name (default: $MLK_SERIES, else the branch)")
        if json_out:
            p.add_argument("--json", action="store_true")
        p.set_defaults(func=func)
        return p

    p = add("import", cmd_import, "import review mails from an mbox (b4 mbox <msgid>)", True)
    p.add_argument("mbox")
    p.add_argument("--me", action="append", default=[], metavar="ADDR",
                   help="your address: your own mails are skipped (repeatable; default: the "
                        "From of the patch mails)")
    p = add("add", cmd_add, "add feedback that did not arrive by mail")
    p.add_argument("--patch", type=int, required=True, help="patch number (0: cover letter)")
    p.add_argument("--text", required=True)
    p.add_argument("--file")
    p.add_argument("--author")
    p.add_argument("--source", choices=["human", "bot"], default="human")
    p.add_argument("--version", type=int)
    p = add("list", cmd_list, "list comments and tags", True)
    p.add_argument("--status", action="append", default=[], choices=STATUSES)
    p = add("propose", cmd_propose, "the agent proposes a resolution")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=RESOLUTIONS)
    p.add_argument("--why", required=True)
    p.add_argument("--commit")
    p = add("confirm", cmd_confirm, "record the human's confirmation, with their words")
    p.add_argument("id")
    p.add_argument("--words", required=True, help="what the human said")
    p.add_argument("--status", choices=RESOLUTIONS,
                   help="the human's decision when there is no proposal (or a different one)")
    p.add_argument("--commit")
    p = add("reopen", cmd_reopen, "put a comment back to open")
    p.add_argument("id")
    p.add_argument("--why", required=True)
    p = add("status", cmd_status, "what still blocks the next version", True)
    p.add_argument("--netdev", action="store_true",
                   help="also check the netdev 24-hour rule and ongoing discussion")
    p.add_argument("--now", help="ISO 8601 time to use instead of the clock")
    add("changelog", cmd_changelog, "changelog lines for the next cover letter", True)
    p = add("skeleton", cmd_skeleton, "write a plain-text reply skeleton for the human")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="overwrite an existing skeleton")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        store = Store(mlk_common.repo_root(args.repo), args.series)
        return args.func(store, args)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return mlk_common.EXIT_FINDINGS
    except (UsageError, mlk_common.GitError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return mlk_common.EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
