#!/usr/bin/env bash
# run-harness-improvements.sh — Run TaskFleet tasks to improve the World-Office test harness
#
# This script configures and runs the wo-orchestrator to execute the harness
# improvement tasks defined in tasks-harness.json.
#
# Usage:
#   run-harness-improvements.sh              # Run all harness improvement tasks
#   run-harness-improvements.sh --status     # Show status of harness tasks
#   run-harness-improvements.sh --task TH-001 # Run specific harness task
#   run-harness-improvements.sh --dry-run     # Show dispatch plan
#
# The harness improvement tasks implement the 14 improvements identified:
#   TH-001: Unified test orchestrator
#   TH-002: Test task generation from source
#   TH-003: Harness graph CI integration
#   TH-004: Test parallelization
#   TH-005: Coverage reporting
#   TH-006: Test impact analysis
#   TH-007: Mutation testing
#   TH-008: Visual regression testing
#   TH-009: Agent evaluation harness
#   TH-010: Performance/load testing
#   TH-011: HTML/JSON test reports
#   TH-012: Harness graph integration into test selection
#   TH-013: Unified CI/CD pipeline
#   TH-014: Documentation
#
# Author: World-Office Team
# License: AGPL-3.0-or-later

set -uo pipefail

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR_DIR="$SCRIPT_DIR"

# The harness moved out of the server repo into wo-test-harness (Q2 2026).
# Harness-improvement tasks (TH-*) act on the harness repo; resolve it via
# WO_TEST_HARNESS env or a sibling `wo-test-harness` checkout.
if [[ -n "${WO_TEST_HARNESS:-}" ]]; then
  REPO_DIR="$WO_TEST_HARNESS"
elif [[ -d "$SCRIPT_DIR/../../wo-test-harness/.git" ]]; then
  REPO_DIR="$(cd "$SCRIPT_DIR/../../wo-test-harness" && pwd)"
else
  REPO_DIR="$(cd "$ORCHESTRATOR_DIR/../.." && pwd)"  # legacy: embedded repo
fi

# Harness improvement tasks configuration
HARNESS_TASKS_DIR="$SCRIPT_DIR/config"
HARNESS_TASKS_FILE="$HARNESS_TASKS_DIR/tasks-harness.json"
HARNESS_CONFIG_DIR="$SCRIPT_DIR/config-harness"
HARNESS_STATE_DIR="$SCRIPT_DIR/state-harness"
HARNESS_WORKTREE_ROOT="$REPO_DIR/.wo-harness-worktrees"

# -----------------------------------------------------------------------------
# Setup
# -----------------------------------------------------------------------------

# Ensure directories exist
mkdir -p "$HARNESS_CONFIG_DIR" "$HARNESS_STATE_DIR" "$HARNESS_WORKTREE_ROOT"

# Create symlink to harness tasks as tasks.json in harness config
if [[ ! -f "$HARNESS_CONFIG_DIR/tasks.json" ]]; then
  ln -sf "$HARNESS_TASKS_FILE" "$HARNESS_CONFIG_DIR/tasks.json"
fi

# Copy workers.json from main config if it doesn't exist
if [[ ! -f "$HARNESS_CONFIG_DIR/workers.json" ]]; then
  cp "$HARNESS_TASKS_DIR/workers.json" "$HARNESS_CONFIG_DIR/workers.json" 2>/dev/null || \
    cp "$SCRIPT_DIR/config/workers.json" "$HARNESS_CONFIG_DIR/workers.json" 2>/dev/null || \
    cp "$REPO_DIR/scripts/wo-orchestrator/config/workers.json" "$HARNESS_CONFIG_DIR/workers.json"
fi

# -----------------------------------------------------------------------------
# Run orchestrator with harness tasks
# -----------------------------------------------------------------------------

export TF_REPO_DIR="$REPO_DIR"
export TF_CONFIG_DIR="$HARNESS_CONFIG_DIR"
export TF_STATE_DIR="$HARNESS_STATE_DIR"
export TF_WORKTREE_ROOT="$HARNESS_WORKTREE_ROOT"
export TF_BRANCH_PREFIX="harness-improvement"

echo "Running World-Office Test Harness Improvements"
echo "=============================================="
echo ""
echo "Tasks: $HARNESS_TASKS_FILE"
echo "Config: $HARNESS_CONFIG_DIR"
echo "State: $HARNESS_STATE_DIR"
echo "Worktrees: $HARNESS_WORKTREE_ROOT"
echo ""

# Pass all arguments to orchestrator
cd "$ORCHESTRATOR_DIR"
exec bash orchestrator.sh "$@"
