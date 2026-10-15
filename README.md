# follow (working title)

**Learn to read code, not just write it.**

An open source platform for learning to read and understand code, from beginner to advanced. AI writes code faster than people do. Understanding and reviewing it is the new bottleneck.

> **Status: early draft.** The first course is live at <https://sagosand.se/follow/>. It has 14 reviewed Python questions in 7 lessons. The name "follow" is a working title and will change.

## How it works

- Courses are code blocks followed by multiple-choice questions. Each question has one correct answer, one half-correct answer and at least three wrong answers. Every wrong answer maps to a named misconception and comes with its own feedback.
- Question types vary: what does this print, which input crashes it, what happens if a line is removed, which rewrite is equivalent, and "an AI wrote this, what is wrong?".
- You can start right away without an account. Progress stays in your browser.
- Planned: an adaptive placement test that maps what you already know and suggests where to start.
- Anyone can build question libraries. The platform owns the contract: a question schema, a validator and a shared concept map.

## Repository layout

| Path | What |
|---|---|
| [`public/`](public/) | The static site: plain HTML, CSS and ES modules, with no build step. Served as is. |
| [`public/data/`](public/data/) | Questions, courses and the derived index the site reads. |
| [`schema/`](schema/) | JSON Schemas for questions, courses and the concept map, plus the Python concept map. |
| [`validator/`](validator/) | Python validator and static data export. |
| [`content/`](content/) | Illustrative example content. |
| [`web-tests/`](web-tests/) | Tests for the site's modules (`node:test`). |

## Run it locally

```sh
python3 -m http.server -d public 8123   # then open http://localhost:8123/
```

The site uses relative URLs only, so it also works under a sub-path.

## Check your changes

```sh
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r validator/requirements.txt
python3 -m unittest discover -s validator/tests
python3 -m unittest discover -s schema/tests
python3 -m validator public/data/questions/*.json   # static checks
python3 -m validator export --check                 # derived data is up to date
node --test 'web-tests/*.test.mjs'
```

CI runs the same checks on every pull request. Question code is checked statically in CI and never executed there. Running snippets locally needs explicit flags and is meant only for code you have read and trust. See [`validator/README.md`](validator/README.md).

## Licenses

| Part | License |
|---|---|
| Platform code | [AGPL-3.0](LICENSE) |
| Question schema, concept map and validator | [MIT](LICENSES/MIT.txt) |
| Course and question content | [CC BY-SA 4.0](LICENSES/CC-BY-SA-4.0.txt) |

Directories under a license other than AGPL-3.0 say so in their own README.
