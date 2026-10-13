# Adapter authoring guide

This guide describes the LOCALFORGE adapter contract and the evidence needed before an adapter is
presented as supported. See the [connector support matrix](connector-support-matrix.md) for the
current catalog, connection modes, host OS declarations, fixture evidence, documentation links,
and known limits.

## Before adding an adapter

1. Read the relevant first-party API, metrics, platform, and release documentation. Record direct
   documentation links in `AdapterManifest.documentation_url` and the support matrix.
2. Choose the integration mode that matches what the code actually does:
   - `attached_endpoint`: sends requests to an endpoint the user already started. The selected
     profile records whether that endpoint is local or remote; remote egress requires the app's
     confirmation flow.
   - `managed_process`: LOCALFORGE starts and supervises only a validated local process it owns.
   - `library_pipeline`: LOCALFORGE calls an already-installed runtime in a separate bounded worker
     process. Model paths refer to files already on the local host.
   - `composed_serving_stack`: the selected target includes a router or orchestrator and one or
     more independently identified workers.
3. Add a stable `localforge.<name>` ID and a manifest with a truthful category, integration modes,
   LOCALFORGE host OS list, configuration schema, declared capabilities, limitations, and docs URL.
   An adapter must not list a mode that it does not implement. A planned catalog entry uses
   `implementation_status="planned"` and an empty `integration_modes` list; the catalog does not
   register it as callable.
4. Keep engine-specific profile values in an adapter-owned `engine_extensions` namespace. Describe
   the fields in `config_schema`. The generic profile dialog renders supported strings, numbers,
   integers, booleans, and enums from the manifest; common endpoint fields and benchmark storage
   do not need to change for a new connector.

## Capability claims

`declared_capabilities` is the upper bound on what the adapter may claim. Each observed capability
uses one state with provenance and details:

| State | Meaning |
|---|---|
| `supported` | At runtime, the adapter verified this capability from the selected engine or local pipeline. State the evidence source and any version scope. A fixture tests connector behavior; it does not prove that a real upstream release supports the capability. |
| `unsupported` | The selected target or integration verified that it cannot provide this capability. Describe the checked limitation. A synthetic fixture alone cannot establish an upstream limitation. |
| `unknown` | No safe or reliable evidence is available. Prefer this over guessing from a project name, model format, or related runtime. |
| `user_supplied` | The value came from profile input and was not verified against the target. |

Do not turn missing routes, unauthorized metadata, or incomplete topology into a negative claim or
a guessed value. Native metrics stay engine-native unless an adapter has a documented conversion
with a valid unit and formula.

## Tests and local fixtures

Implement the shared `EngineAdapter` contract and register the adapter in a catalog test. Use the
shared helper in `backend/tests/adapter_contract.py`:

```python
await assert_registered_adapter_contract(catalog, "localforge.example", fixture_config)
```

Add local-only fixtures for configuration validation, health and version parsing, model inventory,
generation and streaming, cancellation where supported, metric parsing or explicit unsupported
results, sanitized failures, and timeout behavior. A fixture may use an in-process ASGI app,
`httpx.MockTransport`, or a child-process script under a temporary test directory. Do not contact
public services, install engines, fetch model files, or require an accelerator in standard tests.
Any live endpoint, engine binary, or hardware test must be explicitly opt-in and documented as
such.

For a library pipeline, also test malformed and oversized IPC, child crash, startup and request
timeouts, cleanup, and restart behavior. The child process must not be able to corrupt the API
process or database. Keep paths and prompts out of logs, exception text, fingerprints, and default
exports unless a feature explicitly requests content retention.

## Version and release status

`version_tested` is evidence metadata, not a promise of general compatibility. Label fixture values
as fixture values, for example `1.2.3 (local API fixture)`. This means only that a local fixture
returned that synthetic value; it is not an installed or live-tested upstream release. Record a
real release only after a reproducible live test against that upstream version, and document the
tested routes, operating system, and material hardware/dependency scope. Leave the list empty when
there is no version evidence. For library pipelines, successful local probes may report installed
package metadata at runtime; fixture tests do not populate `version_tested`.

Use `implementation_status` and the integration modes to distinguish:

- **Planned:** catalog-only and unavailable; no callable adapter or active mode.
- **Attach-only:** implemented for `attached_endpoint`; LOCALFORGE does not install or launch the
  engine. A profile's local/remote classification describes the configured endpoint.
- **Managed launch:** `managed_process` is explicitly listed and tested for ownership, argument
  validation, startup, shutdown, and recovery.
- **Library pipeline:** `library_pipeline` is explicitly listed and the inference package runs in a
  bounded isolated worker.
- **Composed stack:** `composed_serving_stack` is explicitly listed; router, worker, and aggregate
  identities and metrics remain separate unless a documented formula justifies combining them.
- **Fixture-validated:** shared adapter tests pass with local fixtures. This does not mean a live
  upstream release or accelerator was tested.
- **Live release tested:** describe this separately in docs and record only the release and scope
  that were actually exercised. Never infer this status from a synthetic fixture value.

TGI's maintenance state, planned connectors, attach-only behavior, and managed launch support must
remain visible in both the manifest and support matrix. Update those statuses when implementation
or upstream maintenance changes.

## Privacy and endpoint behavior

- Make network egress explicit. Do not probe a remote endpoint until the existing LOCALFORGE
  confirmation flow authorizes the requested check.
- Never store plaintext credentials in profile data. Use the existing secret-reference mechanism.
- Redact credentials, secret query values, local model paths, prompts, and generated output from
  diagnostics, logs, fingerprints, and exports by default.
- Do not install packages, download models, or mutate the user's engine as a side effect of profile
  creation, probing, or a benchmark.
- Keep all subprocess arguments as validated argument-array elements. Use bounded timeouts, output
  limits, process cleanup, and controlled environment/working-directory behavior.
- Report `unknown` when the target does not expose a field through a safe, verified route.

## Catalog and release checklist

When adding or changing a connector:

1. Add local fixture-backed contract tests and any adapter-specific edge-case tests.
2. Add official documentation links, host OS scope, mode, fixture/live version evidence, status,
   and known limitations to `docs/connector-support-matrix.md`.
3. Ensure the generic profile form can configure every required schema field; do not add a new
   engine-specific benchmark or profile screen.
4. Run backend format, lint, type checks, and tests. Run frontend format, lint, type checks,
   tests, and build if catalog, manifest, or profile UI code changed.
5. Run the full catalog-to-matrix coverage test. It verifies every intended adapter ID and compares
   status, modes, host OS list, version evidence, and documentation URL with the live manifest.
6. Update the prompt completion record with scope, limitations, exact checks, and any live-test
   evidence. Do not claim live compatibility from fixture-only checks.
