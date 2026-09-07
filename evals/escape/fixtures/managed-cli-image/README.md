# Native CLI fixture image

Supply a locally installed native Claude Code binary compatible with the Docker
host architecture. Copy it into a temporary build context as `claude`, then build
using this Dockerfile, for example:

```sh
docker build -t symbi-managed-real-e2e:local -f evals/escape/fixtures/managed-cli-image/Dockerfile /path/to/temporary-context
```

The Dockerfile provisions Python and Git and copies the supplied binary. It does
not contain credentials or download a CLI. Do not add the binary to the repository.
The base image is pinned; package mirror contents may change, so record the final
image content ID and the supplied CLI hash/version with each evaluation.

Run the suite from `evals/escape` with Python 3 and OpenSSL available:

```sh
python scripts/verify_runtime_managed_cli.py --source /path/to/symbiont --target-dir /tmp/symbiont-e2e-target --image symbi-managed-real-e2e:local --report /tmp/managed-cli-e2e.json
```

The suite builds the exact shipping runtime offline before testing. Inference is
scripted locally; any token/cost counters emitted by the native CLI are calculated
from synthetic responses and do not represent paid provider usage. Host-only
synthetic credentials must not appear in worker output or audit records. The
suite keeps missing/failed trials invalid and records immutable source, build,
image, policy, manifest and proposal identities.

A runtime process killed mid-request leaves an incomplete signed journal and can
leave an immutable channel directory until runtime-owned storage is cleaned up.
The fixture removes its own storage only after container and lease cleanup has
been checked. An incomplete journal is never accepted as successful completion.
