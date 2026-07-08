"""
Regression guard for `_BoundaryScheduleMixin`'s type-checker contract.

`_BoundaryScheduleMixin` inherits from `CoordinatorEntity[Any]` under
`TYPE_CHECKING` only. At runtime it inherits from `object`. This test
locks both facts in so a future edit that switches the runtime base
(and quietly changes MRO for every concrete subclass) fails here first,
before it fails in production as a leaked timer or a missed state write.
"""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.engie_be.binary_sensor import (
    EngieBeEpexNegativeSensor,
    EngieBeHappyHourActiveSensor,
)
from custom_components.engie_be.entity import _BoundaryScheduleMixin


def test_mixin_runtime_base_is_object() -> None:
    """
    Mixin runtime base check.

    Mixin must inherit from `object` at runtime so its MRO composes
    cooperatively with the concrete entity's `CoordinatorEntity` base.
    A stray runtime inheritance from `CoordinatorEntity` would collapse
    the MRO and can silently break `super()` chaining in
    `async_added_to_hass` / `_handle_coordinator_update`.
    """
    assert _BoundaryScheduleMixin.__bases__ == (object,)


def test_concrete_subclasses_place_mixin_before_coordinator_base() -> None:
    """
    MRO ordering for boundary mixin and coordinator.

    Every concrete subclass must list `_BoundaryScheduleMixin` before
    its `CoordinatorEntity` subclass so cooperative `super()` reaches
    the coordinator base. This is the MRO invariant documented in the
    mixin's docstring; without it, boundary timers do not re-arm.
    """
    for cls in (EngieBeHappyHourActiveSensor, EngieBeEpexNegativeSensor):
        mro = cls.__mro__
        mixin_idx = mro.index(_BoundaryScheduleMixin)
        # Skip index 0 (the class itself) when searching for a CoordinatorEntity
        # base, so the lookup finds the inherited base rather than the subclass.
        coord_idx = next(
            i
            for i, base in enumerate(mro)
            if i > 0 and issubclass(base, CoordinatorEntity)
        )
        assert mixin_idx < coord_idx, (
            f"{cls.__name__}: _BoundaryScheduleMixin must precede "
            f"CoordinatorEntity in MRO (got mixin@{mixin_idx}, "
            f"coordinator@{coord_idx})"
        )
