# Streamlined README Design

## Goal

Turn the root README into a concise, onboarding-first entry point for NoxyDB.
It should help a new user understand the project and run either the embedded
Noxy API or the local server with its Python client without first reading
implementation details.

## Audience and language

The primary audience is a developer discovering NoxyDB for the first time.
The README remains in English to preserve the repository's current public
documentation language. The existing Portuguese deep-dive remains available
for readers who want implementation details.

## Content structure

The README will contain, in this order:

1. A short project description and a compact feature list.
2. A small architecture diagram showing embedded and client/server usage.
3. An embedded Noxy quickstart.
4. A local-server and Python-client quickstart.
5. Links to executable examples and the detailed architecture document.
6. A concise API reference and operational limitations.
7. Commands for running the test suites.

## Content removed or relocated

Low-level explanations of socket deadlines, polling synchronization, surplus
byte detection, append-log grammar, replay validation, and failure-state
transitions will not be repeated in the README. The README will link to
`docs/noxydb-como-funciona.md`, which already documents the internals in depth.

The server's local-only binding, lack of authentication, one-process-per-file
constraint, logical Python close behavior, and durability boundary remain in
the README because they affect safe operation.

## Presentation constraints

- Target roughly 120 to 160 lines.
- Prefer short paragraphs, bullets, and runnable code blocks.
- Keep one compact Mermaid diagram without implementation-level nodes.
- Avoid duplicating details already covered by the deep-dive document.
- Use relative Markdown links so documentation works on GitHub and locally.

## Verification

- Confirm every referenced path exists.
- Run a Markdown link/path check for local links.
- Run the repository's documented test commands if the required Noxy runtime
  is available; otherwise report the missing prerequisite explicitly.
- Review the final diff for accuracy, concision, and accidental removal of
  safety-critical operational guidance.

## Pull request

The change will be committed on `docs/streamline-readme` and proposed against
`develop`. The pull request will summarize the streamlined onboarding flow and
the relocation of implementation detail to the existing deep-dive document.
