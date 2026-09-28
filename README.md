# SPALI

**Study Planning via ASP and LLM Integration**

SPALI is a command-line study planner that helps students build semester plans that comply with their study regulations. Students describe their situation and preferences; an LLM interprets the input, and an Answer Set Programming (ASP) solver guarantees that every suggested plan satisfies the program's study regulations.

## Process

The CLI walks the student through four phases:

1. **Core info**: current semester and program-specific details
2. **Completed modules**: modules already completed from prior semesters
3. **Semester planner**: choose courses for the upcoming semester; the solver rejects choices that would make the degree impossible to finish
4. **Plan navigation**: explore complete study plans via natural language preferences 

## Requirements

- Python 3.12
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A [Voyage AI API key](https://dashboard.voyageai.com/)

## Installation

```bash
git clone https://github.com/tsither/SPALI.git
cd SPALI

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# then add your ANTHROPIC_API_KEY and VOYAGE_API_KEY to .env
```

The vector database for the included program is part of the repository, so no build step is required.

## Usage

```bash
python cli.py -p cogsys
```

| Flag | Description |
|---|---|
| `-p ID` | Study program to use (e.g. 'cogsys') |
| `-s SEM`, `--semester SEM` | Semester to plan for (e.g. `sose26`).
| `-b`, `--brave` | Brave mode: select courses one at a time using ASP brave consequences |

Defaults such as the program, semester and model names are set in `config.yaml`.

## Key Files

```
cli.py                  Entry point and workflow
config.yaml             Default settings
prompts.yaml            LLM prompts
core/                   Workflow phases, student state, LLM extraction, plan navigation
asp/                    ASP encodings
  lp/                   General encoding, extensions, externals
  study_program_instances/   Instances each study program
  preferences/          Preference (weak constraint) encodings
db/chroma/              Vector store and database access
setup/                  Program data and database builder
testing/                Example test cases for preferences
```

## Included program

**Cognitive Systems (M.Sc.), University of Potsdam**, with course data2526`) and summer 2026 (`sose26`).

## Author

Theodore Sither
