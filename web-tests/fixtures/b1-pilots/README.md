# Beginner practice fixtures

Four beginner-practice questions (schema 0.2, `answer_profile: "beginner-practice"`) and the traces recorded from them,
kept here byte for byte as test fixtures for the step player's narrator line and the practice presentation of the question
view. They are not part of any course or of the site data. `web-tests/b1-practice.test.mjs` pins their SHA-256 sums, so
any change to these files fails the tests until the pins are updated on purpose.

`course.json` is a small course built from the same four questions, with the lesson fields the beginner course pages read
(`practice_summary` and `introductions`), taken word for word from the authors' lesson notes. It is a fixture, not a course.

Licence: each question states its own licence (CC-BY-SA-4.0); the traces and `course.json` are derived from that material
under the same licence.
