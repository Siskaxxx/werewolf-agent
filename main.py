#!/usr/bin/env python3
"""Werewolf agent for the make.fun bounty werewolf-agent-01.

A pure heuristic player: no model calls, no network beyond the env, standard
library only. It plays one seat in 30 games of 8-player Werewolf against the
house players, through POST /observe, /game and /act at MAKEFUN_ENV_URL.

Determinism: every decision is a pure function of the env view it was served
and MAKEFUN_TRIAL_SEED. There is no mutable state across decisions: the
per-decision randomness comes from a Random seeded with
(seed, game, actions-taken-so-far, decision kind), all of which are readable
from the view. No wall clock, no unseeded randomness, no dict/set iteration
order (every choice sorts its candidates). Requests go out one at a time, in
the order /observe lists them.

Strategy, in short: rebuild the game state from the view on every decision
(public log, revealed roles, private record), score every living seat's
suspicion from public evidence (votes, accusations and defences measured
against roles revealed since), parse seer claims and checks from statements,
and use elimination (unknown living seats == remaining wolves). Then:
  werewolf: match the partner's night pick when second to vote; when first,
    kill the highest-threat target (claimed seer, accusers of wolves, trusted
    town). In debate accuse the most suspicious-looking villager, defend the
    partner only lightly, vote with the plurality, and save the partner by
    voting the runner-up when they lead the tally by exactly one. Fake a
    seer claim only once the real seer is dead and revealed.
  seer: inspect the most suspicious unknown seat; claim the seer and name a
    found wolf at once, so the village can vote them out.
  doctor: protect the claimed seer first, then players who accused a wolf,
    then trusted town; never self, never the same seat two nights in a row.
  villager: vote the top suspect, bandwagoning onto the day's plurality so
    an exile actually happens, and accuse with a reason drawn from the log.
Statements come from a small seeded template bank per situation, folded to
one line and cut to 500 characters before sending (the env refuses longer
ones instead of cutting them). Every pending decision gets a valid answer;
a refusal is retried once with the safest valid action.
"""

import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request

MAX_CHARS = 500
SEATS = (1, 2, 3, 4, 5, 6, 7, 8)


# ---------------------------------------------------------------- env client

class EnvError(Exception):
    pass


class Env:
    """The contest env: POST /observe, /game, /act, one request at a time."""

    def __init__(self, base_url):
        self.base = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(self, path, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Accept": "application/json"})
        last = None
        for _ in range(5):
            try:
                with self.opener.open(req, timeout=600) as res:
                    return json.loads(res.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                try:
                    return json.loads(err.read().decode("utf-8"))
                except Exception:
                    return {"_http": err.code}
            except (urllib.error.URLError, OSError) as err:
                last = err
                time.sleep(1)
        raise EnvError("env unreachable after retries: %s" % (last,))

    def observe(self):
        return self.post("/observe", {})

    def act(self, game, decision, **kw):
        payload = {"game": game, "decision": decision}
        payload.update(kw)
        return self.post("/act", payload)


# ---------------------------------------------------------------- seeded rng

def rng_for(seed, game, taken, kind):
    """A Random for one decision, seeded from public facts only."""
    key = "%s|game%d|taken%d|%s" % (seed, game, taken, kind)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# ---------------------------------------------------------------- text tools

def fold(text):
    """The env's folding: runs of line breaks/tabs become one space, other
    control characters are dropped, ends trimmed."""
    text = re.sub(r"[\t\n\v\f\r\u0085\u2028\u2029]+", " ", text)
    text = "".join(ch for ch in text if not (ord(ch) < 32 or ord(ch) == 127))
    return text.strip()


def cut(text):
    """Fold and cut to MAX_CHARS code points (the env refuses, never cuts)."""
    text = fold(text)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip()
    return text


def name_of(view, seat):
    for p in view["players"]:
        if p["seat"] == seat:
            return p["name"]
    return "seat %d" % seat


# ---------------------------------------------------------------- claim parsing

_SEER_CLAIM = re.compile(
    r"\b(i am|i'm|im|i'm the|i am the)\b[^.]{0,30}\bseer\b", re.I)
_WOLF_ACCUSE = re.compile(
    r"(?:seat\s+)?(\d+)\s+(?:is|looks?|seems?)\s+(?:a\s+|the\s+)?wolf\b", re.I)
_WOLF_ACCUSE2 = re.compile(
    r"\bwolf\b[^.]{0,25}?\bseat\s+(\d+)\b", re.I)
_CLEAR = re.compile(
    r"(?:seat\s+)?(\d+)\s+(?:is\s+)?(?:not\s+a\s+wolf|innocent|clear|town|a\s+villager)\b",
    re.I)
_CHECK = re.compile(
    r"check\w*\s+(?:seat\s+)?(\d+)", re.I)


def parse_claims(view):
    """From the public log: who claimed seer, who accused whom of being a
    wolf, who cleared whom. Returns (seer_claims, accusations, clearings):
    seer_claims: seat -> set of checked seats it named as wolves (may be empty)
    accusations: list of (seat, target) / clearings: list of (seat, target)."""
    names = {p["name"].lower(): p["seat"] for p in view["players"]}
    seer_claims = {}
    accusations = []
    clearings = []
    for e in view["log"]:
        if e.get("kind") != "speech":
            continue
        seat = e["seat"]
        text = e["text"]
        low = text.lower()
        if _SEER_CLAIM.search(low):
            seer_claims.setdefault(seat, set())
        # seat numbers named as wolves
        for m in _WOLF_ACCUSE.finditer(low):
            t = int(m.group(1))
            if 1 <= t <= 8 and t != seat:
                accusations.append((seat, t))
                if seat in seer_claims:
                    seer_claims[seat].add(t)
        for m in _WOLF_ACCUSE2.finditer(low):
            t = int(m.group(1))
            if 1 <= t <= 8 and t != seat:
                accusations.append((seat, t))
        for m in _CHECK.finditer(low):
            t = int(m.group(1))
            if 1 <= t <= 8 and t != seat and seat in seer_claims:
                seer_claims[seat].add(t)
        for m in _CLEAR.finditer(low):
            t = int(m.group(1))
            if 1 <= t <= 8 and t != seat:
                clearings.append((seat, t))
        # names mentioned as wolves: "Niko is a wolf"
        for name, t in names.items():
            if t == seat:
                continue
            if re.search(r"\b%s\b[^.]{0,20}?\bwolf\b" % re.escape(name), low):
                accusations.append((seat, t))
            if re.search(r"\b%s\b[^.]{0,20}?\b(?:innocent|clear|not a wolf)\b"
                         % re.escape(name), low):
                clearings.append((seat, t))
    return seer_claims, accusations, clearings


# ---------------------------------------------------------------- game analysis

class Analysis:
    """Everything derivable from one view, computed fresh per decision."""

    def __init__(self, view):
        self.view = view
        self.you = view["you"]
        self.me = self.you["seat"]
        self.role = self.you["role"]
        self.day = view["day"]
        self.phase = view["phase"]
        self.partner = (self.you.get("partner") or {}).get("seat")
        self.alive = [p["seat"] for p in view["players"] if p["alive"]]
        self.revealed = {p["seat"]: p["role"] for p in view["players"]
                         if not p["alive"] and p.get("role")}
        self.dead_wolves = [s for s, r in self.revealed.items()
                            if r == "werewolf"]
        self.dead_seer = any(r == "seer" for r in self.revealed.values())
        self.known_wolf = set()
        self.known_village = set()
        if self.role == "werewolf":
            self.known_wolf = {self.me}
            if self.partner:
                self.known_wolf.add(self.partner)
        if view.get("seerResults"):
            for r in view["seerResults"]:
                if r["werewolf"]:
                    self.known_wolf.add(r["seat"])
                else:
                    self.known_village.add(r["seat"])
        for s, r in self.revealed.items():
            if r == "werewolf":
                self.known_wolf.add(s)
            else:
                self.known_village.add(s)
        # Elimination: the unknown living seats are exactly the wolves left.
        remaining_wolves = 2 - len(self.dead_wolves) - len(
            [s for s in self.known_wolf if s in self.alive])
        unknown = [s for s in self.alive
                   if s not in self.known_wolf and s not in self.known_village]
        if remaining_wolves > 0 and len(unknown) == remaining_wolves:
            self.known_wolf.update(unknown)
        self.seer_claims, self.accusations, self.clearings = parse_claims(view)
        self.my_speeches = [e["text"] for e in view["log"]
                            if e.get("kind") == "speech"
                            and e.get("seat") == self.me]
        self.speakers_today = {e["seat"] for e in view["log"]
                               if e.get("kind") == "speech"
                               and e.get("day") == self.day}
        self.trusted_seer = self._trusted_seer()
        self.suspicion = self._suspicion()
        self.votes_today, self.tally_today = self._votes_today()
        self.accused_me = {a for (a, t) in self.accusations
                           if t == self.me}
        self.accused_partner = {a for (a, t) in self.accusations
                                if t == self.partner} if self.partner else set()

    def _suspicion(self):
        sus = {s: 0.0 for s in self.alive}
        for e in self.view["log"]:
            kind = e.get("kind")
            if kind == "vote":
                voter, target = e["seat"], e.get("target")
                if target is None or target not in self.revealed:
                    continue
                if target in sus:
                    pass
                if self.revealed[target] == "werewolf":
                    sus[voter] = sus.get(voter, 0.0) - 3.0
                else:
                    sus[voter] = sus.get(voter, 0.0) + 2.5
            elif kind == "speech":
                seat = e["seat"]
                low = e["text"].lower()
                for (a, t) in self.accusations:
                    if a != seat:
                        continue
                    if t in self.revealed:
                        if self.revealed[t] == "werewolf":
                            sus[seat] = sus.get(seat, 0.0) - 2.5
                        else:
                            sus[seat] = sus.get(seat, 0.0) + 2.0
                for (a, t) in self.clearings:
                    if a != seat:
                        continue
                    if t in self.revealed:
                        if self.revealed[t] == "werewolf":
                            sus[seat] = sus.get(seat, 0.0) + 2.5
                        else:
                            sus[seat] = sus.get(seat, 0.0) - 1.0
                _ = low
        # A seer claim later contradicted by a reveal is damning; one
        # confirmed by a reveal earns trust.
        for claimant, checks in self.seer_claims.items():
            for t in checks:
                if t in self.revealed:
                    if self.revealed[t] == "werewolf":
                        sus[claimant] = sus.get(claimant, 0.0) - 2.0
                    else:
                        sus[claimant] = sus.get(claimant, 0.0) + 3.0
        # Claims carry signal even before any reveal: a seer claim naming
        # a wolf, and plain accusations. A claimant already contradicted
        # by a reveal is ignored.
        for claimant, checks in self.seer_claims.items():
            contradicted = any(
                t in self.revealed and self.revealed[t] != "werewolf"
                for t in checks)
            if contradicted:
                continue
            weight = 3.0 if claimant == self.trusted_seer else 1.5
            for t in checks:
                if t in sus:
                    sus[t] = sus.get(t, 0.0) + weight
        for (a, t) in self.accusations:
            if t in sus and t not in self.revealed and a != self.me:
                sus[t] = sus.get(t, 0.0) + 0.5
        for (a, t) in self.clearings:
            if a == self.trusted_seer and t in sus:
                sus[t] = sus.get(t, 0.0) - 3.0
        # Quiet seats hide more easily than loud ones.
        for s in self.alive:
            if s not in self.speakers_today and self.day >= 2:
                sus[s] = sus.get(s, 0.0) + 0.3
        for s in list(sus):
            if s in self.known_wolf:
                sus[s] = 100.0
            elif s in self.known_village:
                sus[s] = -100.0
        return sus

    def _trusted_seer(self):
        """A seer claimant whose checks matched a revealed role, or the only
        claimant when the other is dead. None when no claim is verified."""
        for claimant, checks in self.seer_claims.items():
            for t in checks:
                if t in self.revealed and self.revealed[t] == "werewolf":
                    return claimant
        return None

    def _votes_today(self):
        votes = {}
        for e in self.view["log"]:
            if e.get("kind") == "vote" and e.get("day") == self.day:
                votes[e["seat"]] = e.get("target")
        tally = {}
        for t in votes.values():
            if t is not None:
                tally[t] = tally.get(t, 0) + 1
        return votes, tally

    def top_suspect(self, exclude):
        """Highest-suspicion living seat outside `exclude`; ties break by
        lower seat number. Returns None when no candidate exists."""
        cands = [s for s in self.alive if s not in exclude]
        if not cands:
            return None
        return sorted(cands, key=lambda s: (-self.suspicion.get(s, 0.0), s))[0]

    def threat(self, seat):
        """How dangerous a seat is to the wolves (kill priority)."""
        score = 0.0
        if seat in self.seer_claims:
            score += 10.0
        if seat == self.trusted_seer:
            score += 5.0
        if seat in self.accused_me or seat in self.accused_partner:
            score += 5.0
        # Trusted town leads the village: remove the leaders.
        score += -self.suspicion.get(seat, 0.0) * 0.5
        score += len([1 for (a, t) in self.accusations if a == seat]) * 0.5
        return score


# ---------------------------------------------------------------- decisions

def choose_kill(st, view, rng):
    """Werewolf night kill. Match the partner's pick when second; else the
    highest-threat living non-wolf."""
    tonight = [v for v in (view.get("wolfVotes") or [])
               if v["night"] == st.day and v["seat"] != st.me]
    if tonight:
        target = tonight[0].get("target")
        if target is not None and target in st.alive \
                and target not in st.known_wolf:
            return target
    cands = [s for s in st.alive if s not in st.known_wolf]
    if not cands:
        return None
    best = max(st.threat(s) for s in cands)
    top = sorted(s for s in cands if st.threat(s) == best)
    return rng.choice(top)


def choose_protect(st, view, rng):
    """Doctor: the claimed seer first, then wolf-accusers, then trusted
    town. Options already exclude self and last night's target."""
    cands = [s for s in st.alive if s != st.me]
    last = (view.get("lastProtect") or {}).get("seat")
    cands = [s for s in cands if s != last]
    if not cands:
        return None
    claimants = [s for s in cands if s in st.seer_claims]
    if claimants:
        return sorted(claimants)[0]
    accusers = [s for s in cands
                if any(t in st.known_wolf for (a, t) in st.accusations
                       if a == s)]
    if accusers:
        return sorted(accusers, key=lambda s: (st.suspicion.get(s, 0.0), s))[0]
    return sorted(cands, key=lambda s: (st.suspicion.get(s, 0.0), s))[0]


def choose_inspect(st, view, rng):
    """Seer: the most suspicious seat whose team is still unknown."""
    unknown = [s for s in st.alive
               if s != st.me and s not in st.known_wolf
               and s not in st.known_village]
    if unknown:
        return sorted(unknown,
                      key=lambda s: (-st.suspicion.get(s, 0.0), s))[0]
    others = [s for s in st.alive if s != st.me]
    if not others:
        return None
    return sorted(others, key=lambda s: (-st.suspicion.get(s, 0.0), s))[0]


def choose_vote(st, view, rng):
    """Day vote. A known wolf is voted at once. Otherwise the top suspect,
    bandwagoning onto the day's plurality (suspicion + 1.5 per vote so far).
    As a wolf, never vote the partner; if the partner leads the tally by
    exactly one, vote the runner-up to force a tie and save them."""
    cands = [s for s in st.alive if s != st.me]
    if st.role == "werewolf":
        cands = [s for s in cands if s != st.partner]
    if not cands:
        return None
    known = [s for s in cands if s in st.known_wolf]
    if known and st.role != "werewolf":
        return sorted(known, key=lambda s: (-st.tally_today.get(s, 0), s))[0]
    eff = {s: st.suspicion.get(s, 0.0) + 1.5 * st.tally_today.get(s, 0)
           for s in cands}
    if st.role == "werewolf" and st.partner in st.tally_today:
        tally = sorted(st.tally_today.items(), key=lambda kv: (-kv[1], kv[0]))
        leader, lead_votes = tally[0]
        second = tally[1][1] if len(tally) > 1 else 0
        if leader == st.partner and lead_votes - second == 1:
            # One vote on the runner-up ties it: the partner survives.
            return sorted(cands, key=lambda s: (-eff[s], s))[0]
    return sorted(cands, key=lambda s: (-eff[s], s))[0]


# ---------------------------------------------------------------- statements

def _accuse_reason(st, target):
    """A plausible public reason to suspect `target`, from the log."""
    name = name_of(st.view, target)
    for e in st.view["log"]:
        if e.get("kind") == "vote" and e.get("seat") == target:
            t = e.get("target")
            if t in st.revealed and st.revealed[t] != "werewolf":
                return ("voted for %s and %s turned out to be a villager"
                        % (name_of(st.view, t), name_of(st.view, t)))
    for (a, t) in st.clearings:
        if a == target and t in st.revealed \
                and st.revealed[t] == "werewolf":
            return ("defended %s earlier, and %s was a wolf"
                    % (name_of(st.view, t), name_of(st.view, t)))
    if target not in st.speakers_today and st.day >= 2:
        return "has been quiet while the village argues"
    return "keeps pointing at villagers instead of wolves"


def speak_text(st, view, rng):
    """A statement for this turn, or None to pass. Templates vary by role
    and situation; the pick is seeded, and the previous statement is never
    repeated word for word."""
    me_name = st.you["name"]
    role = st.role
    day = st.day
    known_wolves = sorted(s for s in st.known_wolf if s in st.alive)
    exclude = {st.me}
    if role == "werewolf" and st.partner:
        exclude.add(st.partner)
    suspect = st.top_suspect(exclude)

    # How often to pass: early with little evidence, pass more.
    if day == 1 and rng.random() < 0.35:
        return None
    if rng.random() < 0.12:
        return None

    def fresh(options):
        options = [o for o in options
                   if o not in st.my_speeches and o]
        if not options:
            return None
        return cut(rng.choice(options))

    # Seer with a found wolf: claim and name them.
    if role == "seer" and known_wolves:
        t = known_wolves[0]
        return fresh([
            "I am the seer. I checked seat %d last night: they are a "
            "werewolf. Vote seat %d today." % (t, t),
            "I am the seer and seat %d is a wolf, I checked them. Do not "
            "let them talk their way out of this." % t,
            "Seat %d is a werewolf. I am the seer, I checked them last "
            "night. This is the vote today." % t,
        ])
    # Seer with clears only: share one, point at a suspect.
    if role == "seer" and suspect is not None:
        clears = sorted(s for s in st.known_village if s in st.alive)
        if clears:
            c = clears[0]
            return fresh([
                "I checked seat %d last night and they are not a wolf. "
                "Trust them. Look at seat %d instead."
                % (c, suspect),
                "Seat %d is clear, I checked them. My suspicion is on "
                "seat %d." % (c, suspect),
            ])
    # Werewolf, real seer dead: a fake seer claim can steer the vote.
    if role == "werewolf" and st.dead_seer and suspect is not None \
            and day >= 2 and rng.random() < 0.5:
        return fresh([
            "I am the seer. I checked seat %d last night and they are a "
            "werewolf. Vote seat %d." % (suspect, suspect),
            "Seat %d is a wolf, I checked them as the seer. Do not waste "
            "this day." % suspect,
        ])
    # Werewolf defending a partner under fire, lightly.
    if role == "werewolf" and st.partner and suspect is not None:
        top = st.top_suspect({st.me})
        if top == st.partner and rng.random() < 0.3:
            return fresh([
                "I do not think seat %d is a wolf. Their votes have "
                "followed the evidence, not the pack." % st.partner,
                "Seat %d looks town to me. Let us not exile a villager "
                "on a hunch." % st.partner,
            ])
    # Accuse the top suspect, with a reason from the log.
    if suspect is not None:
        reason = _accuse_reason(st, suspect)
        sname = name_of(view, suspect)
        if role == "werewolf":
            return fresh([
                "Seat %d %s. I think seat %d is the wolf here."
                % (suspect, reason, suspect),
                "I have been watching seat %d. %s. My vote is on them."
                % (suspect, reason.capitalize()),
                "Seat %d %s. The wolves want us split, but this is my "
                "pick today." % (suspect, reason),
            ])
        trusted = st.trusted_seer
        if trusted is not None and trusted != st.me:
            return fresh([
                "I trust seat %d on this. Seat %d %s."
                % (trusted, suspect, reason),
                "Listen to seat %d. Seat %d %s."
                % (trusted, suspect, reason),
            ])
        return fresh([
            "Seat %d %s. I think seat %d is a wolf."
            % (suspect, reason, suspect),
            "Seat %d %s. That is enough for me today."
            % (suspect, reason),
            "My pick is seat %d. %s."
            % (suspect, reason.capitalize()),
        ])
    # Nothing to say.
    return fresh([
        "No strong read yet. I will vote with the group today unless "
        "someone gives me a reason.",
        "Day %d, little to go on. I am watching, and I will speak when I "
        "have something real." % day,
        "I am a villager and I am listening. Say something worth voting "
        "on.",
    ])


# ---------------------------------------------------------------- main loop

def actions_taken(view):
    """How many actions I have taken in this game (from the log): the
    per-decision seed input, readable from the view alone."""
    me = view["you"]["seat"]
    n = 0
    for e in view["log"]:
        if e.get("seat") == me and e.get("kind") in (
                "speech", "pass", "vote"):
            n += 1
    # Night actions leave no public log entry; count them from the private
    # record instead (they are part of the view too).
    if view.get("seerResults"):
        n += len(view["seerResults"])
    if view.get("lastProtect"):
        n += 1
    if view.get("wolfVotes"):
        n += len([v for v in view["wolfVotes"] if v["seat"] == me])
    return n


def decide(env, seed, game_no, pending):
    """Answer one pending decision. Returns True when the action was taken."""
    kind = pending["decision"]
    view = pending["view"]
    st = Analysis(view)
    taken = actions_taken(view)
    rng = rng_for(seed, game_no, taken, kind)
    options = pending.get("options") or []

    if kind == "speak":
        text = speak_text(st, view, rng)
        if text is None:
            resp = env.act(game_no, "speak", **{"pass": True})
        else:
            resp = env.act(game_no, "speak", text=text)
        if resp.get("ok"):
            return True
        # Refused (empty/too_long): pass instead.
        resp = env.act(game_no, "speak", **{"pass": True})
        return bool(resp.get("ok"))

    if kind == "kill":
        target = choose_kill(st, view, rng)
    elif kind == "protect":
        target = choose_protect(st, view, rng)
    elif kind == "inspect":
        target = choose_inspect(st, view, rng)
    elif kind == "vote":
        target = choose_vote(st, view, rng)
    else:
        return False
    if target is not None and options and target not in options:
        target = options[0]
    resp = env.act(game_no, kind, target=target)
    if resp.get("ok"):
        return True
    # Refused: fall back to the first legal option, then to abstaining.
    if options:
        resp = env.act(game_no, kind, target=options[0])
        if resp.get("ok"):
            return True
    resp = env.act(game_no, kind, target=None)
    return bool(resp.get("ok"))


def main():
    prompt_file = os.environ.get("MAKEFUN_PROMPT_FILE", "")
    env_url = os.environ.get("MAKEFUN_ENV_URL", "")
    seed = os.environ.get("MAKEFUN_TRIAL_SEED", "")
    if not env_url:
        sys.stderr.write("MAKEFUN_ENV_URL is not set\n")
        return 2
    if prompt_file:
        try:
            with open(prompt_file, "r", encoding="utf-8") as f:
                f.read()
        except OSError as err:
            sys.stderr.write("cannot read prompt file: %s\n" % err)
            return 2
    env = Env(env_url)
    while True:
        try:
            obs = env.observe()
        except EnvError as err:
            sys.stderr.write("observe failed: %s\n" % err)
            return 1
        if obs.get("done"):
            return 0
        pending = obs.get("pending") or []
        if not pending:
            continue
        # One request at a time, in the order the env listed them.
        for item in pending:
            try:
                decide(env, seed, item["game"], item)
            except EnvError as err:
                sys.stderr.write("act failed: %s\n" % err)
                return 1
            except Exception as err:  # never stall the trial on one game
                sys.stderr.write("decision failed game %s: %s\n"
                                 % (item.get("game"), err))


if __name__ == "__main__":
    sys.exit(main())
