# follow (working title)

**Learn to read code, not just write it.**

An open-source platform for learning to read and understand code, from beginner to advanced. AI writes code faster than people do. Understanding and reviewing it is the new bottleneck.

> **Current question bank:** 74 free questions — 34 Python, 30 Read like a computer, 5 Production, and 5 SQL.

## How it works

- Courses present code followed by multiple-choice questions. A standard question has one correct answer, one half-correct answer, and at least three wrong answers (five to eight choices). A beginner-practice question is a gentler exercise with exactly four choices (one correct, one half-correct, two wrong) and no points. Wrong answers map to named misconceptions and have their own feedback.
- Question types include predicting output, finding an input that crashes code, reasoning about a removed line, comparing equivalent rewrites, and reviewing code written by AI.
- You can start without an account. Progress stays in your browser.
- Anyone can build question libraries. The platform provides the question schema, validator, and shared concept maps.

## Repository layout

| Path | What |
|---|---|
| [`public/`](public/) | Static site: plain HTML, CSS, and ES modules; no build step. |
| [`public/data/`](public/data/) | Public questions, courses, concept maps, traces, and derived indexes. |
| [`schema/`](schema/) | JSON Schemas for questions, courses, and concept maps, plus the Python concept map. |
| [`validator/`](validator/) | Python content validator and static data export/check tools. |
| [`adaptive/`](adaptive/) | Experimental local practice-recommendation policy. It is deterministic advice, not a placement model, assessment, mastery tracker, or production UI. |
| [`server/`](server/) | Accounts/backend foundation, SQLite storage, HTTP endpoints, and a loopback development runner. The runner is mock/test only; it is not a live service or proof of real-provider behavior. See [`server/README.md`](server/README.md). |
| [`content/`](content/) | Illustrative example content. |
| [`web-tests/`](web-tests/) | Browser-module tests using Node's built-in test runner. |

## Development setup

The CI workflow currently checks Python 3.11 and 3.14, and uses Node 24. Install the Python requirements and use Node 24 or newer for the web tests:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r validator/requirements.txt -r server/requirements.txt
```

For a static-only preview, serve `public/` from the repository root:

```sh
python3 -m http.server -d public 8123
# Open http://localhost:8123/
```

For the local mock backend and site on one loopback origin, run:

```sh
python -m server.dev --port 8000
# Open http://127.0.0.1:8000/follow/
```

The development runner uses a temporary SQLite database and generated local secrets, binds to loopback, and makes no external provider calls. Provider/payment flows are disabled there. This setup is for local development and tests, not deployment. See [`server/README.md`](server/README.md) for configuration and limitations.

## Check your changes

```sh
python -m unittest discover -s validator/tests
python -m unittest discover -s schema/tests
python -m unittest discover -s server/tests
python -m validator export --check
python -m validator trace --check
node --test 'web-tests/*.test.mjs'
```

To check a question while you write it, pass the relevant course vocabulary (Python is the default):

```sh
python -m validator public/data/questions/<python-question>.json
python -m validator --concept-map schema/concepts/production-v0.json public/data/questions/<production-question>.json
python -m validator --concept-map schema/concepts/sql-v0.json public/data/questions/<sql-question>.json
```

CI runs the checks above. Question code is checked statically and is never executed in CI. Running snippets locally requires explicit flags and is intended only for code you have read and trust. See [`validator/README.md`](validator/README.md).

## Licenses

| Part | License |
|---|---|
| Platform code | [AGPL-3.0](LICENSE) |
| Question schema, concept maps, and validator | [MIT](LICENSES/MIT.txt) |
| Course and question content | [CC BY-SA 4.0](LICENSES/CC-BY-SA-4.0.txt) |
| Letter outlines in the logo's wordmark (`public/js/components/logo.js`) | [SIL OFL 1.1](LICENSES/OFL-1.1.txt) |

Directories under a license other than AGPL-3.0 say so in their own README.

The wordmark's letter outlines are drawn from JetBrains Mono, Copyright 2020 The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono), under the SIL Open Font License 1.1, whose full text and the font's copyright notice are in [`LICENSES/OFL-1.1.txt`](LICENSES/OFL-1.1.txt). The font declares no Reserved Font Name.
