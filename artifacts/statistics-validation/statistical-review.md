# Statistical review handoff

Awaiting human statistical review; no LLM or agent is part of the statistical pipeline.

**Experiment:** `2c9787f3-2bbd-4329-9235-ea718e3d1a62`

**Input status:** Awaiting exported saved requests; intended factor structure has not been confirmed.

Existing policy defaults are exposed and documented. A new independent score acceptance threshold is optional and unconfigured until reviewed. Unsupported dependence structures are explicitly withheld.

## 1. Intended design and experimental unit

Are Effort and Feature budget the full intended factor set? Which other settings are constants, treatments, covariates or justified exclusions? Are runs independent, or are there subjects, shared datasets, paired seeds, blocking, clustering or repeated measures requiring a different model?

**Current rule:** Automatically include all observed varying factors unless a factor list is supplied; account for declared/unobserved/constant/excluded factors. Independent whole-dataset runs only. One to six factors, up to 128 analyzed level combinations; rank-deficient designs withheld. Blocks, covariates and dependence models are not implemented in this automatic path.

## 2. Estimands, interactions and incomplete designs

Which main effects and interactions are prespecified? Should comparisons weight all combinations of levels equally or use another target population? Is prediction into unobserved combinations acceptable? For higher-order models, should term tests remove only the named interaction, or enforce hierarchical term removal?

**Current rule:** Sum coding; all interactions through configured max_order (default 2, maximum 3); response-scale pairwise differences/ratios and two-factor contrasts; equal weighting across the full prediction grid. Term-reduced null simulations remove only the tested term.

## 3. Model selection and dispersion

Approve the measurement-driven candidate families, diagnostic exclusions and minimum-AIC selection rule, or supply replacements. Is conditional post-selection inference acceptable, and what dispersion structure is required for beta, count, Gamma and multipart outcomes?

**Current rule:** Comparable response-scale AIC on the same runs, counting all estimated parameters. Selected family stays fixed during resampling. Intervals omit selection uncertainty. Beta precision is intercept-only; native NB2 alpha and Gamma shape are estimated; multipart models use their recorded block structure.

## 4. Covariance and finite-sample reference distributions

Approve each family's sandwich construction, normal/chi-square reference distributions and finite-sample behavior. For native beta, ordinal and zero-inflated models, is the explicit HC0 fallback acceptable, or should inference be withheld until a reviewed alternative is implemented?

**Current rule:** Explicit HC3 for OLS, supported GLMs and NB2; HC0 for native beta/ordinal/zero-inflated models by default, with configurable withholding. Custom models use their documented generalized HC3. Numerical covariance verification is not calibration validation.

## 5. Fit acceptance and diagnostic gates

Specify a reviewed score normalization and tolerance, optimizer retry sequence, iteration limits, identification/conditioning criteria and boundary/degeneracy gates for initial fits and refits. Should distributional diagnostic exclusions also be repeated in each resample?

**Current rule:** Native MLE followed by L-BFGS then Newton when acceptance fails, max 500 iterations per optimizer. Finite estimates/likelihood and positive information/model covariance checked. Independent max-absolute-score/n gate is optional and currently unconfigured. Legacy gates include absolute parameters 30, probability boundary 1e-8, NB2 alpha 1e-6, Poisson/binomial Pearson dispersion 3, minimum 3 runs per observed condition, and severe Gaussian skewness checks. Custom optimizer criteria are reported separately. Passing these gates does not establish a global optimum.

## 6. Resampling assumptions, failure handling and disagreements

Under what recorded randomization/exchangeability assumptions may label permutation run? Approve or replace the Freedman–Lane scale choices and Brown–Forsythe gate, null-model simulation assumptions, case versus parametric interval bootstrap, dispersion correction and failed-refit policy. Which fixed rule should govern analytical/resampling disagreements?

**Current rule:** Label permutation within strata tests a sharp distributional null, potentially different from the analytic test. Supported interactions use residual permutation or term-reduced parametric bootstrap. Exchangeability gate alpha 0.01 is only a diagnostic, not proof. Default minimum successful fraction is 0.9; failures are excluded and counted, completed draw count is required, interrupted inference withheld. Analytic adjusted p-values govern analytic conclusions; sensitivity disagreements are shown, never selected for significance. Native beta interaction null simulation remains unimplemented.

## 7. Multiplicity and unavailable tests

Which hypotheses form confirmatory families across terms, comparisons, procedures, metrics and experiment versions? How should planned but unavailable tests affect correction? Approve the significance threshold and confidence levels.

**Current rule:** Alpha 0.05, pointwise 95% intervals. Holm across available analytic factor tests; separately across available resampling tests; separately within each factor or factor-pair comparison family. No cross-metric correction. Unavailable p-values are excluded from adjustment and have no conclusion.

## 8. Validation targets, sample sizes and beta scope

Specify required null rejection control, interval coverage, power and numerical accuracy targets, designs/families to validate and simulation sample sizes. Review the separate native-beta smoke result (including the three-factor rejection count) before declaring beta inference validated. Supply the original six-report validation records so their counts, uncertainty and withheld analyses can be re-audited.

**Current rule:** Separate beta-smoke.json records three explicit null designs, 6 simulations each, seeds, configurations, rejection events and exact 95% binomial uncertainty. Counts were 0/6, 0/6 and 2/6, with no failed or withheld analyses in this run. This does not validate the three actual beta reports, establish interval coverage, or rule out false-positive rates of 10% or more.

## References

- [Exact uncertainty intervals for simulation rejection counts](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html)
- [Nuisance variables and approximate permutation inference](https://wrap.warwick.ac.uk/65670/1/WRAP_1-s2.0-S1053811914000913-main.pdf)
