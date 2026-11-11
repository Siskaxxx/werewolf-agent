# Build log — werewolf-agent (make.fun werewolf-agent-01)

## Design

Pure heuristic agent, `main.py`, Python stdlib only, no model calls (no
provider API key exists for this entry, and the gateway answers model calls
401 without one). One loop: POST /observe, answer each pending decision in
the order listed, exit when `done` is true.

Determinism: no mutable state across decisions. Every decision is a pure
function of the served view and MAKEFUN_TRIAL_SEED: the per-decision RNG is
seeded from (seed, game, actions-taken-so-far, decision kind), all readable
from the view. No wall clock, no unseeded randomness, every candidate list
sorted before choosing. One request at a time, in /observe order.

Analysis rebuilt from the view on every decision:
- public-evidence suspicion: votes for revealed villagers +2.5, votes for
  revealed wolves −3; accusations/defences vs revealed roles ±2–2.5; seer
  claims confirmed by a reveal earn trust (−2), contradicted claims +3;
  quiet seats +0.3/day from day 2.
- Claim signal (added in revision 2): a seer claim naming a wolf adds +3
  (trusted seer) or +1.5 (unverified claimant) to the named seat; plain
  accusations +0.5; a trusted seer's clearing −3. Contradicted claimants
  are ignored.
- Seer knowledge: inspections give certain wolves/villagers; elimination —
  when the unknown living seats equal the remaining wolves, all are wolves.
- Kill threat (wolf): claimed seer +10, trusted seer +5, accusers of me or
  my partner +5, low suspicion (trusted town) weighted in.

Role play:
- Werewolf: match the partner's night pick when voting second; when first,
  kill the highest-threat target. Accuse the most suspicious-looking
  villager with a reason from the log; defend the partner only lightly;
  vote with the plurality; save the partner by voting the runner-up when
  they lead the tally by exactly one. Fake a seer claim only once the real
  seer is dead and revealed (day 2+, 50%).
- Seer: inspect the most suspicious unknown seat; claim the seer and name a
  found wolf immediately; share clears otherwise.
- Doctor: protect the claimed seer, then wolf-accusers, then trusted town;
  never self or last night's target (the env's options enforce this too).
- Villager: vote the top suspect with plurality bandwagoning (suspicion +
  1.5 per vote so far today); accuse with a reason from the log.
- Statements: seeded template bank per situation; pass 35% on day 1,
  12% later; never repeats the previous statement; folded and cut to 500
  chars before sending (the env refuses longer ones). Refusals retry once
  with the safest valid action (pass / first option / abstain).

## Bugs found in validation

1. First mock trial spun forever with no action: `Analysis.__init__`
   called `_suspicion()` before `self.speakers_today` was set
   (AttributeError, caught by the per-decision guard, so the trial kept
   looping). Fixed by moving `my_speeches`/`speakers_today` (and then
   `trusted_seer`) above the `_suspicion()` call. This is the genuine
   revision the entrypoint carries.
2. An edge-case test of mine was wrong, not the code: `fold` drops control
   characters without inserting a space, exactly like the env's
   `foldStatement` — `'a\n\n\tb\x01c '` folds to `'a bc'`.

## Local validation

Mock env (`mock_env.py`, outside the repo): the kit's engine rules
implemented from `program/env/server.mjs` (night wolf order, kill
agreement, protect/inspect constraints, 2-cycle debate, vote plurality,
win checks, day-10 draw), same /observe /game /act shapes, seven house
seats played by scripted deterministic heuristic bots (seeded per game
and seat). The real env could not be run locally: its house lane needs an
Anthropic key, which this entry does not have; `grade-local --driver
docker` is unavailable (no Docker on this VM).

Practice trials, seed `local-test-seed-1` (mock scores — house bots are
simpler than the real claude-haiku-5-5 table, and they ignore unverified
claims, so they understate what statements can do against the real table):

| version | trial-0 | trial-1 | trial-2 | mean |
|---------|---------|---------|---------|------|
| v1 (initial) | 400 (6W/6V) | 400 (6W/6V) | 567 (8W/9V) | 456 |
| v2 (+claim signal) | 400 (6W/6V) | 433 (6W/7V) | 567 (8W/9V) | 467 |

House baseline (starter, opus-5-5 plain): 367. All three v2 trials score
above it. All 30 games finished in every trial; vote accuracy 478–692‰.
Per-role (v2): werewolf 6–8/15, seer 2/5 every trial, doctor 2–3/5,
villager 1–5/5 (high variance). Seer stays at 2/5 because the mock house
bots ignore unverified seer claims — against the real haiku table a
claimed wolf should draw more votes; the mock number is a floor, not a
ceiling, for that role.

Determinism: trial-0 run twice with the same seed — 204 entry actions,
byte-identical. `py_compile` clean (Python 3.12.3 here; no 3.13-only
features). Edge cases verified: fold/cut (500-char cap), empty statement,
pass fallback.

## Limits (honest)

- The mock house bots are heuristic scripts, not claude-haiku-5-5: the
  mock score estimates, not measures, the real-trial score. Statement
  persuasion — a real strength of this design — is mostly unmeasured.
- No full local trial against the real env was possible (house lane needs
  a provider key; no Docker for grade-local).
- Seer play depends on the table believing a seer claim; if the house
  players ignore claims, seer games rely on votes alone.
