"""Structural multi-tenancy.

The rule this module enforces is: *every* ORM read that touches a tenant-scoped
table is filtered by the tenant bound to the current context, and a read issued
with no tenant bound is an error rather than a full-table scan.

That inverts the usual arrangement. Normally each endpoint remembers to add
``.where(Model.tenant_id == tenant_id)``, and the day somebody forgets is the
day one customer reads another customer's records. Here the filter is applied
by a SQLAlchemy ``do_orm_execute`` hook, so forgetting is not expressible: a
query written without the predicate still comes back scoped, and a query run
outside a tenant context raises :class:`MissingTenantContextError`.

Escaping the scope is deliberately awkward and greppable -- :func:`unscoped`
is used by the seeder and the benchmark script and nowhere else.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from sqlalchemy import event
from sqlalchemy.orm import Mapped, Session, mapped_column, with_loader_criteria

__all__ = [
    "MissingTenantContextError",
    "TenantScoped",
    "current_tenant_id",
    "install_tenant_guard",
    "tenant_scope",
    "unscoped",
]


class MissingTenantContextError(RuntimeError):
    """Raised when a tenant-scoped table is queried with no tenant bound.

    This is a programming error, not a client error: it means a code path
    reached the database without establishing who it was acting for.
    """


# None  -> no tenant bound (queries raise)
# int   -> scope every tenant-scoped read to this tenant
# _ANY  -> explicitly unscoped (seeding, benchmarks, cross-tenant maintenance)
_ANY = "__unscoped__"

_tenant_var: ContextVar[int | str | None] = ContextVar("current_tenant_id", default=None)


class TenantScoped:
    """Mixin marking a model as belonging to exactly one tenant.

    Inheriting from this is the *only* thing a model has to do to opt into
    automatic scoping; the guard discovers it by walking the mapper hierarchy.
    """

    tenant_id: Mapped[int] = mapped_column(nullable=False)


def current_tenant_id() -> int:
    """Return the tenant bound to this context, or raise if there is none."""
    value = _tenant_var.get()
    if value is None:
        raise MissingTenantContextError(
            "No tenant bound to the current context. Wrap the call in "
            "tenant_scope(tenant_id), or unscoped() if it is genuinely "
            "cross-tenant work."
        )
    if value is _ANY:
        raise MissingTenantContextError(
            "The current context is explicitly unscoped, so there is no single tenant to act for."
        )
    return int(value)


@contextmanager
def tenant_scope(tenant_id: int) -> Iterator[int]:
    """Bind ``tenant_id`` for the duration of the block."""
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int):
        raise TypeError(f"tenant_id must be an int, got {type(tenant_id).__name__}")
    token: Token[int | str | None] = _tenant_var.set(tenant_id)
    try:
        yield tenant_id
    finally:
        _tenant_var.reset(token)


@contextmanager
def unscoped() -> Iterator[None]:
    """Escape hatch for genuinely cross-tenant work (seeding, benchmarks).

    Deliberately named so that ``grep -rn unscoped app/`` lists every place the
    isolation guarantee is suspended.
    """
    token: Token[int | str | None] = _tenant_var.set(_ANY)
    try:
        yield
    finally:
        _tenant_var.reset(token)


def _touches_tenant_scoped(orm_execute_state: object) -> bool:
    mappers = getattr(orm_execute_state, "all_mappers", ())
    return any(issubclass(m.class_, TenantScoped) for m in mappers)


def install_tenant_guard() -> None:
    """Attach the scoping hook to every ORM ``Session``.

    Idempotent, so importing this module twice (or calling it from both the app
    factory and a test fixture) cannot double-register the listener.
    """
    if getattr(install_tenant_guard, "_installed", False):
        return

    @event.listens_for(Session, "do_orm_execute")
    def _apply_tenant_scope(orm_execute_state):  # pragma: no cover - thin hook
        if not orm_execute_state.is_select:
            return
        if not _touches_tenant_scoped(orm_execute_state):
            return

        value = _tenant_var.get()
        if value is _ANY:
            return
        if value is None:
            raise MissingTenantContextError(
                "Refusing to run an unscoped query against a tenant-scoped "
                "table. Wrap the call in tenant_scope(tenant_id)."
            )

        tenant_id = int(value)
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                TenantScoped,
                lambda cls: cls.tenant_id == tenant_id,
                include_aliases=True,
            )
        )

    install_tenant_guard._installed = True  # type: ignore[attr-defined]
