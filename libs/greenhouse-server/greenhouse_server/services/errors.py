"""Not-found errors raised by services.

The one convention for "no such row" at the service boundary: a service raises one of these,
and each route translates it to its own 404 detail (``deps.not_found_as_404``). Subclasses of
``LookupError`` so callers that only care about "missing" can catch the base.
"""


class NotFoundError(LookupError):
    """A service was asked about a row that does not exist."""


class ClusterNotFoundError(NotFoundError):
    """No cluster has the requested id."""


class PlantNotFoundError(NotFoundError):
    """No plant has the requested id (or no cluster lists it)."""
