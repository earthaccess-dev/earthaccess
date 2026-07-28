from typing import Any


def valid_dataset_parameters(**kwargs: Any) -> bool:  # noqa: ANN401
    return len(kwargs) != 0
