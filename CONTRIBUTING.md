# Contributing

Thanks for helping. This is a small project, so the process is short.

## Set up and check your change

```bash
./hippocampus up              # your own throwaway stack (it rebuilds the image from your code) on 127.0.0.1 (./hippocampus reset wipes it)
scripts/check.sh              # migrations from zero, the test suite, the dashboard JavaScript (CI runs the same)
scripts/smoke_live.py         # optional: end-to-end check of the running stack (asks for an admin token, leaves nothing behind)
```

`scripts/check.sh --no-db` runs the unit tests only. Everything must work on macOS, Linux and Windows: new tooling is written in Python, not bash
(`scripts/check.sh` just starts `scripts/check.py`; on Windows run `python scripts/check.py`). A new feature needs a test; a bug fix should come with the test that
would have caught it. Keep the Python style of the surrounding code and keep the dashboard free of external requests and
`innerHTML` (everything from the API is data, shown as text).

## Sending a change

1. Branch from `main` (`git checkout -b fix/short-name`) and open a pull request against `main`.
2. CI must pass, and the maintainer reviews every pull request (one approval is required; `main` is protected).
3. Describe what changed and why in the pull request; fill in the template's checklist.

## How changes flow

This repository is generated from the maintainer's private upstream, which is where development happens. When your pull
request is merged here, the maintainer imports it upstream with your authorship kept, so the next sync keeps your change.
So please do not force-push `main`, and do not change `LICENSE`, `.github/CODEOWNERS` or `.gitignore` in a pull request:
those are written by the export. Ideas that need a conversation first belong in an issue.

## Licence

By contributing you agree that your contribution is licensed under the MIT License of this repository.

## Security

Report vulnerabilities privately, as described in `SECURITY.md`.
