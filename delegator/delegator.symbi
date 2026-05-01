// Delegator agent — profile of one (task routing).
//
// The delegator picks which of the registered benchmark tasks a worker
// agent should run next. It can ONLY call `choose_task`; Cedar enforces
// the same at the policy layer (`policies/delegator.cedar`) and the
// harness wires a `DelegatorActionExecutor` that refuses every other
// tool name.
//
// The separation — delegator selects, task agent executes, reflector
// records — scales the v1/v2 two-principal sandbox to N principals
// without weakening any boundary. Each principal sees only the tool
// surface it needs; a policy relaxation on one cannot reach another.
//
// Added in v6 to demonstrate the safety story at N > 2.

metadata {
    version "0.1.0"
    author "symbiont-orga-demo"
    description "Selects which benchmark task should be executed next. No other capability."
}

with {
    sandbox docker
    timeout 15.seconds
}

capabilities {
    tool "choose_task"
}
