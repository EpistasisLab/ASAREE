# Interaction sensitivity — 44 interaction terms

Not prespecified; not a decision rule. Markdown twin of `interaction_sensitivity.csv`, split across two tables keyed by Holm rank. Full write-up: `INTERACTION_SENSITIVITY.md`.

### Estimates

| rank | endpoint | term | order | link contrast (95% CI) | mult | exp(contrast) (95% CI) | raw p |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | wallclock_min | model:critic | two-way | 0.384 (0.21, 0.559) | x4 | 1.47 (1.23, 1.75) | 1.60e-05 |
| 2 | total_tokens | model:critic | two-way | 0.327 (0.167, 0.486) | x4 | 1.39 (1.18, 1.63) | 5.85e-05 |
| 3 | usd | model:critic | two-way | 0.359 (0.18, 0.537) | x4 | 1.43 (1.2, 1.71) | 7.94e-05 |
| 4 | n_features_after_fs | model:critic | two-way | 0.297 (0.103, 0.49) | x4 | 1.35 (1.11, 1.63) | 2.63e-03 |
| 5 | prop_engineered_selected | model:effort | two-way | -0.281 (-0.47, -0.0925) | x4 | 0.755 (0.625, 0.912) | 3.49e-03 |
| 6 | prop_engineered_selected | model:critic | two-way | -0.261 (-0.45, -0.0726) | x4 | 0.77 (0.638, 0.93) | 6.64e-03 |
| 7 | n_engineered_features | model:effort | two-way | -0.142 (-0.245, -0.039) | x4 | 0.868 (0.783, 0.962) | 6.88e-03 |
| 8 | inner_cv_score | model:critic | two-way | 0.0182 (0.00419, 0.0322) | x4 | 1.02 (1, 1.03) | 1.09e-02 |
| 9 | wallclock_min | model:effort:critic | three-way | -0.427 (-0.776, -0.0776) | x8 | 0.653 (0.46, 0.925) | 1.66e-02 |
| 10 | roc_auc | model:critic | two-way | 0.0183 (0.00134, 0.0352) | x4 | 1.02 (1, 1.04) | 3.44e-02 |
| 11 | n_turns_total | effort:critic | two-way | -0.225 (-0.437, -0.0141) | x4 | 0.798 (0.646, 0.986) | 3.66e-02 |
| 12 | prop_engineered_selected | effort:critic | two-way | -0.175 (-0.363, 0.0141) | x4 | 0.84 (0.695, 1.01) | 6.98e-02 |
| 13 | total_tokens | effort:critic | two-way | -0.144 (-0.303, 0.0154) | x4 | 0.866 (0.738, 1.02) | 7.66e-02 |
| 14 | n_turns_total | model:critic | two-way | 0.173 (-0.0379, 0.385) | x4 | 1.19 (0.963, 1.47) | 1.08e-01 |
| 15 | wallclock_min | effort:critic | two-way | -0.143 (-0.317, 0.032) | x4 | 0.867 (0.728, 1.03) | 1.09e-01 |
| 16 | roc_auc | model:effort | two-way | 0.0133 (-0.00366, 0.0302) | x4 | 1.01 (0.996, 1.03) | 1.24e-01 |
| 17 | inner_cv_score | effort:critic | two-way | -0.00978 (-0.0238, 0.00423) | x4 | 0.99 (0.976, 1) | 1.71e-01 |
| 18 | n_engineered_features | model:effort:critic | three-way | -0.143 (-0.349, 0.0622) | x8 | 0.866 (0.705, 1.06) | 1.72e-01 |
| 19 | inner_cv_score | model:effort:critic | three-way | -0.0187 (-0.0467, 0.00937) | x8 | 0.982 (0.954, 1.01) | 1.92e-01 |
| 20 | n_turns_total | model:effort | two-way | -0.137 (-0.348, 0.0743) | x4 | 0.872 (0.706, 1.08) | 2.04e-01 |
| 21 | n_engineered_features_selected | effort:critic | two-way | -0.123 (-0.332, 0.0863) | x4 | 0.884 (0.717, 1.09) | 2.49e-01 |
| 22 | n_engineered_features | model:critic | two-way | 0.0596 (-0.0432, 0.162) | x4 | 1.06 (0.958, 1.18) | 2.56e-01 |
| 23 | total_tokens | model:effort | two-way | -0.0903 (-0.25, 0.0691) | x4 | 0.914 (0.779, 1.07) | 2.67e-01 |
| 24 | n_engineered_features_selected | model:effort | two-way | -0.118 (-0.327, 0.0916) | x4 | 0.889 (0.721, 1.1) | 2.70e-01 |
| 25 | usd | model:effort:critic | three-way | -0.191 (-0.547, 0.165) | x8 | 0.826 (0.578, 1.18) | 2.92e-01 |
| 26 | n_engineered_features_selected | model:critic | two-way | 0.106 (-0.103, 0.316) | x4 | 1.11 (0.902, 1.37) | 3.20e-01 |
| 27 | n_features_after_fs | model:effort | two-way | 0.0913 (-0.102, 0.285) | x4 | 1.1 (0.903, 1.33) | 3.55e-01 |
| 28 | inner_cv_score | model:effort | two-way | 0.00596 (-0.00805, 0.02) | x4 | 1.01 (0.992, 1.02) | 4.05e-01 |
| 29 | usd | effort:critic | two-way | -0.0744 (-0.252, 0.104) | x4 | 0.928 (0.777, 1.11) | 4.13e-01 |
| 30 | pr_auc | effort:critic | two-way | -0.0113 (-0.0391, 0.0165) | x4 | 0.989 (0.962, 1.02) | 4.27e-01 |
| 31 | usd | model:effort | two-way | -0.0713 (-0.249, 0.107) | x4 | 0.931 (0.779, 1.11) | 4.32e-01 |
| 32 | pr_auc | model:effort | two-way | 0.0107 (-0.0171, 0.0385) | x4 | 1.01 (0.983, 1.04) | 4.51e-01 |
| 33 | prop_engineered_selected | model:effort:critic | three-way | 0.137 (-0.24, 0.515) | x8 | 1.15 (0.787, 1.67) | 4.76e-01 |
| 34 | n_features_after_fs | model:effort:critic | three-way | -0.136 (-0.523, 0.25) | x8 | 0.873 (0.593, 1.28) | 4.90e-01 |
| 35 | roc_auc | model:effort:critic | three-way | -0.0115 (-0.0454, 0.0224) | x8 | 0.989 (0.956, 1.02) | 5.05e-01 |
| 36 | roc_auc | effort:critic | two-way | -0.00571 (-0.0227, 0.0112) | x4 | 0.994 (0.978, 1.01) | 5.09e-01 |
| 37 | pr_auc | model:effort:critic | three-way | -0.0171 (-0.0728, 0.0385) | x8 | 0.983 (0.93, 1.04) | 5.47e-01 |
| 38 | n_engineered_features | effort:critic | two-way | -0.0294 (-0.132, 0.0735) | x4 | 0.971 (0.876, 1.08) | 5.76e-01 |
| 39 | n_turns_total | model:effort:critic | three-way | 0.0675 (-0.355, 0.49) | x8 | 1.07 (0.701, 1.63) | 7.54e-01 |
| 40 | total_tokens | model:effort:critic | three-way | -0.0453 (-0.364, 0.274) | x8 | 0.956 (0.695, 1.31) | 7.81e-01 |
| 41 | n_features_after_fs | effort:critic | two-way | -0.00746 (-0.201, 0.186) | x4 | 0.993 (0.818, 1.2) | 9.40e-01 |
| 42 | n_engineered_features_selected | model:effort:critic | three-way | -0.0161 (-0.435, 0.403) | x8 | 0.984 (0.647, 1.5) | 9.40e-01 |
| 43 | wallclock_min | model:effort | two-way | -0.00365 (-0.178, 0.171) | x4 | 0.996 (0.837, 1.19) | 9.67e-01 |
| 44 | pr_auc | model:critic | two-way | -0.000354 (-0.0282, 0.0275) | x4 | 1 (0.972, 1.03) | 9.80e-01 |

### Multiplicity

| rank | endpoint | term | raw p | global threshold | global p_holm | within-endpoint p_holm | survives global |
|---:|---|---|---:|---:|---:|---:|---|
| 1 | wallclock_min | model:critic | 1.60e-05 | 1.14e-03 | 7.05e-04 | 6.41e-05 | **yes** |
| 2 | total_tokens | model:critic | 5.85e-05 | 1.16e-03 | 2.51e-03 | 2.34e-04 | **yes** |
| 3 | usd | model:critic | 7.94e-05 | 1.19e-03 | 3.33e-03 | 3.17e-04 | **yes** |
| 4 | n_features_after_fs | model:critic | 2.63e-03 | 1.22e-03 | 1.08e-01 | 1.05e-02 | no |
| 5 | prop_engineered_selected | model:effort | 3.49e-03 | 1.25e-03 | 1.40e-01 | 1.40e-02 | no |
| 6 | prop_engineered_selected | model:critic | 6.64e-03 | 1.28e-03 | 2.59e-01 | 1.99e-02 | no |
| 7 | n_engineered_features | model:effort | 6.88e-03 | 1.32e-03 | 2.61e-01 | 2.75e-02 | no |
| 8 | inner_cv_score | model:critic | 1.09e-02 | 1.35e-03 | 4.03e-01 | 4.36e-02 | no |
| 9 | wallclock_min | model:effort:critic | 1.66e-02 | 1.39e-03 | 5.97e-01 | 4.97e-02 | no |
| 10 | roc_auc | model:critic | 3.44e-02 | 1.43e-03 | 1.00e+00 | 1.38e-01 | no |
| 11 | n_turns_total | effort:critic | 3.66e-02 | 1.47e-03 | 1.00e+00 | 1.46e-01 | no |
| 12 | prop_engineered_selected | effort:critic | 6.98e-02 | 1.52e-03 | 1.00e+00 | 1.40e-01 | no |
| 13 | total_tokens | effort:critic | 7.66e-02 | 1.56e-03 | 1.00e+00 | 2.30e-01 | no |
| 14 | n_turns_total | model:critic | 1.08e-01 | 1.61e-03 | 1.00e+00 | 3.23e-01 | no |
| 15 | wallclock_min | effort:critic | 1.09e-01 | 1.67e-03 | 1.00e+00 | 2.19e-01 | no |
| 16 | roc_auc | model:effort | 1.24e-01 | 1.72e-03 | 1.00e+00 | 3.73e-01 | no |
| 17 | inner_cv_score | effort:critic | 1.71e-01 | 1.79e-03 | 1.00e+00 | 5.14e-01 | no |
| 18 | n_engineered_features | model:effort:critic | 1.72e-01 | 1.85e-03 | 1.00e+00 | 5.15e-01 | no |
| 19 | inner_cv_score | model:effort:critic | 1.92e-01 | 1.92e-03 | 1.00e+00 | 5.14e-01 | no |
| 20 | n_turns_total | model:effort | 2.04e-01 | 2.00e-03 | 1.00e+00 | 4.07e-01 | no |
| 21 | n_engineered_features_selected | effort:critic | 2.49e-01 | 2.08e-03 | 1.00e+00 | 9.97e-01 | no |
| 22 | n_engineered_features | model:critic | 2.56e-01 | 2.17e-03 | 1.00e+00 | 5.15e-01 | no |
| 23 | total_tokens | model:effort | 2.67e-01 | 2.27e-03 | 1.00e+00 | 5.34e-01 | no |
| 24 | n_engineered_features_selected | model:effort | 2.70e-01 | 2.38e-03 | 1.00e+00 | 9.97e-01 | no |
| 25 | usd | model:effort:critic | 2.92e-01 | 2.50e-03 | 1.00e+00 | 8.77e-01 | no |
| 26 | n_engineered_features_selected | model:critic | 3.20e-01 | 2.63e-03 | 1.00e+00 | 9.97e-01 | no |
| 27 | n_features_after_fs | model:effort | 3.55e-01 | 2.78e-03 | 1.00e+00 | 1.00e+00 | no |
| 28 | inner_cv_score | model:effort | 4.05e-01 | 2.94e-03 | 1.00e+00 | 5.14e-01 | no |
| 29 | usd | effort:critic | 4.13e-01 | 3.13e-03 | 1.00e+00 | 8.77e-01 | no |
| 30 | pr_auc | effort:critic | 4.27e-01 | 3.33e-03 | 1.00e+00 | 1.00e+00 | no |
| 31 | usd | model:effort | 4.32e-01 | 3.57e-03 | 1.00e+00 | 8.77e-01 | no |
| 32 | pr_auc | model:effort | 4.51e-01 | 3.85e-03 | 1.00e+00 | 1.00e+00 | no |
| 33 | prop_engineered_selected | model:effort:critic | 4.76e-01 | 4.17e-03 | 1.00e+00 | 4.76e-01 | no |
| 34 | n_features_after_fs | model:effort:critic | 4.90e-01 | 4.55e-03 | 1.00e+00 | 1.00e+00 | no |
| 35 | roc_auc | model:effort:critic | 5.05e-01 | 5.00e-03 | 1.00e+00 | 1.00e+00 | no |
| 36 | roc_auc | effort:critic | 5.09e-01 | 5.56e-03 | 1.00e+00 | 1.00e+00 | no |
| 37 | pr_auc | model:effort:critic | 5.47e-01 | 6.25e-03 | 1.00e+00 | 1.00e+00 | no |
| 38 | n_engineered_features | effort:critic | 5.76e-01 | 7.14e-03 | 1.00e+00 | 5.76e-01 | no |
| 39 | n_turns_total | model:effort:critic | 7.54e-01 | 8.33e-03 | 1.00e+00 | 7.54e-01 | no |
| 40 | total_tokens | model:effort:critic | 7.81e-01 | 1.00e-02 | 1.00e+00 | 7.81e-01 | no |
| 41 | n_features_after_fs | effort:critic | 9.40e-01 | 1.25e-02 | 1.00e+00 | 1.00e+00 | no |
| 42 | n_engineered_features_selected | model:effort:critic | 9.40e-01 | 1.67e-02 | 1.00e+00 | 9.97e-01 | no |
| 43 | wallclock_min | model:effort | 9.67e-01 | 2.50e-02 | 1.00e+00 | 9.67e-01 | no |
| 44 | pr_auc | model:critic | 9.80e-01 | 5.00e-02 | 1.00e+00 | 1.00e+00 | no |
