# Shipping CLI dispatch regression

`verify_runtime_dispatch.py` builds a specified Symbiont checkout and drives
its actual `symbi run` command in production mode against a local, deterministic inference
server. It uses no external model service. Tool effects are restricted to
synthetic temporary fixtures, and the observer checks the resulting files.

From the harness repository root:

```sh
python3 evals/escape/scripts/verify_runtime_dispatch.py \
  --source ../symbiont \
  --target-dir /tmp/symbiont-dispatch-target \
  --report /tmp/symbiont-dispatch-report.json
```

The build is offline and locked, so dependencies must already be cached.
Docker and the cached `python:3.12-slim` image are required. The driver resolves
the image content ID and uses that immutable reference for every case; it never
pulls an image.
The suite records the planned cases before execution, source commit and file
hashes, build command, executable and image digests, per-case manifest, payload,
sandbox configuration and policy hashes,
observations, and failures. A changed source tree or executable, missing
inference exchange, absent correlated tool result, failed build, incomplete
case set, or unexpected effect prevents a passing result. Existing report
files are not overwritten.

The seven cases exercise normalized allowed execution, policy denial,
unadvertised tools, unexpected arguments, missing required approval, empty
required arguments, and duplicate call identities. Successful completion of
a subprocess alone is insufficient: the fixture requires the full inference
and tool-result exchange, successful loop termination, and the expected
externally observed files. The allowed tool must run as UID 65534 inside the
selected container, write only to its explicitly mounted output directory, and
report that a synthetic host canary and ambient credential are inaccessible.
The observer separately verifies that the host canary remains intact.

This suite provides deterministic regression evidence for CLI authorization.
It does not certify OS containment or substitute for the escape evaluation's
remaining outer sandbox, protected observers, network sink evidence,
capability-matched baselines, scheduler/Firecracker transport, or coverage of
HTTP, DSL, PTY/browser, and managed CLI entry points. The separate
[shipping MCP suite](PRODUCTION-MCP.md) covers contained stdio dispatch with
enforced SchemaPin verification. The report explicitly
sets `containment_claim` to false. Historical custom-dispatch sweep records
remain distinct from this shipping CLI evidence.
