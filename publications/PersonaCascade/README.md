# Persona Cascade Experiment

Generator→Critic cascade testing OCEAN personality traits on BixBench clinical bias questions.

## Files

| File | Description |
| --- | --- |
| `persona_cascade_experiment.json` | The experiment protocol |
| `results/` | Exported results (CSV, analysis) |

## Design

- **Dataset**: BixBench - 205 clinical bias questions
- **Design**: 5×5 factorial (Generator persona × Critic persona)
- **Factors**:
  - Generator persona: Baseline, Analyst, Explorer, Critic, Pushover
  - Critic persona: Baseline, Analyst, Explorer, Critic, Pushover
- **Cells**: 25 (all persona combinations)
- **Replicates**: 3 per cell
- **Rows**: 205 (per-row execution)
- **Total executions**: 15,375 (25 × 3 × 205)

## Goal

Evaluate how different persona combinations affect:
- Accuracy (comparing Critic.answer to ground truth TRUE/FALSE)
- Reasoning quality
- Error detection (does Critic catch Generator's mistakes?)
- Persona-specific biases

## Usage

Import the experiment JSON into ASAREE and run all cells.

Export results to `results/` after completion.
