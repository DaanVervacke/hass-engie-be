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
```

All three scripts use the `.venv` created by `scripts/setup`. You do
not need the devcontainer to run tests or linting.

### Devcontainer (live Home Assistant)

A devcontainer is included for running a live Home Assistant instance
with the integration loaded. Open the repo in VS Code (or any
devcontainer-compatible editor) and it will start a stand-alone HA
configured with the included
[`configuration.yaml`](./config/configuration.yaml) file. Use this for
manual testing and UI verification, not for running the test suite.

## License

By contributing, you agree that your contributions will be licensed under its MIT License.
