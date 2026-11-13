from collections.abc import Iterable, Mapping
from typing import Any


def valid_dataset_parameters(**kwargs: Any) -> bool:
    return len(kwargs) != 0


def raise_for_mixed_providers(
    granules: Iterable[Mapping[str, Any]],
    context: str,
) -> None:
    """Raise when granules come from more than one CMR provider.

    Some access paths resolve credentials or object stores from the first
    granule only, so mixing providers in one call cannot work there.

    Parameters:
        granules: Granule records (``DataGranule`` instances).
        context: Phrase naming the unsupported operation, completing the
            sentence "... are not supported <context>."

    Raises:
        ValueError: The granules have more than one ``provider-id``.
    """
    providers = {granule["meta"].get("provider-id") for granule in granules}
    if len(providers) > 1:
        names = ", ".join(sorted(str(provider) for provider in providers))
        msg = (
            f"Granules from multiple providers ({names}) are not supported "
            f"{context}. Call once per provider."
        )
        raise ValueError(msg)
