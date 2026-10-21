<!-- SPDX-License-Identifier: GPL-2.0 -->

# Security tripwire

v1 of this skill does not prepare security reports. Its job is to notice a
possible vulnerability early, stop the steps that could leak it, explain
the rules that apply (read from the tree), and let the human decide. Do
not assume the private channel by default: the current rules send many
cases, including most AI-found bugs, through public channels.

## Signals

Stop and screen when any of these appear in the bug, the reproducer or
the patch:

- memory corruption (out-of-bounds write, use-after-free, double free)
  reachable by an unprivileged user or from the network;
- information leak to userspace or the network (uninitialised memory,
  kernel pointers);
- permission or capability bypass, namespace escape, missing access check;
- a crash or hang triggerable remotely or by an unprivileged user;
- the human or a report calls it a vulnerability, exploit or CVE.

## Immediately

1. Stop ordinary preparation: no external review (it sends the code to an
   LLM provider), no posting, no drafting of public text.
2. Keep the reproducer local (`$STATE/`), out of commit messages, cover
   letters, bug trackers and chats with third parties.
3. Tell the human what you saw, in one or two sentences, and that the
   rules below decide what happens next.

## Read and explain the applicable rules

Read these from the target tree, whole, and explain the parts that apply
to this case:

- `Documentation/process/threat-model.rst`: what counts as a security bug.
- `Documentation/process/security-bugs.rst`: what qualifies, what a report
  must contain, AI-specific rules, contacts, embargo, coordination.
- `Documentation/process/coding-assistants.rst`, bug-fixing procedure: the
  assistant determines bug vs vulnerability from `threat-model.rst` and
  leaves the result to the human; it never sends anything.
- netdev: "Security fixes" in `Documentation/process/maintainer-netdev.rst`.

Fallback summary of mainline as of 2026-10 (verify in the tree before
relying on any of it):

- **Most bugs are handled in public.** The security list is for urgent bugs
  that give an attacker a capability they should not have on a correctly
  configured production system, are easy to exploit, and threaten many
  users. Many reports to it are ordinary bugs; those go through the normal
  channels.
- **AI-assisted discovery: treat the bug as public.** Such bugs tend to be
  found by several people at once. Do not share the reproducer publicly;
  say that one is available and that maintainers can ask for it privately.
- **Unsure whether it qualifies:** the text says to err on the side of
  reporting privately rather than missing a real vulnerability.
- **Contacts:** a report goes to the subsystem maintainers (found with
  `get_maintainer.pl`, see the options in `security-bugs.rst`), with
  security@kernel.org in Cc in the cases the document lists (always if
  there are two or fewer recipients). Not to a public list at that stage,
  unless the issue is public or trivially discoverable (which includes use
  of AI-based tools).
- **Report content:** affected version as a commit ID or exact release,
  description with traces, a tested reproducer (shared on request when the
  report is public), conditions; ideally a tested fix with a `Fixes:` tag,
  in plain text, no Markdown.
- **Embargo:** fixes for publicly known bugs are released immediately; for
  undisclosed ones a delay of up to 7 days from the start of the release
  process (exceptionally 14) can be requested. Do not contact
  linux-distros before a fix is accepted. The security team does not
  assign CVEs.
- **netdev:** do not email netdev maintainers directly about possible
  security bugs; use the lists, or security@kernel.org / the distros
  process as alternatives.

## Outcomes

Write down the human's decision and the reason, then:

- **Not a security bug** under `threat-model.rst` (e.g. it only needs
  privileges the threat model already trusts, such as root in the initial
  namespace; check the user-namespace notes there for `CAP_NET_ADMIN`):
  resume the normal flow. If the bug was found with AI tools, the
  reproducer still stays out of public text (`security-bugs.rst`, "Responsible
  use of AI to find bugs"); no confirmation changes that. Otherwise keep it
  private until the classification is settled and the in-tree rules allow
  publishing it.
- **Security bug, to be handled publicly** (e.g. AI-found, per the current
  text): v1 stops at the explanation. Point the human to the procedure in
  `security-bugs.rst` ("Responsible use of AI to find bugs"); local
  technical work (building, testing the fix in a VM) may continue if the
  human asks, but no external review and no public reproducer.
- **Security bug, private report:** v1 stops. Point to "Identifying
  contacts" and "Sending the report" in `security-bugs.rst`. The human
  writes and sends the report; do not draft or send it.

## Record

Every series gets a screen, at least when the work is chosen and again
before submission:

```
python3 <skill-dir>/scripts/evidence.py note --key security_screen \
  --value "no signals: wrong return value in ethtool get path, no memory safety impact"
```
or
```
python3 <skill-dir>/scripts/evidence.py note --key security_screen \
  --value "tripwire: UAF reachable from unprivileged socket; ordinary prep stopped; human decision: <...>"
```
