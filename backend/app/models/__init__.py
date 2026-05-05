# Lazy imports to avoid pulling in geoalchemy2/PostGIS deps in dev mode.
# The DB models (Feature, Channel, AnalysisJob) are only needed when
# running with a real database. Pydantic models (AgentResult, ScoredCell)
# are always available via direct import.


def __getattr__(name):
    if name == "Feature":
        from app.models.feature import Feature
        return Feature
    elif name == "Channel":
        from app.models.channel import Channel
        return Channel
    elif name == "AnalysisJob":
        from app.models.analysis_job import AnalysisJob
        return AnalysisJob
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Feature", "Channel", "AnalysisJob"]
