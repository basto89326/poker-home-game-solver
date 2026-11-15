# Home Game Solver

**A personalised poker study tool that turns notes about your friends into player profiles, then works out how to play against each of them.**

Standard GTO solvers assume your opponent plays perfectly. Your friends don't. Dave calls too much, Sarah never bluffs the river, and Tom goes on tilt after losing one big pot. This project uses that knowledge instead of throwing it away.

## Run It

```bash
pip install -r requirements.txt   # first time only
python -m src.api                 # start the app
```

Then open **http://127.0.0.1:5000** in your browser. Stop the app with `Ctrl+C`.

- First start builds a preflop equity table (about a minute, cached afterwards).
- "Rebuild profile with AI" needs [Claude Code](https://claude.com/claude-code) installed and logged in (no API key). See [Getting Started](#getting-started).
- Run the tests with `python -m pytest`.

---

## How It Works

```
+--------------+     +------------------+     +--------------------+     +------------------+
|  Your notes  | --> |  AI profile      | --> |  Exploitative      | --> |  Strategy advice |
|  (free text) |     |  builder (LLM)   |     |  solver            |     |  per spot/player |
+--------------+     +------------------+     +--------------------+     +------------------+
```

1. **Write notes** about each friend in plain English, the way you'd describe them to someone else.
2. **The AI builds a profile.** Claude reads your notes and turns them into a structured player profile: estimated stats, tendencies, and a confidence level for each one.
3. **The solver adapts.** The solver fixes ("node-locks") the opponent's strategy to match their profile, then calculates the *best response*: the strategy that wins the most against *that particular player*, not against a perfect one.
4. **You study the spot.** Pick an opponent and a spot, click a hand in the grid, and see how to play it against them and why.

---

## Example

**Input notes (`notes/dave.md`):**

> Dave plays almost every hand preflop, especially after a couple of beers. He'll call down with any pair and basically never folds top pair. Rarely raises unless he has a monster. When he does bet big on the river it's always the nuts. Gets quiet and tight when he's losing.

**Generated profile (`profiles/dave.json`, shortened):**

```json
{
  "name": "Dave",
  "archetype": "Loose passive calling station",
  "summary": "Dave plays almost every hand, calls down with any pair, rarely raises, and only bets big on the river with the nuts.",
  "stats": {
    "vpip":         { "estimate": 0.6,  "confidence": "high" },
    "pfr":          { "estimate": 0.04, "confidence": "medium" },
    "open_raise":   { "estimate": 0.08, "confidence": "medium" },
    "fold_to_3bet": { "estimate": 0.45, "confidence": "low" },
    "fold_to_cbet": { "estimate": 0.25, "confidence": "high" },
    "aggression":   { "estimate": 0.15, "confidence": "medium" },
    "river_bluff":  { "estimate": 0.03, "confidence": "medium" }
  },
  "tendencies": ["Basically never folds top pair.", "A big river bet from Dave always means the nuts."],
  "modifiers": [
    { "condition": "After a couple of beers", "description": "Plays even more hands preflop.", "adjustments": { "vpip": 0.1 } }
  ],
  "last_updated": "2026-11-15"
}
```

**In the app:** seat Dave (HJ), Priya (CO), you (BTN), Sarah (SB) and Tom (BB), deal, and play it through. Dave's grid shows what his profile predicts (raise his top 8%, limp the next 52%, fold the rest); you pick "raise to $0.50" for him and "fold" for Priya. On your turn, clicking **KQs** shows:

```
KQs                                        4 combos - suited
Raise 100%   Call 0%   Fold 0%
Average result: Raise to $1.50 +$0.14 - Call $0.50 -$0.06 - Fold $0.00

WHY  Close call: the difference is within the simulation's margin of error,
     so either choice is reasonable. Raising to $1.50 averages +$0.14, vs
     -$0.06 for calling. Everyone folds 18% of the time; when called, KQs wins
     39% of the pot on average.
```

---

## The App

Everything runs in a local web app, so after installing you never need the command line.

### Solver screen: play a hand through
- **Table setup:** choose how many players (2-9), who sits in each seat (you plus friends from your profiles) and each stack. Defaults come from your home game: $0.05/$0.10 blinds, $20 stacks.
- **Seat by seat:** the screen shows the player to act. Pick their action (fold, check/call, or raise to an amount; the usual open of $0.50 and 3x re-raises are pre-filled) and it moves to the next seat until preflop is over. **Undo**, **Restart hand** and **Change players** are always there.
- **Predicted / Best play toggle** on a friend's turn: *Predicted* is what their profile says they'll do with each hand; *Best play* is the most profitable choice in their seat against everyone else's profiles. On your turn you always see best play.
- **13x13 hand grid** coloured by raise / call / fold. Hands a player can no longer have (given their earlier actions) are faded; close calls are marked with `~`.
- **Seats bar** with stacks, bets, pot, amount to call and pot odds, plus an **action log**.
- **Profile panel** with the acting player's key preflop stats and confidence levels.
- **Range summary** and a **hand panel** with frequencies, average results in dollars and a plain-English *why*.

### Players screen
- **Notes editor** for each friend (saves automatically), plus a log of short notes from each session
- **Rebuild profile with AI** to turn your notes into the structured profile
- **Profile view** with stats, confidence badges, tendencies, and "changes with the night" modifiers (tilt, drinking, etc.)
- **"Play a hand vs [player]"** to jump to the table setup with that friend seated
- **Remove player** (asks first) to delete a friend's notes and profile

---

## Features

- [x] **Natural-language notes -> structured profiles** using Claude (via the Claude Code CLI)
- [x] **Confidence levels** so you know which reads rest on strong evidence and which are guesses
- [x] **Preflop hand play-through** for 2-9 players: predicted play per profile and best play per seat
- [x] **Heads-up river solver** (CFR+) with node-locking to a profile
- [x] **Profile updates**: add session notes after each game and rebuild the profile
- [x] **Add and remove players**
- [ ] **Flop**: choose the three flop cards and keep playing the hand
- [ ] **Turn and river in the play-through** (the river solver is available through the API only)
- [ ] **Dynamic modifiers** applied in the solver (currently shown on the profile only)
- [ ] **Session log import** to pull hand histories from a tracking app

---

## Project Structure

```
poker-home-game-solver/
|-- notes/                  # Your raw notes, one Markdown file per player
|-- profiles/               # AI-generated player profiles (JSON)
|-- designs/                # UI mock-ups the front end follows
|-- data/                   # Cached preflop equity table (generated, not committed)
|-- src/
|   |-- config.py           # Settings from config.yaml
|   |-- engine/             # Cards, vectorised hand evaluator, equity
|   |   |-- cards.py
|   |   |-- evaluator.py
|   |   `-- equity.py
|   |-- profiler/           # Notes -> profile
|   |   |-- prompts.py      # The prompt sent to Claude
|   |   |-- schema.py       # Profile data model, stat definitions, validation
|   |   |-- builder.py      # Runs the Claude Code CLI
|   |   `-- store.py        # Reads/writes notes and profiles
|   |-- solver/
|   |   |-- table.py        # Preflop betting rules for 2-9 players
|   |   |-- predict.py      # Profile -> predicted fold/call/raise per hand
|   |   |-- bestplay.py     # Monte Carlo best play against everyone's profiles
|   |   |-- handplay.py     # Replays a hand and analyses the next decision
|   |   |-- ranges.py       # Range strings, preflop hand strength
|   |   |-- nodelock.py     # Locking strategies by hand strength
|   |   |-- tree.py         # River betting tree
|   |   |-- cfr.py          # CFR+ solver
|   |   `-- river.py        # Heads-up river spot
|   |-- api/                # Flask server the UI talks to
|   `-- ui/                 # Plain HTML/CSS/JS front end
|-- tests/
|-- config.example.yaml
|-- requirements.txt
`-- README.md
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- [Claude Code](https://claude.com/claude-code) installed and logged in to your Claude plan (used by the profile builder)

No API key is needed. Profiles are built by running the Claude Code CLI, which uses your Claude plan's usage rather than separately billed API credits. The builder refuses to run if `ANTHROPIC_API_KEY` is set, because Claude Code would then bill that key instead.

### Installation

```bash
git clone https://github.com/<your-username>/poker-home-game-solver.git
cd poker-home-game-solver
pip install -r requirements.txt
python -m src.api
```

Then open http://127.0.0.1:5000. The first start builds a preflop equity table (about a minute) and caches it in `data/`. To change your game's blinds, usual open, default stack, the port or Claude settings, copy `config.example.yaml` to `config.yaml` and edit it.

Run the tests with `python -m pytest`.

### Quick Start

1. **Open the app** and go to **Players**.
2. **Add a friend** and write your notes about them in plain English.
3. **Click "Rebuild profile with AI"** (about 20 seconds), then check the stats and tendencies it produces.
4. **Click "Play a hand vs [name]"**, choose who else is in the hand, click **Deal hand**, and play it through seat by seat. Click any hand in the grid to see how it's played and why.

---

## Technical Approach

### Profile Builder
Claude (through `claude -p`, with tools disabled) receives your notes, exact definitions of each stat, and a strict JSON shape. It is told to:
- Map descriptive language ("plays every hand") to numeric estimates (VPIP ~ 50%+)
- Mark each estimate with a confidence level based on how much evidence the notes contain
- Fall back to placeholder defaults (confidence "low") where the notes say nothing
- Never invent tendencies that aren't supported by the notes

The reply is validated (ranges, required stats, PFR <= VPIP) before it is saved.

### Solver
- **Predicted play (preflop):** each profile turns into shares of the player's *current* range by hand strength: with no raise yet, raise the top `open_raise` and limp up to `vpip`; facing a raise, re-raise `three_bet` and call `call_open`; facing a re-raise, fold `fold_to_3bet`. After every action the player's range is narrowed to the hands that would have taken it.
- **Best play (preflop):** a Monte Carlo simulation (400 deals per hand) of every option against everyone else's predicted play. For each deal it averages exactly over who folds and who continues, and uses the exact equity table for heads-up showdowns, which removes most of the noise. Simplifications: one round of responses (re-raises count as calls), hands scored by all-in equity (no postflop play), no side pots. Decisions within the simulation's margin of error are marked as close calls.
- **River:** vectorised CFR+ on a heads-up check/bet/raise tree. With a profile, villain's facing-a-bet nodes are node-locked to fold the weakest `fold_to_cbet` share (a stand-in, since that stat is about flop c-bets), and the weaker half of villain's range bluffs at `river_bluff`; everything else re-optimises.
- **Equity:** a cached 169x169 Monte Carlo table of preflop all-in equity (~1% error per matchup) and a vectorised 5-7 card hand evaluator.

### Libraries / References
- Hand evaluation: own vectorised evaluator (`src/engine/evaluator.py`)
- CFR: Zinkevich et al. (2007), Tammelin (2014) for CFR+
- Existing solvers for comparison: TexasSolver (open source), PioSOLVER, GTO Wizard

---

## Roadmap

| Phase | Goal | Status |
|-------|------|--------|
| **1** | Profile schema + LLM notes-to-profile pipeline | Done |
| **2** | Hand evaluator + equity calculator | Done |
| **3** | Preflop play-through with predicted and best play per profile | Done (simplified model) |
| **4** | Heads-up postflop solver with node-locking (river first, then turn, then flop) | River done |
| **5** | Solver screen: table setup, hand grid, seat-by-seat actions, hand panel | Done |
| **5b** | Flop: pick the flop cards and continue the hand | Next |
| **6** | Players screen: notes editor + AI profile view | Done |
| **7** | Profile updating from new notes + session history | Done (manual rebuild) |

---

## Limitations & Honest Caveats

- **Garbage in, garbage out.** Profiles are only as good as your notes. Small samples and memory bias can produce confident-looking but wrong reads.
- **The preflop model is simplified.** Best play treats every flop as an all-in (no postflop skill or position), so it favours raising when opponents fold often. Predicted play sorts hands by one strength number and ignores bet size and position. Use it to see *which direction* to adjust, not as exact frequencies.
- **Best play is a simulation.** Results have a margin of error; hands marked `~` are too close to call.
- **Exploits cut both ways.** A strategy built to beat one player can be badly exploitable if your read is off or they adjust.
- **Full no-limit hold'em is huge.** Solutions use small trees and abstractions and are approximate.
- **Friends adapt.** Reread and update profiles regularly.

---

## Fair Play

This is a **study tool for use away from the table**: review spots before game night and analyse hands afterwards. Using it during a hand is real-time assistance, which most players (including your friends, probably) would consider cheating. Keep the games fun.

---

## License

MIT (or your choice)
