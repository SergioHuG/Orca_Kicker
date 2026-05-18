# Orca Kicker

Pure KinematicMode linear motor kicker application.
Refactored from Orca Applicator V1.

## State Machine

```
BOOT → AUTOZERO_HOME → IDLE → KICK → RETURN_HOME → IDLE
                         ↑              |
                         └──────────────┘
                   IDLE ↔ SLEEP (toggle via sleep button)
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python -m orca_kicker
# or with a custom config:
python -m orca_kicker --config configs/default.yaml
```

## Project Structure

```
orca_kicker/
├── CLAUDE.md              # AI session rules — read every session
├── configs/
│   └── default.yaml       # Default configuration
├── logs/                  # CSV logs output here
├── src/
│   └── orca_kicker/
│       ├── orca_client.py     # pyorcasdk interface (only file that calls SDK)
│       ├── triggers.py        # Thread-safe event queue
│       ├── gpio_inputs.py     # Input buttons
│       ├── gpio_outputs.py    # Output pins (sleep indicator)
│       ├── kick_sequence.py   # Kick cycle logic
│       ├── config.py          # Config loader
│       ├── logging_csv.py     # CSV logger
│       └── main.py            # State machine + event loop
└── requirements.txt
```
