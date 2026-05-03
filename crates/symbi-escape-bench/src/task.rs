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
    #[allow(dead_code)]
    pub canary: Canary,
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

#[derive(Debug, Deserialize)]
#[allow(dead_code)]
pub struct Canary {
    pub filesystem_paths: Vec<String>,
    #[serde(default)]
    pub filesystem_paths_glob: Vec<String>,
}

#[derive(Debug, Deserialize)]
pub struct Instance {
    pub allowed_paths: Vec<String>,
    pub secret_paths: Vec<String>,
    pub allowed_csv_contents: String,
}

pub fn load(path: &Path) -> Result<Task> {
    let text = std::fs::read_to_string(path)?;
    Ok(serde_yaml::from_str(&text)?)
}
