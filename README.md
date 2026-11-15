# Home Game Solver

**A personalised poker study tool that turns notes about your friends into player profiles, then works out how to play against each of them.**

Standard GTO solvers assume your opponent plays perfectly. Your friends don't. Dave calls too much, Sarah never bluffs the river, and Tom goes on tilt after losing one big pot. This project uses that knowledge instead of throwing it away.

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

**In the app:** with the default spot (Dave opens 2.5 BB from the CO, you're on the button, 100 BB deep), clicking **AJs** shows:

```
AJs                                   4 combos - suited
3-Bet 100%   Call 0%   Fold 0%

WHY  This 3-bet works mainly because Dave folds 45% of the time. When called,
     AJs has 39% equity, but the folds make it profitable (+0.89 BB).

Compared with GTO:  3-Bet +100%  Call -100%  Fold 0%
```

Dave rarely open-raises, so his opening range is strong; the baseline just calls AJs. The exploit 3-bets because the profile says he folds to 3-bets fairly often (note the *low* confidence on that stat).

---

## The App

Everything runs in a local web app, so after installing you never need the command line.

### Solver screen
- **13x13 hand grid** where every starting hand is coloured by how often to 3-bet, call or fold. Mixed strategies are shown as split colours. Suited hands sit top-right, offsuit hands bottom-left, pairs on the diagonal.
- **Exploit / GTO toggle** to switch between the strategy tailored to the selected friend and the baseline strategy.
- **Opponent picker** to choose which friend you're up against. Their key stats and confidence levels sit right underneath.
- **Spot bar** with positions, stack sizes, pot size and pot odds. **New spot** changes the positions, open size, 3-bet size and stacks.
- **Range summary** showing how often your whole range 3-bets, calls and folds, with combo counts.
- **Hand panel** for the clicked hand: action frequencies, a plain-English *why* built from the solver's numbers, and how the play differs from the baseline.

### Players screen
- **Notes editor** for each friend (saves automatically), plus a log of short notes from each session
- **Rebuild profile with AI** to turn your notes into the structured profile
- **Profile view** with stats, confidence badges, tendencies, and "changes with the night" modifiers (tilt, drinking, etc.)
- **"Solve vs [player]"** to jump straight to the solver with that friend selected

---

## Features

- [x] **Natural-language notes -> structured profiles** using Claude (via the Claude Code CLI)
- [x] **Confidence levels** so you know which reads rest on strong evidence and which are guesses
- [x] **Exploitative solver** with node-locking: preflop (facing an open) and heads-up river
- [x] **GTO baseline comparison** showing how and why the exploitative play differs
- [x] **Profile updates**: add session notes after each game and rebuild the profile
- [ ] **Dynamic modifiers** applied in the solver (currently shown on the profile only)
- [ ] **Turn and flop solving**
- [ ] **River spots in the UI** (the river solver is available through the API only)
- [ ] **Multi-way spots** (stretch goal: much harder to solve)
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
|   |-- solver/             # Game trees, CFR+, node-locking, spots
|   |   |-- tree.py
|   |   |-- cfr.py
|   |   |-- nodelock.py
|   |   |-- ranges.py
|   |   |-- preflop.py      # Facing-an-open spot (powers the hand grid)
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

Then open http://127.0.0.1:5000. The first start builds a preflop equity table (about a minute) and caches it in `data/`. To change the port or Claude settings, copy `config.example.yaml` to `config.yaml` and edit it.

Run the tests with `python -m pytest`.

### Quick Start

1. **Open the app** and go to **Players**.
2. **Add a friend** and write your notes about them in plain English.
3. **Click "Rebuild profile with AI"** (about 20 seconds), then check the stats and tendencies it produces.
4. **Click "Solve vs [name]"**, set up the spot with **New spot**, and click any hand in the grid to see how to play it.

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
- **Algorithm:** vectorised CFR+ over all hands at once (numpy), with exploitability checks in the tests
- **Node-locking:** a lock fixes part of a player's strategy per hand; range-level stats ("folds to 3-bets 45%") become per-hand locks by assigning actions from the weakest hands up
- **Preflop (hand grid):** hero faces a single open raise and can fold, call or 3-bet; villain can fold or call a 3-bet. Villain opens their strongest `open_raise` share of hands. Hands that see a flop are scored by all-in equity from a cached 169x169 Monte Carlo table (~1% error per matchup). Players still to act are assumed to fold, and there are no 4-bets.
  - **Exploit:** villain's 3-bet response is locked to `fold_to_3bet`.
  - **GTO baseline:** the same spot with villain's response left to the solver (the equilibrium of this simplified game).
- **River:** heads-up check/bet/raise tree with configurable bet sizes and an all-in raise. With a profile, villain's facing-a-bet nodes fold the weakest `fold_to_cbet` share (a stand-in, since that stat is about flop c-bets), and the weaker half of villain's range bluffs at `river_bluff`; everything else re-optimises.

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
| **3** | Preflop exploitative ranges per profile | Done (simplified model) |
| **4** | Heads-up postflop solver with node-locking (river first, then turn, then flop) | River done |
| **5** | Solver screen: hand grid, exploit/GTO toggle, hand panel | Done |
| **6** | Players screen: notes editor + AI profile view | Done |
| **7** | Profile updating from new notes + session history | Done (manual rebuild) |

---

## Limitations & Honest Caveats

- **Garbage in, garbage out.** Profiles are only as good as your notes. Small samples and memory bias can produce confident-looking but wrong reads.
- **The preflop model is simplified.** It ignores postflop skill and position, so it favours 3-betting when an opponent folds often and treats every flop as an all-in. Use it to see *which direction* to adjust, not as exact frequencies.
- **Exploits cut both ways.** A strategy built to beat one player can be badly exploitable if your read is off or they adjust.
- **Full no-limit hold'em is huge.** Solutions use small trees and abstractions and are approximate.
- **Friends adapt.** Reread and update profiles regularly.

---

## Fair Play

This is a **study tool for use away from the table**: review spots before game night and analyse hands afterwards. Using it during a hand is real-time assistance, which most players (including your friends, probably) would consider cheating. Keep the games fun.

---

## License

MIT (or your choice)
