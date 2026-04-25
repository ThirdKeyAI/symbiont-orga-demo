# Contributing

Thanks for considering a contribution. This is a research
demo, not a product, so the bar is "the change makes the safety
or evaluation story sharper" rather than "the change ships a
feature." If you're not sure whether something fits, open an
issue first.

## Local setup — side-by-side checkout (default)

```bash
mkdir <parent> && cd <parent>
git clone https://github.com/ThirdKeyAI/symbiont
git clone https://github.com/ThirdKeyAI/symbiont-karpathy-loop
cd symbiont-karpathy-loop
cargo build -j2 --release
```

Workspace `Cargo.toml` declares
`symbi-runtime = { path = "../symbiont/crates/runtime", … }`,
so the layout above is required. CI follows the same shape
(`.github/workflows/ci.yml` checks out both repos with the
runtime pinned to a specific SHA).

### Single-clone alternative — git dep

If you want a clone-and-build flow with no sibling checkout,
edit the workspace `Cargo.toml` to use a git-dep instead:

```toml
symbi-runtime = { git = "https://github.com/ThirdKeyAI/symbiont", rev = "cbddea16e787fbb9562a24a7cc69ed011769acb4", default-features = false, features = [
    "cloud-llm",
    "orga-adaptive",
    "cedar",
] }
```

Bump `rev` when you want a newer runtime. Don't commit this
back to main without coordinating with the maintainer — CI
expects the side-by-side layout.

## Running the demo

Mock provider, no API key required:

```bash
scripts/run-demo.sh                  # 3 iterations × 5 tasks, mock
```

Real models, requires `OPENROUTER_API_KEY` (see README quickstart):

```bash
echo "OPENROUTER_API_KEY=…"            > .env
echo "OPENROUTER_MODEL=anthropic/claude-haiku-4.5" >> .env
chmod 600 .env
scripts/run-openrouter-sweep.sh 10
```

## Pre-commit checklist

Before opening a PR:

```bash
# 1. Linter — Rust quality + invisible-character + homoglyph fences
cargo clippy -j2 --release --all-targets \
    -p demo-karpathy-loop -p symbi-kloop-bench -- -D warnings
scripts/lint-cedar-policies.py

# 2. Unit + integration tests
cargo test -j2 --release --workspace

# 3. ORGA typestate compile-fail proofs
cargo test -j2 --release -p symbi-kloop-bench --test typestate_compile_fail

# 4. Knowledge-store audit (only matters if you ran a sweep)
scripts/audit-knowledge-stores.py --strict
```

CI runs the same set on every push and PR.

## Commit messages

Conventional-ish: `feat:`, `fix:`, `docs:`, `ci:`, etc. Focus on
the *why*, not the *what*. The diff already says what changed.

## Adding a new attack-shape variant

Adversarial reflector prompts live in
`crates/symbi-kloop-bench/src/reflector.rs` as a `ReflectorPrompt`
enum. To add one:

1. Add the variant + system prompt in `reflector.rs`.
2. Wire it into `main.rs`'s `--adversarial-variant` parse.
3. Add a per-suffix branch in `scripts/run-openrouter-sweep.sh`.
4. Run a small sweep (`ITERATIONS=2 VARIANT=<your-variant> scripts/run-openrouter-sweep.sh`)
   on at least three models that historically bite (Qwen 3.6,
   gpt-oss-20b, DeepSeek). Confirm Cedar refuses every attempted
   forbidden call.
5. Document the result in the next `MODEL-SWEEP-REPORT-vN.md`.

## License

By contributing, you agree your contributions are licensed
under Apache-2.0 (`LICENSE` at the repo root).
