"""MapVerity: field-level assurance for data pipelines.

Validate that every field was transformed correctly from source to target, measure how strong
the tests are, and run the riskiest checks first.

    from mapverity import Spec, validate, spec_from_mapping, mutation_test
"""

__version__ = "0.1.0"

from .core.engine import ValidationResult, load_data, validate  # noqa: E402
from .core.gates import Decision, GatePolicy, evaluate_gates  # noqa: E402
from .core.spec import CheckSpec, DatasetSpec, Spec  # noqa: E402
from .validation.checks import REGISTRY, CheckOutcome, Context, register  # noqa: E402
from .validation.cobol import Layout, read_cobol  # noqa: E402
from .validation.documents import flatten_documents  # noqa: E402
from .validation.fixedwidth import read_fixed_width  # noqa: E402
from .validation.infer import InferOptions, infer_spec  # noqa: E402
from .validation.mapping import load_mapping, mapping_coverage, spec_from_mapping  # noqa: E402
from .validation.mutate import OPERATORS, mutation_test  # noqa: E402
from .validation.openapi import spec_from_openapi  # noqa: E402

__all__ = [
    "__version__",
    "Spec",
    "CheckSpec",
    "DatasetSpec",
    "validate",
    "load_data",
    "ValidationResult",
    "GatePolicy",
    "Decision",
    "evaluate_gates",
    "infer_spec",
    "InferOptions",
    "spec_from_mapping",
    "load_mapping",
    "mapping_coverage",
    "spec_from_openapi",
    "mutation_test",
    "OPERATORS",
    "read_cobol",
    "Layout",
    "read_fixed_width",
    "flatten_documents",
    "register",
    "REGISTRY",
    "CheckOutcome",
    "Context",
]
