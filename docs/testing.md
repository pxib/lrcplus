Run the routine developer suite with:

```powershell
python -m pytest -q
```

Rendering tests marked `render_matrix` retain every parameter value and every
pair of parameter values. Their selection is deterministic. Tests marked
`exhaustive` cover expensive full encounter simulations and additional real pip
install scenarios. The routine suite still exercises a real dependency
installation, shared dependency removal, and import checks.

Run every original rendering combination and all expensive checks with:

```powershell
python -m pytest -q --exhaustive
```

CI uses the exhaustive command for pull requests and pushes to `release`.
Superseded runs on the same ref are cancelled. Feature branches with a pull
request are tested by the pull request event.

For a focused change, pass a test file or node ID. A directly selected rendering
case runs even when it is outside the routine matrix. Directly selecting a test
marked `exhaustive` also requires `--exhaustive`.

```powershell
python -m pytest -q tests/test_queue.py
python -m pytest -q --exhaustive tests/test_dependencies.py
```

The shared test fixtures flush Qt's deferred deletion events after every test.
GUI fixtures must still close their windows, stop their workers, and call
`deleteLater()`; `processEvents()` alone does not deliver deferred deletions when
no Qt event loop is running. This prevents retained UI trees from slowing later
tests without reusing application state between cases.
