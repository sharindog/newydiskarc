# Tasks

1. [ ] Update `pyproject.toml` to drop Python 3.6-3.8 and set minimum to 3.9. Migrate `setup.py` and `setup.cfg` data into `pyproject.toml` and delete them.
2. [ ] Update `.github/workflows/ci.yml` to remove EOL Python versions from the test matrix, and strictly enforce `black`, `flake8`, and `mypy` without `continue-on-error`.
3. [ ] Extract HTTP session and Yandex API call logic from `processor.py` into `client.py`.
4. [ ] Extract download streaming, retry chunking, and partial file `.part` logic into `downloader.py`.
5. [ ] Create `models.py` with dataclasses reflecting Yandex API metadata structures.
6. [ ] Wire the CLI (`core.py`) to use the new refactored classes.
7. [ ] Update existing tests to reflect the new modular architecture, ensuring `pytest` passes cleanly.
