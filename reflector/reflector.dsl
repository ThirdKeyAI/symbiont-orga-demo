// Reflector agent — profile of one.
//
// The reflector observes the task agent's just-completed run (journal +
// score) and proposes 0–5 concrete, actionable procedures the task
// agent should remember for similar future runs. It can ONLY call
// `store_knowledge`; Cedar enforces the same at the policy layer and
// the harness wires a `ReflectorActionExecutor` that refuses every
// other tool name.
//
// This separation — reflector writes, task agent reads, neither can do
// what the other can — is what makes the loop Cedar-bounded.

metadata {
    version "0.1.0"
    author "symbiont-orga-demo"
    description "Observes one task run and records at most five procedures for the next run of the same task."
}

with {
    sandbox docker
    timeout 30.seconds
}

capabilities {
    tool "store_knowledge"
}
