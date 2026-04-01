from .state import ClaimWorkflowState

__all__ = ["compile_claims_graph", "ClaimWorkflowState"]


def __getattr__(name: str):
    if name == "compile_claims_graph":
        from .claims_workflow import compile_claims_graph

        return compile_claims_graph
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
