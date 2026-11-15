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
2. **The AI builds a profile.** An LLM reads your notes and turns them into a structured player profile: estimated stats, tendencies, and a confidence score for each one.
3. **The solver adapts.** The solver fixes ("node-locks") the opponent's strategy to match their profile, then calculates the *best response*: the strategy that wins the most against *that particular player*, not against a perfect one.
4. **You ask questions.** "I've got AsJs on the button, Dave raises from the cutoff, what do I do?" Then you get an answer tailored to Dave.

---

## Example

**Input notes (`notes/dave.md`):**

> Dave plays almost every hand preflop, especially after a couple of beers. He'll call down with any pair and basically never folds top pair. Rarely raises unless he has a monster. When he does bet big on the river it's always the nuts. Gets quiet and tight when he's losing.

**Generated profile (`profiles/dave.json`):**

```json
{
  "name": "Dave",
  "archetype": "Loose-passive calling station",
  "stats": {
    "vpip":                 { "estimate": 0.55, "confidence": "high" },
    "pfr":                  { "estimate": 0.08, "confidence": "medium" },
    "fold_to_cbet":         { "estimate": 0.25, "confidence": "medium" },
    "river_bluff_freq":     { "estimate": 0.02, "confidence": "high" },
    "aggression_factor":    { "estimate": 0.8,  "confidence": "medium" }
  },
  "tendencies": [
    "Over-calls with weak made hands",
    "Large river bets are heavily weighted to the nuts",
    "Rarely raises without a very strong hand"
  ],
  "dynamic_modifiers": {
    "when_drinking": { "vpip": "+0.10" },
    "when_losing":   { "vpip": "-0.15", "pfr": "-0.03" }
  },
  "source_notes": "notes/dave.md",
  "last_updated": "2026-11-15"
}
```

**In the app:** pick the spot (Dave opens from the CO, you're on the button, 100 BB deep), choose Dave as the opponent, and click **AJs** in the hand grid. The hand panel shows:

```
Against Dave (loose-passive calling station):

Preflop:  3-BET to 10bb (vs. GTO: mixed call/3-bet)
  -> Dave calls 3-bets too wide. Get value from his dominated aces and broadways.

Flop plan:
  - Hit top pair+ -> bet for value, size UP (66-75% pot). He won't fold top pair.
  - Missed       -> check back more often. Bluffs don't work on Dave.

River:
  - If Dave suddenly bets big -> fold everything but the nuts or near-nuts.

Why this differs from GTO: Dave's low fold-to-cbet means bluffs lose money,
while thin value bets gain extra.
```

---

## The App

Everything runs in a desktop/web app, so you never need the command line.

### Solver screen
- **13x13 hand grid** where every starting hand is coloured by how often to 3-bet, call or fold. Mixed strategies are shown as split colours. Suited hands sit top-right, offsuit hands bottom-left, pairs on the diagonal.
- **Exploit / GTO toggle** to switch between the strategy tailored to the selected friend and the baseline solver strategy.
- **Opponent picker** to choose which friend you're up against. Their key stats and confidence levels sit right underneath.
- **Spot bar** with positions, stack sizes, pot size and pot odds, like a real table.
- **Range summary** showing how often your whole range 3-bets, calls and folds, with combo counts.
- **Hand panel** for the clicked hand: action frequencies, a plain-English *why* based on that player's tendencies, and how the play differs from GTO.

### Players screen
- **Notes editor** for each friend, plus a log of short notes from each session
- **Rebuild profile with AI** to turn your notes into the structured profile
- **Profile view** with stats, confidence badges, tendencies, and "changes with the night" modifiers (tilt, drinking, etc.)
- **"Solve vs [player]"** to jump straight to the solver with that friend selected

---

## Features

- [ ] **Natural-language notes -> structured profiles** using an LLM
- [ ] **Confidence scores** so you know which reads rest on strong evidence and which are guesses
- [ ] **Exploitative solver** with node-locking and best-response calculation
- [ ] **GTO baseline comparison** showing how and why the exploitative play differs
- [ ] **Dynamic modifiers** for tilt, drinking, stack size, time of night
- [ ] **Profile updates** where you add new notes after each session and the profile is refined
- [ ] **Multi-way spots** (stretch goal: much harder to solve)
- [ ] **Session log import** to pull hand histories from a tracking app or manual entry

---

## Project Structure

```
poker-home-game-solver/
|-- notes/                  # Your raw notes, one file per player
|   |-- dave.md
|   `-- sarah.md
|-- profiles/               # AI-generated player profiles (JSON)
|-- src/
|   |-- profiler/           # LLM prompts + notes -> profile conversion
|   |   |-- prompts.py
|   |   |-- schema.py       # Profile data model + validation
|   |   `-- builder.py
|   |-- solver/             # Game tree, CFR, node-locking, best response
|   |   |-- tree.py
|   |   |-- cfr.py
|   |   |-- nodelock.py
|   |   `-- ranges.py
|   |-- engine/             # Hand evaluation, equity calculations
|   |-- api/                # Local web server the UI talks to
|   `-- ui/                 # Front end: Solver + Players screens
|-- tests/
|-- config.example.yaml
`-- README.md
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- An API key for an LLM provider (for the profile builder)

### Installation

```bash
git clone https://github.com/<your-username>/poker-home-game-solver.git
cd poker-home-game-solver
pip install -r requirements.txt
cp config.example.yaml config.yaml   # then add your API key
```

Installation is the only step that uses a terminal. After that, launch the app and do everything from the UI.

### Quick Start

1. **Open the app** and go to **Players**.
2. **Add a friend** and write your notes about them in plain English.
3. **Click "Rebuild profile with AI"**, then check the stats and tendencies it produces.
4. **Click "Solve vs [name]"**, set up the spot, and click any hand in the grid to see how to play it.

---

## Technical Approach

### Profile Builder
The LLM receives your notes plus a strict JSON schema and is told to:
- Map descriptive language ("plays every hand") to numeric estimates (VPIP ~ 50%+)
- Mark each estimate with a confidence level based on how much evidence the notes contain
- Fall back to population defaults for typical home-game players where the notes say nothing
- Never invent tendencies that aren't supported by the notes

### Solver
- **Game abstraction:** heads-up postflop spots first, using bet-size and card abstraction to keep trees tractable
- **Algorithm:** Counterfactual Regret Minimisation (CFR+ or Discounted CFR)
- **Exploitation:** the opponent's strategy is node-locked to frequencies derived from their profile, then hero's best response is computed
- **Safety blend (optional):** mix the exploitative strategy with GTO according to profile confidence, so a shaky read doesn't leave you wide open

### Possible Libraries / References
- Hand evaluation: [`treys`](https://github.com/ihendley/treys), `phevaluator`
- CFR reference implementations and papers: Zinkevich et al. (2007), Brown & Sandholm (Libratus/Pluribus papers)
- Existing solvers for comparison: TexasSolver (open source), PioSOLVER, GTO Wizard

---

## Roadmap

| Phase | Goal |
|-------|------|
| **1** | Profile schema + LLM notes-to-profile pipeline |
| **2** | Hand evaluator + equity calculator |
| **3** | Preflop exploitative ranges per profile |
| **4** | Heads-up postflop solver with node-locking (river first, then turn, then flop) |
| **5** | Solver screen: hand grid, exploit/GTO toggle, hand panel |
| **6** | Players screen: notes editor + AI profile view |
| **7** | Profile updating from new notes + session history |

---

## Limitations & Honest Caveats

- **Garbage in, garbage out.** Profiles are only as good as your notes. Small samples and memory bias can produce confident-looking but wrong reads.
- **Exploits cut both ways.** A strategy built to beat one player can be badly exploitable if your read is off or they adjust.
- **Full no-limit hold'em is huge.** Solutions use abstractions and are approximate, especially for multi-way pots.
- **Friends adapt.** Reread and update profiles regularly.

---

## Fair Play

This is a **study tool for use away from the table**: review spots before game night and analyse hands afterwards. Using it during a hand is real-time assistance, which most players (including your friends, probably) would consider cheating. Keep the games fun.
