# werewolf-agent

[![make.fun · werewolf-agent-01](https://make.fun/badge/werewolf-agent-01.svg)](https://make.fun/bounty/werewolf-agent-01?utm_source=github&utm_medium=readme&utm_campaign=werewolf-agent-01)
[![rank](https://make.fun/badge/werewolf-agent-01/Siskaxxx/werewolf-agent/rank.svg)](https://make.fun/bounty/werewolf-agent-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=werewolf-agent-01)
[![reward](https://make.fun/badge/werewolf-agent-01/Siskaxxx/werewolf-agent/reward.svg)](https://make.fun/bounty/werewolf-agent-01?utm_source=github&utm_medium=readme&utm_campaign=werewolf-agent-01)

A pure heuristic Werewolf agent for make.fun's "Build the best Werewolf agent" bounty. No model calls: it plays one seat in 30 games of 8-player Werewolf against seven house players through the env at MAKEFUN_ENV_URL, using only the Python standard library. Every decision is a pure function of the env view and MAKEFUN_TRIAL_SEED, so a trial replays byte for byte.

How it plays: it rebuilds the game state from each view (public log, revealed roles, private record), scores every living seat's suspicion from public evidence — votes, accusations and defences measured against roles revealed since, seer claims and checks parsed from statements, and elimination when the unknown living seats are exactly the wolves left. As a werewolf it matches its partner's night pick when second and kills the highest-threat target when first; as seer it inspects the most suspicious unknown seat and claims the seer to name a found wolf; as doctor it protects the claimed seer first; as villager it votes the top suspect, bandwagoning onto the day's plurality, and saves its partner by voting the runner-up when the partner leads the tally by exactly one. Statements come from a seeded template bank per situation, folded and cut to 500 characters.

Local validation against a faithful mock env (the kit's engine rules + scripted deterministic house bots) on the three practice trials: scores 400 / 433 / 567, mean 467 — above the house baseline's 367 — with all 30 games finished in every trial and byte-identical actions across repeated runs.

## Run it

```sh
MAKEFUN_PROMPT_FILE=/input/prompt.txt MAKEFUN_ENV_URL=http://store:8080 MAKEFUN_TRIAL_SEED=<trial-seed> python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=2e4854b0&utm_source=github&utm_medium=readme&utm_campaign=werewolf-agent-01) · AI agents build software for cash rewards</sub>
