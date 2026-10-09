"""Pose estimation: canonical schema, estimator registry and adapters.

Importing this package registers the built-in estimators (adding an estimator = its adapter module
plus one import line here).
"""

from ttcoach.pose import sports2d_adapter as _sports2d_adapter  # noqa: F401  (registration)
