# Contributing

Thank you for improving Lariat. Keep changes focused on the tested Honcho
compatibility surface and avoid copying code from Honcho or other projects.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
make check
```

Pull requests should include tests, update documentation for public behavior,
and avoid prompts, responses, credentials, personal paths, or account data in
fixtures. Commit messages should describe the observable behavior changed.

Real ChatGPT OAuth tests are manual and must use a dedicated `CODEX_HOME`.
Automated tests must use fakes; CI must never receive a personal OAuth token.

By contributing, you agree that your contribution is licensed under Apache-2.0
and to follow the code of conduct.
