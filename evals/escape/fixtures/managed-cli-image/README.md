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


## VM image

With Python 3.12+, Docker, `mkfs.ext4`, an already cached image from above and a
matching static `symbi-sandbox-guest`, build a disposable 1 GiB ext4 fixture:

```sh
python fixtures/managed-cli-image/build_vm_rootfs.py \
  --image symbi-managed-real-e2e:local \
  --guest-binary /path/to/static/symbi-sandbox-guest \
  --output /path/to/managed-rootfs.ext4
```

The builder exports a stopped container and removes it afterward. It does not
pull images, start a CLI or mount host storage. It filters archive extraction,
rewrites absolute guest symlinks as equivalent relative links, removes inherited
resolver/host identity, installs the supplied guest and records the image content
ID plus guest, CLI, Python and rootfs hashes. Existing outputs are refused.
`--program NAME=/path/to/static/program` optionally installs the runtime's
`mcp_fixture` and `pty_fixture` examples for focused stream regressions. Failed
build output is not a usable artifact without its completed provenance record.

The kernel and guest implementation must match the selected runtime. Provision
all CLI libraries in the base image; this is not a production image build system.
The guest has loopback and private broker channels, without a NIC. The CLI and
tool guests use the same read-only image, so its readable files are not private
backend inputs. The shipping verifier uses synthetic guest scratch data and does
not require host source mounts.
