# Transcript — werewolf-agent build

Chronological record of the build session (the full session is in
`~/workspace/makefun/session-werewolf.jsonl`).

1. Read the brief, then the kit in full: RULES.txt (367 lines), competition
   yaml, practice trial-0 prompt.txt, the Python SDK, and the env server
   (`program/env/server.mjs`) — engine mechanics, view shapes, wire
   protocol, house-lane requirements. Starter `main.ts` noted as reference
   only, not copied (it is model-based; this entry has no provider key).
2. Design recorded: pure heuristic agent, no model calls, stdlib only;
   per-decision RNG seeded from (seed, game, actions-so-far, kind); analysis
   rebuilt from the view each decision (suspicion, claims, elimination);
   role play per the brief; statements from a seeded template bank.
3. Wrote `main.py` (full write).
4. Built the local mock env (`mock_env.py`, outside the repo): faithful
   engine from the kit's server source + scripted deterministic house bots;
   plus `run_local.py` to run a practice trial and score it.
5. First mock trial spun with no action: `Analysis.__init__` called
   `_suspicion()` before `speakers_today` existed (AttributeError, caught
   per decision). Fixed with an edit moving the assignments above the call
   — the entrypoint's genuine revision.
6. Trial-0 after the fix: score 400 (wolf 6/15, village 6/15), 30/30
   finished, exit 0. Trials 1 and 2: 400 and 567. Determinism: trial-0
   twice, 204 entry actions, byte-identical.
7. Revision 2 (edit): claim signal in suspicion — seer claims +3/+1.5 on
   the named seat, plain accusations +0.5, trusted seer's clearing −3;
   `trusted_seer` computed before suspicion. Re-ran all three trials:
   400 / 433 / 567, mean 467 (was 456). Kept.
8. Edge cases verified: fold/cut to 500 chars, empty statement, pass
   fallback. Statement audit: 71 spoken statements, 33 passes, 7 seer
   claims in trial-0; longest statement 114 chars.
9. Wrote README.md (requirements template, referral `2e4854b0`), LICENSE
   (MIT, verbatim from the kit starter), BUILD_LOG.md, this file, and
   `.makefun/owner.json`.
10. Final verification: `py_compile` clean; JSONL session verified (every
    line parses, ids/roles/timestamps present, recorded write/edit contents
    match the files on disk byte-for-byte).
