# Content and examples

This directory holds course and question content for the platform.

## License

Course and question content is licensed under **CC BY-SA 4.0** (see
[`../LICENSES/CC-BY-SA-4.0.txt`](../LICENSES/CC-BY-SA-4.0.txt)). This applies to
files such as `examples/independent-if.json` and to any published question,
explanation or feedback text. It does not apply to the machine-readable contract
and concept vocabulary under `../schema/`, which are licensed under MIT (see
[`../LICENSES/MIT.txt`](../LICENSES/MIT.txt)).

The split follows the same principle as the rest of the project: the interchange
format is permissive infrastructure, while authored questions are share-alike
learning material.

## Examples are illustrative

Files under `examples/` are illustrative fixtures used to exercise the contract
and its tooling. They are self-written, are not copied from any external source,
and have **not** been reviewed for pedagogical quality. They are not approved
course content. Passing automated checks only confirms that a file matches the
contract and that its declared observations hold; it does not certify question
quality, distractor strength or genuine half credit.

## Format

Each question is a single UTF-8 JSON file matching
[`../schema/question.schema.json`](../schema/question.schema.json). See
[`../schema/README.md`](../schema/README.md) for the field reference and for the
concept and misconception identifiers available in the shared map.
