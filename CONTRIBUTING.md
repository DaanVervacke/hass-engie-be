# Contribution guidelines

Contributing to this project should be as easy and transparent as possible, whether it's:

- Reporting a bug
- Discussing the current state of the code
- Submitting a fix
- Proposing new features

## Github is used for everything

Github is used to host code, to track issues and feature requests, as well as accept pull requests.

Pull requests are the best way to propose changes to the codebase.

1. Fork the repo and create your branch from `main`.
2. If you've changed something, update the documentation.
3. Make sure your code lints (using `scripts/lint`).
4. Test your contribution.
5. Issue that pull request!

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.

## Report bugs using Github's [issues](../../issues)

GitHub issues are used to track public bugs.
Report a bug by [opening a new issue](../../issues/new/choose) - it's that easy!

## Write bug reports with detail, background, and sample code

**Great Bug Reports** tend to have:

- A quick summary and/or background
- Steps to reproduce
  - Be specific!
  - Give sample code if you can.
- What you expected would happen
- What actually happens
- Notes (possibly including why you think this might be happening, or stuff you tried that didn't work)

People *love* thorough bug reports. I'm not even kidding.

## Use a Consistent Coding Style

Use [ruff](https://github.com/astral-sh/ruff) to make sure the code follows the style (run `scripts/lint`).

## Test your code modification

### Local venv (tests and linting)

Set up a local Python virtual environment and run the test suite:

```bash
./scripts/setup       # creates .venv and installs dependencies
./scripts/lint        # ruff check + format check
./scripts/test        # pytest with coverage (target: 95%+)
./scripts/typecheck   # mypy baseline (informational, does not gate)
```

All scripts use the `.venv` created by `scripts/setup`. You do
not need the live Home Assistant container to run tests or linting.

Optionally install the git pre-commit hooks so ruff runs on every commit:

    .venv/bin/pip install -r requirements.txt
    .venv/bin/pre-commit install

The hooks call the venv ruff, so they always match the pinned version.
CI enforces the same checks either way.

`./scripts/test` also checks the Bruno API collection in `.bruno/` against the
HTTP client. If you add a method to `api.py`, add a matching request under
`.bruno/` or the build fails. See `.bruno/README.md`.

### Running a live Home Assistant

```bash
./scripts/restart-dev-container.sh
```

The script pulls the Home Assistant version pinned in `requirements.txt`
and starts a container on [http://localhost:8123](http://localhost:8123),
with the integration and this repo's automation blueprints mounted into
it. Home Assistant keeps its own state in the gitignored `dev-config/`
directory, which the script creates on first run, so config entries and
history survive a restart.

Configure the integration through the Home Assistant UI. Use this
container for manual testing and UI checks, not for the test suite.

The script needs [podman](https://podman.io/). Re-running it is safe. It
stops and removes any existing container first.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
