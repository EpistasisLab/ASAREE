"""Statistics for automatic experiment reports.

Published packages first: ``analysis`` fits models with statsmodels and
scikit-learn, ``covariance`` builds verified HC3 sandwich covariances, and
``resampling`` runs bootstrap intervals and interaction null resampling.

``custom`` covers the models no published package implements (zero/one-inflated
beta, hurdle Gamma, beta-binomial) with the likelihood engine from the original
statistics package: ``engine``, ``families``, ``inference``, ``contrasts``,
``design``, ``diagnostics`` and ``nonparametric`` (design-preserving
permutation tests, also used for every report's main-effect checks).
"""
