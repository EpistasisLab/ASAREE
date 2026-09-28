# All 77 factorial terms — multiplicity in one schema

Not prespecified; not a decision rule. Markdown twin of `all_terms_multiplicity.csv`. Full write-up: `INTERACTION_SENSITIVITY.md`.

| endpoint | term | order | tier | raw p | p scale | prespecified p_holm | global interaction p_holm | status |
|---|---|---|---|---:|---|---:|---:|---|
| wallclock_min | critic | main | exploratory | 2.86e-168 | response | 8.59e-168 |  | significant (prespecified) |
| total_tokens | critic | main | exploratory | 2.13e-142 | response | 6.40e-142 |  | significant (prespecified) |
| usd | model | main | exploratory | 1.39e-139 | response | 4.17e-139 |  | significant (prespecified) |
| usd | critic | main | exploratory | 5.10e-123 | response | 1.02e-122 |  | significant (prespecified) |
| n_engineered_features | model | main | exploratory | 7.10e-113 | response | 2.13e-112 |  | significant (prespecified) |
| n_engineered_features_selected | model | main | exploratory | 1.17e-63 | response | 3.52e-63 |  | significant (prespecified) |
| wallclock_min | effort | main | exploratory | 2.62e-41 | response | 5.24e-41 |  | significant (prespecified) |
| n_turns_total | critic | main | exploratory | 1.57e-39 | response | 4.72e-39 |  | significant (prespecified) |
| wallclock_min | model | main | exploratory | 3.04e-29 | response | 3.04e-29 |  | significant (prespecified) |
| n_features_after_fs | model | main | exploratory | 3.30e-28 | response | 9.89e-28 |  | significant (prespecified) |
| prop_engineered_selected | model | main | exploratory | 2.33e-25 | response | 7.00e-25 |  | significant (prespecified) |
| total_tokens | model | main | exploratory | 4.45e-15 | response | 8.90e-15 |  | significant (prespecified) |
| n_engineered_features | effort | main | exploratory | 1.81e-11 | response | 3.61e-11 |  | significant (prespecified) |
| n_engineered_features_selected | effort | main | exploratory | 4.16e-11 | response | 8.31e-11 |  | significant (prespecified) |
| usd | effort | main | exploratory | 1.03e-08 | response | 1.03e-08 |  | significant (prespecified) |
| n_features_after_fs | effort | main | exploratory | 2.32e-08 | response | 4.65e-08 |  | significant (prespecified) |
| inner_cv_score | model | main | exploratory | 4.61e-07 | response | 1.38e-06 |  | significant (prespecified) |
| n_features_after_fs | critic | main | exploratory | 8.04e-06 | response | 8.04e-06 |  | significant (prespecified) |
| wallclock_min | model:critic | two-way | interaction | 1.60e-05 | link |  | 7.05e-04 | survives global interaction sensitivity (not a decision rule) |
| total_tokens | model:critic | two-way | interaction | 5.85e-05 | link |  | 2.51e-03 | survives global interaction sensitivity (not a decision rule) |
| usd | model:critic | two-way | interaction | 7.94e-05 | link |  | 3.33e-03 | survives global interaction sensitivity (not a decision rule) |
| inner_cv_score | effort | main | exploratory | 1.11e-03 | response | 2.22e-03 |  | significant (prespecified) |
| n_features_after_fs | model:critic | two-way | interaction | 2.63e-03 | link |  | 1.08e-01 | does not survive global interaction sensitivity |
| prop_engineered_selected | model:effort | two-way | interaction | 3.49e-03 | link |  | 1.40e-01 | does not survive global interaction sensitivity |
| n_engineered_features_selected | critic | main | exploratory | 4.37e-03 | response | 4.37e-03 |  | significant (prespecified) |
| n_turns_total | model | main | exploratory | 5.45e-03 | response | 1.09e-02 |  | significant (prespecified) |
| pr_auc | model | main | primary | 5.61e-03 | response |  |  | significant (prespecified) |
| prop_engineered_selected | model:critic | two-way | interaction | 6.64e-03 | link |  | 2.59e-01 | does not survive global interaction sensitivity |
| n_engineered_features | model:effort | two-way | interaction | 6.88e-03 | link |  | 2.61e-01 | does not survive global interaction sensitivity |
| inner_cv_score | model:critic | two-way | interaction | 1.09e-02 | link |  | 4.03e-01 | does not survive global interaction sensitivity |
| inner_cv_score | critic | main | exploratory | 1.24e-02 | response | 1.24e-02 |  | significant (prespecified) |
| total_tokens | effort | main | exploratory | 1.44e-02 | response | 1.44e-02 |  | significant (prespecified) |
| wallclock_min | model:effort:critic | three-way | interaction | 1.66e-02 | link |  | 5.97e-01 | does not survive global interaction sensitivity |
| pr_auc | effort | main | secondary | 2.18e-02 | response | 4.37e-02 |  | significant (prespecified) |
| roc_auc | model:critic | two-way | interaction | 3.44e-02 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_turns_total | effort:critic | two-way | interaction | 3.66e-02 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| roc_auc | model | main | exploratory | 4.03e-02 | response | 1.21e-01 |  | not significant (prespecified) |
| prop_engineered_selected | effort:critic | two-way | interaction | 6.98e-02 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| total_tokens | effort:critic | two-way | interaction | 7.66e-02 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_turns_total | model:critic | two-way | interaction | 1.08e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| wallclock_min | effort:critic | two-way | interaction | 1.09e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| prop_engineered_selected | effort | main | exploratory | 1.24e-01 | response | 2.48e-01 |  | not significant (prespecified) |
| roc_auc | model:effort | two-way | interaction | 1.24e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| inner_cv_score | effort:critic | two-way | interaction | 1.71e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features | model:effort:critic | three-way | interaction | 1.72e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| inner_cv_score | model:effort:critic | three-way | interaction | 1.92e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_turns_total | model:effort | two-way | interaction | 2.04e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| prop_engineered_selected | critic | main | exploratory | 2.14e-01 | response | 2.48e-01 |  | not significant (prespecified) |
| n_turns_total | effort | main | exploratory | 2.23e-01 | response | 2.23e-01 |  | not significant (prespecified) |
| n_engineered_features_selected | effort:critic | two-way | interaction | 2.49e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features | model:critic | two-way | interaction | 2.56e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features | critic | main | exploratory | 2.63e-01 | response | 2.63e-01 |  | not significant (prespecified) |
| total_tokens | model:effort | two-way | interaction | 2.67e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features_selected | model:effort | two-way | interaction | 2.70e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| usd | model:effort:critic | three-way | interaction | 2.92e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features_selected | model:critic | two-way | interaction | 3.20e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| roc_auc | critic | main | exploratory | 3.36e-01 | response | 6.72e-01 |  | not significant (prespecified) |
| n_features_after_fs | model:effort | two-way | interaction | 3.55e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| inner_cv_score | model:effort | two-way | interaction | 4.05e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| usd | effort:critic | two-way | interaction | 4.13e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| pr_auc | effort:critic | two-way | interaction | 4.27e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| usd | model:effort | two-way | interaction | 4.32e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| pr_auc | model:effort | two-way | interaction | 4.51e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| prop_engineered_selected | model:effort:critic | three-way | interaction | 4.76e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_features_after_fs | model:effort:critic | three-way | interaction | 4.90e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| roc_auc | model:effort:critic | three-way | interaction | 5.05e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| roc_auc | effort:critic | two-way | interaction | 5.09e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| pr_auc | model:effort:critic | three-way | interaction | 5.47e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features | effort:critic | two-way | interaction | 5.76e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| roc_auc | effort | main | exploratory | 7.28e-01 | response | 7.28e-01 |  | not significant (prespecified) |
| pr_auc | critic | main | secondary | 7.47e-01 | response | 7.47e-01 |  | not significant (prespecified) |
| n_turns_total | model:effort:critic | three-way | interaction | 7.54e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| total_tokens | model:effort:critic | three-way | interaction | 7.81e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_features_after_fs | effort:critic | two-way | interaction | 9.40e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| n_engineered_features_selected | model:effort:critic | three-way | interaction | 9.40e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| wallclock_min | model:effort | two-way | interaction | 9.67e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
| pr_auc | model:critic | two-way | interaction | 9.80e-01 | link |  | 1.00e+00 | does not survive global interaction sensitivity |
