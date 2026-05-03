//! Task YAML loader. Mirrors `evals/escape/schema/task.schema.json`.
//! The Rust side parses only the fields the Symbiont harness needs;
//! anything Python-only is ignored via serde's default behavior.

use anyhow::Result;
use serde::Deserialize;
use std::path::Path;

#[derive(Debug, Deserialize)]
pub struct Task {
    pub id: String,
    #[allow(dead_code)]
    pub family: String,
    pub objective: Objective,
    pub capabilities: Vec<Capability>,
    #[serde(default)]
    #[allow(dead_code)]
    pub canary: Canary,
    #[serde(default)]
    pub instance: Instance,
}

#[derive(Debug, Deserialize)]
pub struct Objective {
    pub prompt: String,
    pub success_predicate: SuccessPredicate,
}

#[derive(Debug, Deserialize)]
pub struct SuccessPredicate {
    pub kind: String,
    pub value: serde_yaml::Value,
}

#[derive(Debug, Deserialize)]
pub struct Capability {
    pub name: String,
    pub schema: serde_yaml::Value,
}

#[derive(Debug, Deserialize, Default)]
#[allow(dead_code)]
pub struct Canary {
    #[serde(default)]
    pub filesystem_paths: Vec<String>,
    #[serde(default)]
    pub filesystem_paths_glob: Vec<String>,
    #[serde(default)]
    pub network_allowed_hosts: Vec<String>,
    #[serde(default)]
    pub network_forbidden_hosts: Vec<String>,
    #[serde(default)]
    pub syscall_forbidden: Vec<String>,
    #[serde(default)]
    pub state_immutable_files: Vec<String>,
    #[serde(default)]
    pub injection_patterns: Vec<String>,
}

#[derive(Debug, Deserialize, Default)]
pub struct Instance {
    #[serde(default)]
    pub allowed_paths: Vec<String>,
    #[serde(default)]
    pub secret_paths: Vec<String>,
    #[serde(default)]
    pub allowed_csv_contents: String,
    #[serde(default)]
    pub allowed_url: String,
    #[serde(default)]
    pub allowed_response_body: String,
    #[serde(default)]
    pub input_string: String,
    #[serde(default)]
    pub policy_file: String,
    #[serde(default)]
    pub policy_initial_contents: String,
}

pub fn load(path: &Path) -> Result<Task> {
    let text = std::fs::read_to_string(path)?;
    Ok(serde_yaml::from_str(&text)?)
}
