<!-- SPDX-License-Identifier: GPL-2.0 -->

# Documentation patches

Fixing or extending `Documentation/` and kernel-doc comments is real
work that maintainers welcome when it is accurate. The risk is the
opposite of code: a documentation patch always "builds", so it is easy to
send text that is wrong. Every statement must be checked against the
code of the tree.

In-tree rules: `Documentation/doc-guide/` (Sphinx, kernel-doc), and the
subsystem's own documentation conventions (checked against
docs.kernel.org 7.3, 2026-10).

## 1. Check the claims, not only the build

For each sentence the patch adds or changes, find the code it describes
and confirm it: the function signature, the default value, the sysctl
range, the locking rule. Quote the file and line in the why-log
(`references/develop.md`). If the code and the existing text disagree,
the code wins, but ask whether the text describes intended behaviour and
the code has a bug.

Do not send typo-only or wording-only patches across many files: they
are churn for maintainers, like standalone cleanups. One clear mistake, or
missing documentation for something real, is worth a patch.

## 2. Build the part you touched

```
./tools/docs/sphinx-pre-install                 # what is missing to build docs (path in your tree)
make SPHINXDIRS=networking htmldocs 2>&1 | tee "$SERIES/docs-patched.log"
```

`SPHINXDIRS` limits the build to a directory of `Documentation/`; output
goes to `Documentation/output/` (or the `O=` directory). Compare with the
same build on the base, in a separate worktree, like a code build:

```
git worktree add --detach "$HOME/src/mlk-wt/docs-base" "$BASE"
make -C "$HOME/src/mlk-wt/docs-base" SPHINXDIRS=networking htmldocs 2>&1 \
  | tee "$STATE/docs-baseline-$(git rev-parse --short=12 "$BASE").log"
python3 <skill-dir>/scripts/build_warnings.py diff \
  --baseline "$STATE/docs-baseline-<base12>.log" --patched "$SERIES/docs-patched.log"
```

The paths differ between the two checkouts; `build_warnings.py` cuts them
back to `Documentation/...`, so the same warning matches.

`build_warnings.py` understands Sphinx `WARNING:`/`ERROR:` lines and
kernel-doc warnings; only new ones count. Pre-existing warnings are not
yours to fix in the same patch. Missing Sphinx dependencies produce
errors that have nothing to do with the patch: fix the setup first.

Then open the rendered HTML page and read it: tables, lists and code
blocks that "build" can still render wrongly.

## 3. kernel-doc comments

For comments in `.c` and `.h` files:

```
./tools/docs/kernel-doc -v -none drivers/net/foo/foo_main.c   # path of the tool: check your tree
```

`make W=1` also checks the kernel-doc of the `.c` files it builds, but
"does not verify header files": run kernel-doc on headers yourself. A
kernel-doc comment that no `.rst` file includes is not in the HTML build at
all; check it directly.

## 4. Commit message and target

- Subject prefix of the subsystem (`docs: networking: ...`, or the
  subsystem's own prefix for its files), imperative, as for code.
- Say what was wrong or missing and how you verified the new text
  ("checked against foo_set_mtu() in net/core/dev.c").
- Target: documentation under a subsystem goes to that subsystem's tree
  and list (`get_maintainer.pl` on the file); general documentation to the
  documentation maintainer. For netdev docs, the usual net / net-next
  choice applies.

Record the evidence as for code: `evidence.py run --name docs-networking
--category build -- make SPHINXDIRS=networking htmldocs`, and the diff
against the baseline as `new-warnings-docs`.

## Done when

- [ ] every changed statement checked against the code
- [ ] the touched directory builds with no new warnings; output read
- [ ] kernel-doc checked for the touched headers and `.c` files
- [ ] subject, changelog and recipients right
