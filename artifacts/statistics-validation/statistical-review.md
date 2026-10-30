# Statistical implementation record

Implementation record; no collaborator review or handoff is pending.

The collaborator clarified that the requirement is deterministic statistical execution through scripts. Statistical fitting, selection, diagnostics, resampling, inference and report text use explicit configured algorithms, without agents or LLM calls. Pseudorandom procedures record seeds and are reproducible for the same inputs, configuration, software versions and seeds. This clarification does not change the subsequently specified statistical algorithms or diagnostic gates.

**Experiment:** `2c9787f3-2bbd-4329-9235-ea718e3d1a62`

**Input status:** Awaiting exported saved requests to regenerate the actual experiment reports.

## Implemented decisions

1. Factor selection already propagates through fitting, comparisons and reports within the documented application limits. This does not establish that the real experiment selected every intended factor.
2. Beta HC3 uses both mean and precision scores, joint expected information including cross-information, h_i = trace(H^-1 I_i), and H^-1 sum[s_i s_i^T/(1-h_i)^2] H^-1. The estimator supports separate precision designs; the automatic model currently fits intercept-only precision.
3. Supported non-Gaussian interactions use the approved fitted-null quantile-residual permutation: randomized PIT for discrete outcomes, CDF for continuous outcomes, within-block permutation, inverse-CDF reconstruction, full refits and the same Wald statistic with plus-one correction. This is approximate and model-dependent. Null-model parametric-bootstrap Wald p-values no longer substitute for permutations.
4. Initial fits and refits require optimizer convergence, finite parameters and likelihood, supported fitted values, positive/stable information, and no configured critical diagnostic failure. Approved critical defaults are information condition number > 1e10, leverage >= 1 - 1e-8, and generalized Cook distance > 1, together with the existing residual/dispersion gates. Global and model-exact deterministic outcomes are descriptive only. Isolated constant cells are warnings, not an exclusion criterion by themselves.
5. Primary comparison conclusions use pointwise 95% fitted-model parametric-bootstrap intervals. Analytic intervals, adjusted analytic p-values and omnibus factor tests are secondary/sensitivity results. An unavailable primary interval remains unavailable; no sensitivity result replaces it. No adjusted bootstrap p-value is inferred from a confidence interval.

## Current rules and scope limitations

These notes document implemented behavior and validation limits; they are not questions assigned to the collaborator. The intended design of the actual experiment has not been verified because its saved inputs are unavailable.

### Intended design and experimental unit

Automatically include all observed varying factors unless a factor list is supplied; account for declared/unobserved/constant/excluded factors. Independent whole-dataset runs only. One to six factors, up to 128 analyzed level combinations; rank-deficient designs withheld. Blocks, covariates and dependence models are not implemented in this automatic path.

### Estimands, interactions and incomplete designs

Sum coding; all interactions through configured max_order (default 2, maximum 3); response-scale pairwise differences/ratios and two-factor contrasts; equal weighting across the full prediction grid. Term-reduced null simulations remove only the tested term.

### Model selection and dispersion

Comparable response-scale AIC on the same runs, counting all estimated parameters. Selected family stays fixed during resampling. Intervals omit selection uncertainty. Beta precision is intercept-only; native NB2 alpha and Gamma shape are estimated; multipart models use their recorded block structure.

### Covariance and finite-sample reference distributions

Joint expected-information HC3 for beta; explicit HC3 for OLS, supported GLMs and NB2; recorded generalized HC3 for custom models. HC0 calculation remains available for ordinal/zero-inflated models, but automatic inference is withheld until their required leverage/influence diagnostics are implemented. Numerical verification is distinct from inference calibration.

### Fit acceptance and diagnostic gates

Native retry order is L-BFGS then Newton, up to 500 iterations per optimizer, without retrying known invalid parameter boundaries. A successful retry must pass the gates. Information condition limit is 1e10, leverage must be below 1 - 1e-8, generalized Cook distance must not exceed 1, and existing residual/dispersion gates are retained. Custom refits repeat their configured simulation diagnostics. No leverage clipping hides unit-leverage failures. The independent score gate remains optional; optimizer convergence does not establish a global optimum.

### Resampling assumptions, failure handling and disagreements

Gaussian/lognormal interactions retain additive-residual Freedman–Lane. Supported native non-Gaussian interactions use fitted-null quantile-residual permutation; label permutation remains a separate main-effect sensitivity procedure. The automatic path explicitly withholds unsupported dependence/blocking models. Custom multipart interaction permutation remains Not run with a specific reason. Resampling records failures, seeds and requested/completed counts; the default minimum successful fraction is 0.9. Primary parametric intervals are never selected or replaced based on significance.

### Multiplicity and unavailable tests

Primary comparison intervals are pointwise 95%, not multiplicity-adjusted. Secondary analytic factor tests use Holm together, resampling tests use a separate Holm family, and analytic comparisons use per-factor/factor-pair Holm families. No cross-metric correction. Unavailable tests have no conclusion and are omitted from the existing Holm implementation.

### Validation targets, sample sizes and beta scope

beta-smoke.json records the current HC3/parametric-interval/permutation policy and per-comparison pointwise noncoverage, secondary omnibus rejection counts, uncertainty, withheld/failed analyses, software/configuration and seeds. The earlier HC0/case-bootstrap evidence is archived in beta-smoke-before-reviewed-policy.json. Small smoke studies do not establish calibration, validate actual experiment reports, or rule out false-positive rates of 10% or more.

## References

- [Exact uncertainty intervals for simulation rejection counts](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html)
- [Nuisance variables and approximate permutation inference](https://wrap.warwick.ac.uk/65670/1/WRAP_1-s2.0-S1053811914000913-main.pdf)
- [Independent beta likelihood score and expected-information reference](https://raw.githubusercontent.com/statsmodels/statsmodels/v0.14.6/statsmodels/othermod/betareg.py)
